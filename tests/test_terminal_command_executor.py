from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai_ide.models import TerminalSession, UsageMetrics
from ai_ide.runner import RunnerResult
from ai_ide.terminal_command_executor import TerminalCommandExecutor


class _FakeRunnerManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def run_in_mode(
        self,
        mode: str,
        command: str,
        execution_session_id: str | None = None,
    ) -> RunnerResult:
        self.calls.append((mode, command, execution_session_id))
        return RunnerResult(
            mode=mode,
            backend=f"fake-{mode}",
            returncode=0,
            stdout=f"runner:{command}",
            stderr="",
            working_directory="/projection",
        )


class TerminalCommandExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metrics = UsageMetrics()
        self.persist_calls = 0
        self.published: list[tuple[str, str, dict[str, object]]] = []
        self.runner = _FakeRunnerManager()

    def _persist_state(self) -> None:
        self.persist_calls += 1

    def _publish_event(self, kind: str, session: TerminalSession, payload: dict[str, object]) -> None:
        self.published.append((kind, session.terminal_id, payload))

    def test_execute_runner_terminal_uses_runner_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = TerminalCommandExecutor(
                Path(temp_dir),
                self.metrics,
                self.runner,
                persist_state=self._persist_state,
                publish_event=self._publish_event,
            )
            session = TerminalSession(
                terminal_id="term-1",
                name="runner",
                created_at="2026-03-07T00:00:00Z",
                transport="runner",
                runner_mode="projected",
                status="active",
                stale_reason=None,
                execution_session_id="sess-1",
                bound_agent_run_id=None,
                command_history=[],
                inbox=[],
            )

            output = executor.execute(session, "echo hello")

        self.assertIn("[transport=runner mode=projected", output)
        self.assertEqual([("projected", "echo hello", "sess-1")], self.runner.calls)
        self.assertEqual(1, self.metrics.terminal_runs)
        self.assertEqual("terminal.command.completed", self.published[-1][0])

    def test_execute_blocked_terminal_increments_blocked_metric(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = TerminalCommandExecutor(
                Path(temp_dir),
                self.metrics,
                self.runner,
                persist_state=self._persist_state,
                publish_event=self._publish_event,
            )
            session = TerminalSession(
                terminal_id="term-2",
                name="stale",
                created_at="2026-03-07T00:00:00Z",
                transport="runner",
                runner_mode="projected",
                status="stale",
                stale_reason="terminal bound to stale execution session sess-old",
                execution_session_id="sess-old",
                bound_agent_run_id=None,
                command_history=[],
                inbox=[],
            )

            output = executor.execute(session, "echo hello")

        self.assertIn("blocked=true", output)
        self.assertEqual(1, self.metrics.terminal_runs)
        self.assertEqual(1, self.metrics.blocked_count)
        self.assertEqual([], self.runner.calls)
        self.assertEqual("terminal.command.blocked", self.published[-1][0])

    def test_execute_host_terminal_runs_shell_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = TerminalCommandExecutor(
                Path(temp_dir),
                self.metrics,
                None,
                persist_state=self._persist_state,
                publish_event=self._publish_event,
            )
            session = TerminalSession(
                terminal_id="term-3",
                name="host",
                created_at="2026-03-07T00:00:00Z",
                transport="host",
                runner_mode=None,
                status="active",
                stale_reason=None,
                execution_session_id=None,
                bound_agent_run_id=None,
                command_history=[],
                inbox=[],
            )

            output = executor.execute(session, "echo hello")

        self.assertIn("[transport=host mode=host", output)
        self.assertIn("unsafe=true", output)
        self.assertIn("hello", output.lower())
        self.assertEqual("terminal.command.completed", self.published[-1][0])

    def test_execute_host_terminal_uses_explicit_shell_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = TerminalCommandExecutor(
                Path(temp_dir),
                self.metrics,
                None,
                persist_state=self._persist_state,
                publish_event=self._publish_event,
            )
            session = TerminalSession(
                terminal_id="term-4",
                name="host",
                created_at="2026-03-07T00:00:00Z",
                transport="host",
                runner_mode=None,
                status="active",
                stale_reason=None,
                execution_session_id=None,
                bound_agent_run_id=None,
                command_history=[],
                inbox=[],
            )

            fake_result = Mock(returncode=0, stdout="ok\n", stderr="")
            with patch("ai_ide.terminal_command_executor.subprocess.run", return_value=fake_result) as mocked_run:
                output = executor.execute(session, "echo hello")

        argv = mocked_run.call_args.args[0]
        self.assertEqual("/d", argv[1])
        self.assertEqual("/s", argv[2])
        self.assertEqual("/c", argv[3])
        self.assertEqual("echo hello", argv[4])
        self.assertFalse(mocked_run.call_args.kwargs["shell"])
        self.assertIn("[transport=host mode=host", output)


if __name__ == "__main__":
    unittest.main()
