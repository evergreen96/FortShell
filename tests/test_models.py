from __future__ import annotations

import unittest

from ai_ide.models import (
    MAX_AGENT_RUN_STREAM_BYTES,
    MAX_TERMINAL_COMMAND_HISTORY,
    MAX_TERMINAL_INBOX_ENTRIES,
    AgentRunWatch,
    AgentRunRecord,
    TerminalEventWatch,
    TerminalSession,
)
from ai_ide.terminal_inbox import TerminalInboxEntry


class AgentRunRecordModelTests(unittest.TestCase):
    def _record(self) -> AgentRunRecord:
        return AgentRunRecord(
            run_id="run-1234",
            agent_session_id="agent-1234",
            execution_session_id="sess-1234",
            agent_kind="codex",
            runner_mode="projected",
            backend="projected",
            io_mode="pipe",
            transport_status="native",
            argv=["python", "-c", "print('hello')"],
            created_at="2026-03-13T00:00:00Z",
            ended_at=None,
            pid=1234,
            returncode=-1,
            status="running",
            stdout="",
            stderr="",
        )

    def test_finish_updates_terminal_fields(self) -> None:
        record = self._record()

        record.finish(status="completed", returncode=0, ended_at="2026-03-13T00:01:00Z")

        self.assertEqual("completed", record.status)
        self.assertEqual(0, record.returncode)
        self.assertEqual("2026-03-13T00:01:00Z", record.ended_at)

    def test_mark_stopped_appends_reason_to_stderr(self) -> None:
        record = self._record()

        record.mark_stopped(returncode=130, ended_at="2026-03-13T00:01:00Z", reason="manual stop")

        self.assertEqual("stopped", record.status)
        self.assertEqual(130, record.returncode)
        self.assertEqual("stopped: manual stop", record.stderr)

    def test_mark_interrupted_sets_status_and_appends_reason(self) -> None:
        record = self._record()

        record.mark_interrupted(
            ended_at="2026-03-13T00:01:00Z",
            reason="restored runtime lost live process handle",
        )

        self.assertEqual("interrupted", record.status)
        self.assertEqual(-2, record.returncode)
        self.assertIn("lost live process handle", record.stderr)

    def test_append_stdout_trims_old_output(self) -> None:
        record = self._record()

        record.append_stdout("a" * (MAX_AGENT_RUN_STREAM_BYTES + 5))

        self.assertEqual(MAX_AGENT_RUN_STREAM_BYTES, len(record.stdout))
        self.assertEqual("a" * MAX_AGENT_RUN_STREAM_BYTES, record.stdout)

    def test_append_stderr_line_trims_old_output(self) -> None:
        record = self._record()

        record.append_stderr("x" * MAX_AGENT_RUN_STREAM_BYTES)
        record.append_stderr_line("tail")

        self.assertEqual(MAX_AGENT_RUN_STREAM_BYTES, len(record.stderr))
        self.assertTrue(record.stderr.endswith("tail"))


class TerminalSessionModelTests(unittest.TestCase):
    def _session(self) -> TerminalSession:
        return TerminalSession(
            terminal_id="term-1234",
            name="terminal",
            created_at="2026-03-13T00:00:00Z",
            transport="runner",
            runner_mode="projected",
            status="active",
            stale_reason=None,
            execution_session_id="sess-1234",
            bound_agent_run_id=None,
            command_history=[],
            inbox=[],
        )

    def test_mark_stale_updates_status_and_reason(self) -> None:
        session = self._session()

        session.mark_stale("execution session became stale")

        self.assertEqual("stale", session.status)
        self.assertEqual("execution session became stale", session.stale_reason)

    def test_bind_and_unbind_agent_run_update_binding(self) -> None:
        session = self._session()

        session.bind_agent_run("run-1234")
        self.assertEqual("run-1234", session.bound_agent_run_id)

        session.unbind_agent_run()
        self.assertIsNone(session.bound_agent_run_id)

    def test_append_command_history_trims_old_entries(self) -> None:
        session = self._session()

        for index in range(MAX_TERMINAL_COMMAND_HISTORY + 2):
            session.append_command_history(f"cmd-{index}")

        self.assertEqual(MAX_TERMINAL_COMMAND_HISTORY, len(session.snapshot_command_history()))
        self.assertEqual("cmd-2", session.snapshot_command_history()[0])

    def test_append_inbox_trims_old_entries(self) -> None:
        session = self._session()

        for index in range(MAX_TERMINAL_INBOX_ENTRIES + 2):
            session.append_inbox(
                TerminalInboxEntry(
                    kind="terminal.message",
                    text=f"msg-{index}",
                    created_at=None,
                    payload={"index": index},
                )
            )

        inbox = session.snapshot_inbox()
        self.assertEqual(MAX_TERMINAL_INBOX_ENTRIES, len(inbox))
        self.assertEqual("msg-2", inbox[0].text)


class WatchModelTests(unittest.TestCase):
    def test_agent_run_watch_touch_updates_timestamp(self) -> None:
        watch = AgentRunWatch(
            watch_id="watch-1",
            run_id="run-1",
            consumer_id="consumer-1",
            created_at="2026-03-13T00:00:00Z",
            name="watch",
        )

        watch.touch("2026-03-13T00:01:00Z")

        self.assertEqual("2026-03-13T00:01:00Z", watch.updated_at)

    def test_terminal_event_watch_touch_updates_timestamp(self) -> None:
        watch = TerminalEventWatch(
            watch_id="watch-1",
            consumer_id="consumer-1",
            kind_prefix="agent.run",
            source_type="agent-run",
            source_id="run-1",
            created_at="2026-03-13T00:00:00Z",
        )

        watch.touch("2026-03-13T00:01:00Z")

        self.assertEqual("2026-03-13T00:01:00Z", watch.updated_at)
