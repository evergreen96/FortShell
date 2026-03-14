from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from backend.agent_supervisor import AgentRunSupervisor
from core.models import AgentRunRecord
from backend.runner import RunnerProcessHandle


class _FakeProcess:
    def __init__(self, pid: int = 1234, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class AgentRunSupervisorTests(unittest.TestCase):
    def test_poll_drains_stdout_and_marks_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stdout_path = root / "stdout.log"
            stderr_path = root / "stderr.log"
            stdout_path.write_text("hello\n", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            stdout_file = stdout_path.open("a", encoding="utf-8")
            stderr_file = stderr_path.open("a", encoding="utf-8")
            record = AgentRunRecord(
                run_id="run-1234",
                agent_session_id="agent-1234",
                execution_session_id="exec-1234",
                agent_kind="codex",
                runner_mode="projected",
                backend="projected",
                io_mode="pipe",
                transport_status="degraded",
                argv=["codex"],
                created_at="2026-03-07T00:00:00Z",
                ended_at=None,
                pid=1234,
                returncode=-1,
                status="running",
                stdout="",
                stderr="",
            )
            handle = RunnerProcessHandle(
                run_id="proc-1234",
                mode="projected",
                backend="projected",
                working_directory=str(root),
                process=_FakeProcess(returncode=0),
                stdin_file=io.StringIO(),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
            )
            events: list[tuple[str, str]] = []
            persisted: list[str] = []
            supervisor = AgentRunSupervisor()
            supervisor.attach(record, handle)

            updated = supervisor.poll(
                record.run_id,
                now=lambda: "2026-03-07T00:00:01Z",
                persist_state=lambda: persisted.append("saved"),
                publish_event=lambda kind, rec, payload: events.append((kind, rec.run_id)),
                status_for_returncode=lambda code: "completed" if code == 0 else "failed",
            )

            self.assertIsNotNone(updated)
            self.assertEqual("completed", updated.status)
            self.assertIn("hello", updated.stdout)
            self.assertFalse(supervisor.has(record.run_id))
            self.assertIn(("agent.run.stdout", "run-1234"), events)
            self.assertIn(("agent.run.completed", "run-1234"), events)
            self.assertEqual(["saved"], persisted)

    def test_send_input_writes_payload_and_publishes_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stdout_path = root / "stdout.log"
            stderr_path = root / "stderr.log"
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            stdout_file = stdout_path.open("a", encoding="utf-8")
            stderr_file = stderr_path.open("a", encoding="utf-8")
            stdin_file = io.StringIO()
            record = AgentRunRecord(
                run_id="run-5678",
                agent_session_id="agent-5678",
                execution_session_id="exec-5678",
                agent_kind="codex",
                runner_mode="projected",
                backend="projected",
                io_mode="pipe",
                transport_status="degraded",
                argv=["codex"],
                created_at="2026-03-07T00:00:00Z",
                ended_at=None,
                pid=5678,
                returncode=-1,
                status="running",
                stdout="",
                stderr="",
            )
            handle = RunnerProcessHandle(
                run_id="proc-5678",
                mode="projected",
                backend="projected",
                working_directory=str(root),
                process=_FakeProcess(returncode=None),
                stdin_file=stdin_file,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
            )
            events: list[str] = []
            supervisor = AgentRunSupervisor()
            supervisor.attach(record, handle)
            try:
                updated, payload = supervisor.send_input(
                    record.run_id,
                    "hello",
                    persist_state=lambda: events.append("saved"),
                    publish_event=lambda kind, rec, details: events.append(kind),
                )

                self.assertEqual(record.run_id, updated.run_id)
                self.assertEqual("hello\n", payload)
                self.assertEqual("hello\n", stdin_file.getvalue())
                self.assertIn("agent.run.stdin", events)
                self.assertIn("saved", events)
            finally:
                stdout_file.close()
                stderr_file.close()
                stdin_file.close()


if __name__ == "__main__":
    unittest.main()
