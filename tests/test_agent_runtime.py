from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent_runtime import AgentRuntimeManager
from backend.agents import AgentAdapter, AgentRegistry
from backend.events import EventBus
from backend.windows.platforms import get_platform_adapter
from core.policy import PolicyEngine
from backend.projection import ProjectedWorkspaceManager
from backend.runner import RunnerManager
from backend.session import SessionManager
from core.models import AgentRunWatch
from backend.windows.windows_strict_helper_resolution import WINDOWS_STRICT_HELPER_RUST_DEV


class AgentRuntimeManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.root = self.base / "project"
        self.runtime_root = self.base / "runtime"
        self.root.mkdir()
        (self.root / "safe").mkdir()
        (self.root / "safe" / "todo.txt").write_text("visible", encoding="utf-8")
        self.policy = PolicyEngine(self.root)
        self.sessions = SessionManager(self.policy)
        self.projection = ProjectedWorkspaceManager(self.root, self.policy, self.runtime_root)
        self.runners = RunnerManager(self.root, self.projection, self.sessions, get_platform_adapter("Windows"))
        self.registry = AgentRegistry()
        self.events = EventBus()
        self.runtime = AgentRuntimeManager(self.registry, self.runners, self.sessions, event_bus=self.events)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_execute_current_marks_unavailable_when_adapter_launcher_is_missing(self) -> None:
        self.sessions.rotate_agent_session("codex")
        with patch("backend.agents.shutil.which", return_value=None):
            execution = self.runtime.execute_current()

        self.assertEqual("unavailable", execution.record.status)
        self.assertEqual(127, execution.record.returncode)
        self.assertIn("unavailable:", execution.record.stderr)

    def test_execute_current_passes_session_metadata_to_runner(self) -> None:
        self.sessions.rotate_agent_session("codex")

        with patch("backend.agents.shutil.which", return_value="/tmp/codex"):
            with patch.object(self.runners, "run_process_in_mode") as run_process:
                run_process.return_value = type(
                    "RunnerResult",
                    (),
                    {
                        "mode": "projected",
                        "backend": "projected",
                        "returncode": 0,
                        "stdout": "ok\n",
                        "stderr": "",
                        "working_directory": str(self.root),
                    },
                )()

                execution = self.runtime.execute_current(["--version"])

        self.assertEqual("completed", execution.record.status)
        self.assertEqual(["/tmp/codex", "--version"], execution.record.argv)
        _, argv = run_process.call_args.args[:2]
        env = run_process.call_args.kwargs["env"]
        self.assertEqual(["/tmp/codex", "--version"], argv)
        self.assertEqual(self.sessions.current_agent_session_id, env["AI_IDE_AGENT_SESSION_ID"])
        self.assertEqual(self.sessions.current_session_id, env["AI_IDE_EXECUTION_SESSION_ID"])
        self.assertEqual("codex", env["AI_IDE_AGENT_KIND"])
        self.assertEqual("pipe", env["AI_IDE_AGENT_IO_MODE"])
        self.assertEqual("degraded", env["AI_IDE_AGENT_TRANSPORT_STATUS"])

    def test_execute_current_marks_transport_unavailable_when_adapter_requires_pty(self) -> None:
        registry = AgentRegistry(
            [
                AgentAdapter("pty-only", "PTY Only", ("pty-only",), io_mode_preference="pty_required"),
            ]
        )
        runtime = AgentRuntimeManager(registry, self.runners, self.sessions, event_bus=self.events)
        self.sessions.rotate_agent_session("pty-only")

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            execution = runtime.execute_current(["--version"])

        self.assertEqual("unavailable", execution.record.status)
        self.assertEqual("none", execution.record.io_mode)
        self.assertEqual("unavailable", execution.record.transport_status)
        self.assertIn("unavailable transport", execution.record.stderr)

    def test_describe_transport_reports_runtime_fallback_for_pty_preferred_adapter(self) -> None:
        self.sessions.rotate_agent_session("codex")

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            transport = self.runtime.describe_transport("codex", mode="strict")

        self.assertEqual("codex", transport.agent_kind)
        self.assertEqual("strict", transport.runner_mode)
        self.assertTrue(transport.adapter_available)
        self.assertEqual("pty_preferred", transport.requested_io_mode)
        self.assertEqual("pipe", transport.resolved_io_mode)
        self.assertEqual("degraded", transport.transport_status)
        self.assertTrue(transport.launchable)

    def test_start_current_and_poll_complete_streaming_run(self) -> None:
        self.sessions.rotate_agent_session("codex")

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            record = self.runtime.start_current(
                [
                    "-u",
                    "-c",
                    "import time; print('stream-start', flush=True); time.sleep(0.2); print('stream-end', flush=True)",
                ]
            )

        self.assertEqual("running", record.status)
        self.assertIsNotNone(record.pid)
        time.sleep(0.35)
        polled = self.runtime.poll_run(record.run_id)

        self.assertEqual("completed", polled.status)
        self.assertIn("stream-start", polled.stdout)
        self.assertIn("stream-end", polled.stdout)
        self.assertIn(
            "agent.run.started",
            [event.kind for event in self.events.list_events()],
        )
        self.assertIn(
            "agent.run.stdout",
            [event.kind for event in self.events.list_events()],
        )
        self.assertIn(
            "agent.run.completed",
            [event.kind for event in self.events.list_events()],
        )

    def test_stop_run_marks_streaming_record_stopped(self) -> None:
        self.sessions.rotate_agent_session("codex")

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            record = self.runtime.start_current(
                [
                    "-u",
                    "-c",
                    "import time; print('stream-live', flush=True); time.sleep(5)",
                ]
            )

        stopped = self.runtime.stop_run(record.run_id, reason="requested by test")

        self.assertEqual("stopped", stopped.status)
        self.assertIn("requested by test", stopped.stderr)

    def test_send_input_delivers_text_to_active_process(self) -> None:
        self.sessions.rotate_agent_session("codex")

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            record = self.runtime.start_current(
                [
                    "-u",
                    "-c",
                    "import sys, time; line = sys.stdin.readline().strip(); print(f'received:{line}', flush=True); time.sleep(0.2)",
                ]
            )

        self.runtime.send_input(record.run_id, "hello-agent")
        time.sleep(0.3)
        updated = self.runtime.poll_run(record.run_id)

        self.assertEqual("completed", updated.status)
        self.assertIn("received:hello-agent", updated.stdout)
        self.assertIn("agent.run.stdin", [event.kind for event in self.events.list_events()])

    def test_start_current_in_strict_mode_can_stream_through_windows_helper_stub(self) -> None:
        self.sessions.rotate_agent_session("codex")
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"
        helper_command = f"{sys.executable} {helper_script}"

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
            with patch("backend.agents.shutil.which", return_value=sys.executable):
                record = self.runtime.start_current(
                    [
                        "-u",
                        "-c",
                        (
                            "import sys, time; "
                            "print('strict-helper-start', flush=True); "
                            "line=sys.stdin.readline().strip(); "
                            "print(f'strict-helper:{line}', flush=True); "
                            "time.sleep(0.2)"
                        ),
                    ],
                    mode="strict",
                )

        self.assertEqual("running", record.status)
        self.assertEqual("restricted-host-helper", record.backend)
        self.runtime.send_input(record.run_id, "hello-strict")
        deadline = time.time() + 2.0
        updated = self.runtime.poll_run(record.run_id)
        while updated.status == "running" and time.time() < deadline:
            time.sleep(0.1)
            updated = self.runtime.poll_run(record.run_id)
        if updated.status == "running":
            self.runtime.stop_run(record.run_id, reason="strict helper test cleanup")
            self.fail("strict helper run did not complete within timeout")

        self.assertEqual("completed", updated.status)
        self.assertIn("strict-helper-start", updated.stdout)
        self.assertIn("strict-helper:hello-strict", updated.stdout)
        self.assertIn("agent.run.stdin", [event.kind for event in self.events.list_events()])

    @unittest.skipUnless(shutil.which("cargo"), "cargo required for rust-dev helper tests")
    def test_execute_current_in_strict_mode_can_use_rust_dev_helper(self) -> None:
        self.sessions.rotate_agent_session("codex")
        real_which = shutil.which

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": WINDOWS_STRICT_HELPER_RUST_DEV}):
            with patch(
                "backend.agents.shutil.which",
                side_effect=lambda name: sys.executable if name == "codex" else real_which(name),
            ):
                execution = self.runtime.execute_current(
                    [
                        "-c",
                        "print('strict-rust-helper-exec')",
                    ],
                    mode="strict",
                )

        self.assertEqual("completed", execution.record.status)
        self.assertEqual("restricted-host-helper", execution.record.backend)
        self.assertIn("strict-rust-helper-exec", execution.result.stdout)

    def test_stop_run_in_strict_mode_closes_helper_stdin_before_forceful_terminate(self) -> None:
        self.sessions.rotate_agent_session("codex")
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"
        helper_command = f"{sys.executable} {helper_script}"

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
            with patch("backend.agents.shutil.which", return_value=sys.executable):
                record = self.runtime.start_current(
                    [
                        "-u",
                        "-c",
                        (
                            "import sys, time; "
                            "line=sys.stdin.readline(); "
                            "print('strict-helper-eof', flush=True) if line == '' else None; "
                            "time.sleep(0.2)"
                        ),
                    ],
                    mode="strict",
                )

        stopped = self.runtime.stop_run(record.run_id, reason="strict helper shutdown")

        self.assertEqual("stopped", stopped.status)
        self.assertEqual("restricted-host-helper", stopped.backend)
        self.assertIn("strict-helper-eof", stopped.stdout)
        self.assertIn("strict helper shutdown", stopped.stderr)

    def test_inspect_run_uses_helper_status_channel_for_active_strict_helper_run(self) -> None:
        self.sessions.rotate_agent_session("codex")
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"
        helper_command = f"{sys.executable} {helper_script}"

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
            with patch("backend.agents.shutil.which", return_value=sys.executable):
                record = self.runtime.start_current(
                    [
                        "-u",
                        "-c",
                        "import time; print('strict-helper-live', flush=True); time.sleep(5)",
                    ],
                    mode="strict",
                )

        try:
            time.sleep(0.2)
            inspection = self.runtime.inspect_run(record.run_id)
        finally:
            self.runtime.stop_run(record.run_id, reason="strict helper inspect cleanup")

        self.assertEqual("running", inspection.record.status)
        self.assertEqual("helper-control", inspection.process.source)
        self.assertEqual("running", inspection.process.state)
        self.assertEqual("restricted-host-helper", inspection.process.backend)
        self.assertIsInstance(inspection.process.pid, int)

    def test_list_run_inspections_uses_helper_status_for_active_strict_helper_run(self) -> None:
        self.sessions.rotate_agent_session("codex")
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"
        helper_command = f"{sys.executable} {helper_script}"

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
            with patch("backend.agents.shutil.which", return_value=sys.executable):
                record = self.runtime.start_current(
                    [
                        "-u",
                        "-c",
                        "import time; print('strict-helper-history', flush=True); time.sleep(5)",
                    ],
                    mode="strict",
                )

        try:
            time.sleep(0.2)
            inspections = self.runtime.list_run_inspections(self.sessions.current_session_id)
        finally:
            self.runtime.stop_run(record.run_id, reason="strict helper history cleanup")

        inspection = next(item for item in inspections if item.record.run_id == record.run_id)
        self.assertEqual("helper-control", inspection.process.source)
        self.assertEqual("running", inspection.process.state)
        self.assertEqual("restricted-host-helper", inspection.process.backend)

    def test_mark_execution_session_stale_stops_active_runs(self) -> None:
        self.sessions.rotate_agent_session("codex")
        execution_session_id = self.sessions.current_session_id

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            record = self.runtime.start_current(
                [
                    "-u",
                    "-c",
                    "import time; print('stream-live', flush=True); time.sleep(5)",
                ]
            )

        self.runtime.mark_execution_session_stale(execution_session_id)
        updated = self.runtime.poll_run(record.run_id)

        self.assertEqual("stopped", updated.status)
        self.assertIn("became stale", updated.stderr)

    def test_watch_run_pulls_unseen_events_with_persisted_cursor(self) -> None:
        self.sessions.rotate_agent_session("codex")

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            record = self.runtime.start_current(
                [
                    "-u",
                    "-c",
                    "import time; print('watch-start', flush=True); time.sleep(0.2); print('watch-end', flush=True)",
                ]
            )

        watch = self.runtime.watch_run(record.run_id, name="watcher")
        time.sleep(0.35)
        self.runtime.poll_run(record.run_id)
        inbox = self.runtime.pull_watch(watch.watch_id)

        self.assertEqual("watcher", watch.name)
        self.assertIn("agent.run.stdout", [event.kind for event in inbox])
        self.assertEqual(
            inbox[-1].event_id,
            self.events.get_cursor(watch.consumer_id),
        )

    def test_pull_watch_refreshes_active_run_without_explicit_poll(self) -> None:
        self.sessions.rotate_agent_session("codex")

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            record = self.runtime.start_current(
                [
                    "-u",
                    "-c",
                    "import time; print('auto-watch', flush=True); time.sleep(0.2); print('auto-done', flush=True)",
                ]
            )

        watch = self.runtime.watch_run(record.run_id, name="auto")
        time.sleep(0.35)
        inbox = self.runtime.pull_watch(watch.watch_id)
        updated = self.runtime.get_run(record.run_id)

        self.assertIn("agent.run.stdout", [event.kind for event in inbox])
        self.assertIn("auto-done", updated.stdout)
        self.assertEqual("completed", updated.status)

    def test_watch_run_can_replay_existing_events(self) -> None:
        self.sessions.rotate_agent_session("codex")

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            execution = self.runtime.execute_current(
                ["-c", "print('replay-agent')"]
            )

        watch = self.runtime.watch_run(execution.record.run_id, replay=True)
        inbox = self.runtime.pull_watch(watch.watch_id)

        self.assertIn("agent.run.completed", [event.kind for event in inbox])

    def test_watch_state_restores_across_restart(self) -> None:
        self.sessions.rotate_agent_session("codex")
        state_path = self.runtime_root / "agents" / "state.json"
        runtime = AgentRuntimeManager(
            self.registry,
            self.runners,
            self.sessions,
            event_bus=self.events,
            state_path=state_path,
        )

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            execution = runtime.execute_current(["-c", "print('persist-watch')"])

        watch = runtime.watch_run(execution.record.run_id, replay=True)
        reloaded_runtime = AgentRuntimeManager(
            self.registry,
            self.runners,
            self.sessions,
            event_bus=self.events,
            state_path=state_path,
        )
        inbox = reloaded_runtime.pull_watch(watch.watch_id)

        self.assertEqual(watch.run_id, reloaded_runtime.get_watch(watch.watch_id).run_id)
        self.assertIn("agent.run.completed", [event.kind for event in inbox])

    def test_load_state_drops_orphaned_watches(self) -> None:
        self.sessions.rotate_agent_session("codex")
        state_path = self.runtime_root / "agents" / "state.json"
        runtime = AgentRuntimeManager(
            self.registry,
            self.runners,
            self.sessions,
            event_bus=self.events,
            state_path=state_path,
        )

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            execution = runtime.execute_current(["-c", "print('persist-watch')"])

        watch = runtime.watch_run(execution.record.run_id, replay=True)
        snapshot = runtime.state_store.load()
        snapshot_watches = dict(snapshot[1])
        snapshot_watches["agent-watch-orphan"] = AgentRunWatch(
            watch_id="agent-watch-orphan",
            run_id="run-missing",
            consumer_id="agent-run:run-missing:watch:agent-watch-orphan",
            created_at="2026-03-13T00:00:00Z",
            updated_at="2026-03-13T00:00:00Z",
            name="orphan",
        )
        runtime.state_store.save(snapshot[0], snapshot_watches)

        reloaded_runtime = AgentRuntimeManager(
            self.registry,
            self.runners,
            self.sessions,
            event_bus=self.events,
            state_path=state_path,
        )

        self.assertEqual(watch.run_id, reloaded_runtime.get_watch(watch.watch_id).run_id)
        self.assertNotIn("agent-watch-orphan", reloaded_runtime.watch_manager.run_watches)

    def test_completed_run_history_restores_across_restart(self) -> None:
        self.sessions.rotate_agent_session("codex")
        state_path = self.runtime_root / "agents" / "state.json"
        runtime = AgentRuntimeManager(
            self.registry,
            self.runners,
            self.sessions,
            event_bus=self.events,
            state_path=state_path,
        )

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            execution = runtime.execute_current(["-c", "print('persisted-history')"])

        reloaded_runtime = AgentRuntimeManager(
            self.registry,
            self.runners,
            self.sessions,
            event_bus=self.events,
            state_path=state_path,
        )
        restored = reloaded_runtime.get_run(execution.record.run_id)

        self.assertEqual("completed", restored.status)
        self.assertIn("persisted-history", restored.stdout)

    def test_running_record_restores_as_interrupted(self) -> None:
        self.sessions.rotate_agent_session("codex")
        state_path = self.runtime_root / "agents" / "state.json"
        runtime = AgentRuntimeManager(
            self.registry,
            self.runners,
            self.sessions,
            event_bus=self.events,
            state_path=state_path,
        )

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            record = runtime.start_current(
                [
                    "-u",
                    "-c",
                    "import time; print('still-running', flush=True); time.sleep(5)",
                ]
            )

        reloaded_runtime = AgentRuntimeManager(
            self.registry,
            self.runners,
            self.sessions,
            event_bus=self.events,
            state_path=state_path,
        )
        restored = reloaded_runtime.get_run(record.run_id)
        runtime.stop_run(record.run_id, reason="test cleanup")

        self.assertEqual("interrupted", restored.status)
        self.assertIn("lost live process handle", restored.stderr)

    def test_cleanup_stale_watches_removes_old_watch_definition_and_cursor(self) -> None:
        self.sessions.rotate_agent_session("codex")

        with patch("backend.agents.shutil.which", return_value=sys.executable):
            execution = self.runtime.execute_current(["-c", "print('cleanup-watch')"])

        watch = self.runtime.watch_run(execution.record.run_id, name="stale-watch")
        watch.updated_at = "2020-01-01T00:00:00Z"
        removed = self.runtime.cleanup_stale_watches(60, now="2026-03-07T00:05:00Z")

        self.assertEqual(1, removed)
        self.assertEqual([], self.runtime.list_watches())
        self.assertIsNone(self.events.get_cursor(watch.consumer_id))


if __name__ == "__main__":
    unittest.main()
