from __future__ import annotations

import unittest

from backend.events import EventBus
from core.models import TerminalEventWatch, TerminalSession
from backend.terminal_watch_manager import MAX_TERMINAL_EVENT_WATCHES, TerminalWatchManager


class _FakeAgentRuntime:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.refresh_calls: list[str | None] = []

    def get_run(self, run_id: str):
        return type(
            "Run",
            (),
            {
                "run_id": run_id,
                "execution_session_id": "sess-left",
                "agent_kind": "codex",
            },
        )()

    def send_input(self, run_id: str, text: str):
        self.sent.append((run_id, text))
        return type("Run", (), {"run_id": run_id})()

    def refresh_active_runs(self, execution_session_id: str | None = None) -> None:
        self.refresh_calls.append(execution_session_id)


class TerminalWatchManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = EventBus()
        self.agent_runtime = _FakeAgentRuntime()
        self.persist_calls = 0
        self.published: list[tuple[str, str, dict[str, object]]] = []
        self.manager = TerminalWatchManager(
            self.events,
            self.agent_runtime,
            now=lambda: "2026-03-07T00:00:00Z",
            persist_state=self._persist_state,
            publish_event=self._publish_event,
        )
        self.terminal = TerminalSession(
            terminal_id="term-1234",
            name="bridge",
            created_at="2026-03-07T00:00:00Z",
            transport="runner",
            runner_mode="projected",
            status="active",
            stale_reason=None,
            execution_session_id="sess-left",
            bound_agent_run_id=None,
            command_history=[],
            inbox=[],
        )

    def _persist_state(self) -> None:
        self.persist_calls += 1

    def _publish_event(self, kind: str, session: TerminalSession, payload: dict[str, object]) -> None:
        self.published.append((kind, session.terminal_id, payload))

    def test_attach_to_agent_run_creates_bridge_watch_and_event(self) -> None:
        self.manager.attach_to_agent_run(self.terminal, "run-1")

        self.assertEqual("run-1", self.terminal.bound_agent_run_id)
        self.assertIn(self.terminal.terminal_id, self.manager.bridge_watches)
        self.assertEqual(1, len(self.manager.event_watches[self.terminal.terminal_id]))
        self.assertEqual("terminal.agent_bridge.attached", self.published[-1][0])

    def test_sync_terminal_inbox_pulls_filtered_events_and_refreshes_agent_runs(self) -> None:
        self.manager.subscribe_to_events(
            self.terminal,
            kind_prefix="agent.run",
            source_type="agent-run",
            source_id="run-1",
        )
        self.events.publish("agent.run.started", source_type="agent-run", source_id="run-1", payload={"ok": True})
        self.events.publish("agent.run.started", source_type="agent-run", source_id="run-2", payload={"ok": False})

        self.manager.sync_terminal_inbox(self.terminal)

        self.assertEqual(["sess-left"], self.agent_runtime.refresh_calls)
        self.assertEqual(1, len(self.terminal.inbox))
        self.assertIn("run-1", self.terminal.inbox[0].text)
        self.assertNotIn("run-2", self.terminal.inbox[0].text)
        self.assertEqual("runtime-event", self.terminal.inbox[0].kind)

    def test_cleanup_stale_watches_clears_bridge_and_cursor(self) -> None:
        self.manager.attach_to_agent_run(self.terminal, "run-1")
        watch_id = self.manager.bridge_watches[self.terminal.terminal_id]
        watch = self.manager.event_watches[self.terminal.terminal_id][0]
        watch.updated_at = "2020-01-01T00:00:00Z"

        removed = self.manager.cleanup_stale_watches(
            {self.terminal.terminal_id: self.terminal},
            60,
            now="2026-03-07T00:05:00Z",
        )

        self.assertEqual(1, removed)
        self.assertNotIn(self.terminal.terminal_id, self.manager.bridge_watches)
        self.assertNotIn(self.terminal.terminal_id, self.manager.event_watches)
        self.assertIsNone(self.terminal.bound_agent_run_id)
        self.assertIsNone(self.events.get_cursor(f"terminal:{self.terminal.terminal_id}:watch:{watch_id}"))

    def test_subscribe_to_events_rejects_when_terminal_watch_limit_exceeded(self) -> None:
        self.manager.event_watches[self.terminal.terminal_id] = [
            TerminalEventWatch(
                watch_id=f"sub-{index:06d}",
                consumer_id=f"terminal:{self.terminal.terminal_id}:watch:{index}",
                kind_prefix=None,
                source_type=None,
                source_id=None,
                created_at="2026-03-07T00:00:00Z",
                updated_at="2026-03-07T00:00:00Z",
            )
            for index in range(MAX_TERMINAL_EVENT_WATCHES)
        ]

        with self.assertRaises(ValueError):
            self.manager.subscribe_to_events(self.terminal)

    def test_replace_state_trims_oversized_watches(self) -> None:
        watches = {
            self.terminal.terminal_id: [
                TerminalEventWatch(
                    watch_id=f"sub-{index:06d}",
                    consumer_id=f"terminal:{self.terminal.terminal_id}:watch:{index}",
                    kind_prefix=None,
                    source_type=None,
                    source_id=None,
                    created_at="2026-03-07T00:00:00Z",
                    updated_at=f"2026-03-07T00:00:{index:02d}Z",
                )
                for index in range(MAX_TERMINAL_EVENT_WATCHES + 2)
            ]
        }

        self.manager.replace_state(watches, {})

        self.assertEqual(
            MAX_TERMINAL_EVENT_WATCHES,
            len(self.manager.event_watches[self.terminal.terminal_id]),
        )


if __name__ == "__main__":
    unittest.main()
