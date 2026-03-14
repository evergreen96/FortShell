from __future__ import annotations

import unittest

from backend.agent_watch_manager import MAX_AGENT_RUN_WATCHES, AgentRunWatchManager
from backend.events import EventBus
from core.models import AgentRunWatch


class AgentRunWatchManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = EventBus()
        self.persisted: list[str] = []
        self.refreshed: list[str] = []
        self.manager = AgentRunWatchManager(
            self.events,
            now=lambda: "2026-03-07T00:00:00Z",
            persist_state=lambda: self.persisted.append("saved"),
            refresh_active_runs=lambda: self.refreshed.append("refreshed"),
            ensure_run_exists=lambda run_id: None,
        )

    def test_watch_run_without_replay_starts_after_latest_seen_event(self) -> None:
        self.events.publish(
            "agent.run.completed",
            source_type="agent-run",
            source_id="run-1234",
            execution_session_id="exec-1234",
            payload={"status": "completed"},
        )

        watch = self.manager.watch_run("run-1234", name="observer", replay=False)
        inbox = self.manager.pull_watch(watch.watch_id)

        self.assertEqual("observer", watch.name)
        self.assertEqual([], inbox)
        self.assertEqual(["saved", "saved"], self.persisted)
        self.assertEqual(["refreshed"], self.refreshed)

    def test_cleanup_stale_watches_clears_cursor(self) -> None:
        watch = self.manager.watch_run("run-5678", replay=True)
        self.events.publish(
            "agent.run.stdout",
            source_type="agent-run",
            source_id="run-5678",
            execution_session_id="exec-5678",
            payload={"chunk": "hello"},
        )
        self.manager.pull_watch(watch.watch_id)
        watch.updated_at = "2020-01-01T00:00:00Z"

        removed = self.manager.cleanup_stale_watches(60, now="2026-03-07T00:05:00Z")

        self.assertEqual(1, removed)
        self.assertEqual([], self.manager.list_watches())
        self.assertIsNone(self.events.get_cursor(watch.consumer_id))

    def test_watch_run_rejects_when_watch_limit_reached(self) -> None:
        for index in range(MAX_AGENT_RUN_WATCHES):
            self.manager.watch_run(f"run-{index}", replay=True)

        with self.assertRaisesRegex(ValueError, "Too many agent watches"):
            self.manager.watch_run("run-overflow", replay=True)

    def test_replace_state_trims_loaded_watches_to_limit(self) -> None:
        watches = {
            f"watch-{index}": self.manager.watch_run(f"run-{index}", replay=True)
            for index in range(MAX_AGENT_RUN_WATCHES)
        }
        extra = AgentRunWatch(
            watch_id="watch-extra",
            run_id="run-extra",
            consumer_id="consumer-extra",
            created_at="2026-03-07T00:00:00Z",
            name="extra",
            updated_at="2026-03-07T01:00:00Z",
        )
        watches[extra.watch_id] = extra

        self.manager.replace_state(watches)

        self.assertEqual(MAX_AGENT_RUN_WATCHES, len(self.manager.run_watches))
        self.assertIn(extra.watch_id, self.manager.run_watches)


if __name__ == "__main__":
    unittest.main()
