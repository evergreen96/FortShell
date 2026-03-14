from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.events import EventBus


class EventBusTests(unittest.TestCase):
    def test_publish_assigns_stable_incrementing_event_ids(self) -> None:
        bus = EventBus()

        first = bus.publish("agent.run.started", source_type="agent-run", source_id="run-1")
        second = bus.publish("agent.run.completed", source_type="agent-run", source_id="run-1")

        self.assertEqual("evt-000001", first.event_id)
        self.assertEqual("evt-000002", second.event_id)

    def test_list_events_supports_limit_and_after_event_id(self) -> None:
        bus = EventBus()
        first = bus.publish("one", source_type="test", source_id="a")
        bus.publish("two", source_type="test", source_id="b")
        bus.publish("three", source_type="test", source_id="c")

        after_first = bus.list_events(after_event_id=first.event_id)
        latest_one = bus.list_events(limit=1)

        self.assertEqual(["two", "three"], [event.kind for event in after_first])
        self.assertEqual(["three"], [event.kind for event in latest_one])

    def test_list_events_supports_source_filters(self) -> None:
        bus = EventBus()
        bus.publish("agent.run.started", source_type="agent-run", source_id="run-1")
        bus.publish("agent.run.completed", source_type="agent-run", source_id="run-2")
        bus.publish("terminal.message.sent", source_type="terminal", source_id="term-1")

        filtered = bus.list_events(source_type="agent-run", source_id="run-1")

        self.assertEqual(["agent.run.started"], [event.kind for event in filtered])

    def test_subscribe_filters_by_kind_prefix_and_source(self) -> None:
        bus = EventBus()
        seen: list[str] = []

        bus.subscribe(
            lambda event: seen.append(event.kind),
            kind_prefix="agent.run",
            source_type="agent-run",
            source_id="run-1",
        )
        bus.publish("agent.run.started", source_type="agent-run", source_id="run-1")
        bus.publish("agent.run.started", source_type="agent-run", source_id="run-2")
        bus.publish("terminal.message.sent", source_type="terminal", source_id="term-1")

        self.assertEqual(["agent.run.started"], seen)

    def test_subscribe_rejects_when_subscription_limit_is_exceeded(self) -> None:
        bus = EventBus()

        for _ in range(EventBus.MAX_SUBSCRIPTIONS):
            bus.subscribe(lambda event: None)

        with self.assertRaisesRegex(ValueError, "too many subscriptions"):
            bus.subscribe(lambda event: None)

    def test_set_cursor_rejects_when_cursor_limit_is_exceeded(self) -> None:
        bus = EventBus()
        event = bus.publish("one", source_type="test", source_id="a")

        for index in range(EventBus.MAX_CURSORS):
            bus.set_cursor(f"consumer-{index}", event.event_id)

        with self.assertRaisesRegex(ValueError, "too many cursors"):
            bus.set_cursor("consumer-overflow", event.event_id)

    def test_load_cursors_trims_to_cursor_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "runtime" / "events"
            log_path = base / "events.jsonl"
            cursor_path = base / "cursors.json"
            bus = EventBus(log_path=log_path, cursor_path=cursor_path)
            event = bus.publish("one", source_type="test", source_id="a")

            payload = {
                f"consumer-{index}": {
                    "event_id": event.event_id,
                    "updated_at": f"2026-03-13T00:{index % 60:02d}:00Z",
                }
                for index in range(EventBus.MAX_CURSORS + 2)
            }
            cursor_path.parent.mkdir(parents=True, exist_ok=True)
            cursor_path.write_text(json.dumps(payload), encoding="utf-8")

            reloaded = EventBus(log_path=log_path, cursor_path=cursor_path)

            self.assertEqual(EventBus.MAX_CURSORS, len(reloaded._cursors))
            self.assertNotIn("consumer-0", reloaded._cursors)
            self.assertIn(f"consumer-{EventBus.MAX_CURSORS + 1}", reloaded._cursors)

    def test_persisted_log_reloads_existing_events_and_continues_counter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "runtime" / "events" / "events.jsonl"
            first_bus = EventBus(log_path=log_path)

            first = first_bus.publish("one", source_type="test", source_id="a")
            second = first_bus.publish("two", source_type="test", source_id="b")

            reloaded_bus = EventBus(log_path=log_path)
            third = reloaded_bus.publish("three", source_type="test", source_id="c")

            self.assertEqual(["evt-000001", "evt-000002"], [first.event_id, second.event_id])
            self.assertEqual(
                ["evt-000001", "evt-000002", "evt-000003"],
                [event.event_id for event in reloaded_bus.list_events()],
            )
            self.assertEqual("evt-000003", third.event_id)
            self.assertEqual(3, len(log_path.read_text(encoding="utf-8").splitlines()))

    def test_list_events_reuses_cached_log_when_file_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "runtime" / "events" / "events.jsonl"
            bus = EventBus(log_path=log_path)
            bus.publish("one", source_type="test", source_id="a")

            with patch("ai_ide.events.RuntimeEvent.from_dict", side_effect=AssertionError("should not reload")):
                events = bus.list_events()

            self.assertEqual(["one"], [event.kind for event in events])

    def test_pull_events_persists_consumer_cursor_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "runtime" / "events"
            log_path = base / "events.jsonl"
            cursor_path = base / "cursors.json"
            first_bus = EventBus(log_path=log_path, cursor_path=cursor_path)

            first_bus.publish("one", source_type="test", source_id="a")
            first_bus.publish("two", source_type="test", source_id="b")
            pulled = first_bus.pull_events("ui-main", limit=1)

            self.assertEqual(["one"], [event.kind for event in pulled])
            self.assertEqual("evt-000001", first_bus.get_cursor("ui-main"))

            reloaded_bus = EventBus(log_path=log_path, cursor_path=cursor_path)
            third = reloaded_bus.publish("three", source_type="test", source_id="c")
            resumed = reloaded_bus.pull_events("ui-main")

            self.assertEqual("evt-000003", third.event_id)
            self.assertEqual(["two", "three"], [event.kind for event in resumed])
            self.assertEqual("evt-000003", reloaded_bus.get_cursor("ui-main"))

    def test_pull_events_filters_by_source_and_advances_past_nonmatches(self) -> None:
        bus = EventBus()
        first = bus.publish("agent.run.stdout", source_type="agent-run", source_id="run-1")
        bus.publish("terminal.message.sent", source_type="terminal", source_id="term-1")
        third = bus.publish("agent.run.completed", source_type="agent-run", source_id="run-1")
        bus.publish("agent.run.started", source_type="agent-run", source_id="run-2")

        pulled = bus.pull_events(
            "agent-watch",
            source_type="agent-run",
            source_id="run-1",
            kind_prefix="agent.run",
        )
        self.assertEqual(third.event_id, bus.get_cursor("agent-watch"))
        repeated = bus.pull_events(
            "agent-watch",
            source_type="agent-run",
            source_id="run-1",
            kind_prefix="agent.run",
        )

        self.assertEqual(["agent.run.stdout", "agent.run.completed"], [event.kind for event in pulled])
        self.assertEqual([], repeated)
        self.assertEqual("evt-000004", bus.get_cursor("agent-watch"))
        self.assertEqual(first.event_id, pulled[0].event_id)

    def test_load_cursors_supports_legacy_string_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "runtime" / "events"
            log_path = base / "events.jsonl"
            cursor_path = base / "cursors.json"
            bus = EventBus(log_path=log_path, cursor_path=cursor_path)
            first = bus.publish("one", source_type="test", source_id="a")
            bus.publish("two", source_type="test", source_id="b")
            cursor_path.parent.mkdir(parents=True, exist_ok=True)
            cursor_path.write_text('{"legacy-ui":"evt-000001"}', encoding="utf-8")

            reloaded = EventBus(log_path=log_path, cursor_path=cursor_path)

            self.assertEqual(first.event_id, reloaded.get_cursor("legacy-ui"))
            self.assertEqual(first.timestamp, reloaded.get_cursor_updated_at("legacy-ui"))

    def test_get_cursor_reuses_cached_cursor_store_when_file_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "runtime" / "events"
            log_path = base / "events.jsonl"
            cursor_path = base / "cursors.json"
            bus = EventBus(log_path=log_path, cursor_path=cursor_path)
            first = bus.publish("one", source_type="test", source_id="a")
            bus.set_cursor("ui-main", first.event_id)

            with patch("json.loads", side_effect=AssertionError("should not reload cursor store")):
                cursor = bus.get_cursor("ui-main")

            self.assertEqual(first.event_id, cursor)

    def test_multi_instance_publish_stays_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "runtime" / "events" / "events.jsonl"
            first_bus = EventBus(log_path=log_path)
            second_bus = EventBus(log_path=log_path)

            first = first_bus.publish("one", source_type="test", source_id="a")
            second = second_bus.publish("two", source_type="test", source_id="b")

            self.assertEqual("evt-000001", first.event_id)
            self.assertEqual("evt-000002", second.event_id)
            self.assertEqual(
                ["evt-000001", "evt-000002"],
                [event.event_id for event in first_bus.list_events()],
            )

    def test_multi_instance_cursor_updates_are_merged_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "runtime" / "events"
            log_path = base / "events.jsonl"
            cursor_path = base / "cursors.json"
            first_bus = EventBus(log_path=log_path, cursor_path=cursor_path)
            first_event = first_bus.publish("one", source_type="test", source_id="a")
            second_event = first_bus.publish("two", source_type="test", source_id="b")

            second_bus = EventBus(log_path=log_path, cursor_path=cursor_path)
            first_bus.set_cursor("ui-left", first_event.event_id)
            second_bus.set_cursor("ui-right", second_event.event_id)

            self.assertEqual(first_event.event_id, second_bus.get_cursor("ui-left"))
            self.assertEqual(second_event.event_id, first_bus.get_cursor("ui-right"))

    def test_compact_keeps_last_events_and_cursor_pins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "runtime" / "events"
            log_path = base / "events.jsonl"
            cursor_path = base / "cursors.json"
            bus = EventBus(log_path=log_path, cursor_path=cursor_path)

            created = [
                bus.publish(f"event-{index}", source_type="test", source_id=f"src-{index}")
                for index in range(1, 6)
            ]
            bus.set_cursor("ui-main", created[1].event_id)

            retained, removed = bus.compact(2)
            reloaded = EventBus(log_path=log_path, cursor_path=cursor_path)
            resumed = reloaded.pull_events("ui-main")

            self.assertEqual((3, 2), (retained, removed))
            self.assertEqual(
                [created[1].event_id, created[3].event_id, created[4].event_id],
                [event.event_id for event in reloaded.list_events()],
            )
            self.assertEqual(["event-4", "event-5"], [event.kind for event in resumed])

    def test_init_auto_compacts_loaded_log_when_event_count_exceeds_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "runtime" / "events" / "events.jsonl"
            first_bus = EventBus(log_path=log_path, max_events=3)
            for index in range(5):
                first_bus.publish(f"event-{index}", source_type="test", source_id=f"src-{index}")

            reloaded = EventBus(log_path=log_path, max_events=3)

            self.assertEqual(["event-2", "event-3", "event-4"], [event.kind for event in reloaded.list_events()])

    def test_compact_rejects_zero_retain_last(self) -> None:
        bus = EventBus()

        with self.assertRaisesRegex(ValueError, "retain_last must be >= 1"):
            bus.compact(0)

    def test_cleanup_stale_cursors_removes_old_records_and_unpins_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "runtime" / "events"
            log_path = base / "events.jsonl"
            cursor_path = base / "cursors.json"
            bus = EventBus(log_path=log_path, cursor_path=cursor_path)
            created = [
                bus.publish(f"event-{index}", source_type="test", source_id=f"src-{index}")
                for index in range(1, 5)
            ]
            bus.set_cursor("stale-ui", created[0].event_id, updated_at="2020-01-01T00:00:00Z")
            bus.set_cursor("fresh-ui", created[1].event_id, updated_at="2026-03-07T00:04:30Z")

            removed = bus.cleanup_stale_cursors(60, now="2026-03-07T00:05:00Z")
            retained, trimmed = bus.compact(1)
            reloaded = EventBus(log_path=log_path, cursor_path=cursor_path)

            self.assertEqual(1, removed)
            self.assertIsNone(reloaded.get_cursor("stale-ui"))
            self.assertEqual(created[1].event_id, reloaded.get_cursor("fresh-ui"))
            self.assertEqual((2, 2), (retained, trimmed))
            self.assertEqual(
                [created[1].event_id, created[3].event_id],
                [event.event_id for event in reloaded.list_events()],
            )

    def test_cleanup_stale_cursors_rejects_negative_age(self) -> None:
        bus = EventBus()

        with self.assertRaisesRegex(ValueError, "max_age_seconds must be >= 0"):
            bus.cleanup_stale_cursors(-1)

    def test_auto_compaction_trims_events_when_exceeding_max(self) -> None:
        bus = EventBus(max_events=10)
        for index in range(1, 20):
            bus.publish(f"event-{index}", source_type="test", source_id=f"src-{index}")

        self.assertLess(bus.size(), 19)
        self.assertGreater(bus.size(), 0)

    def test_auto_compaction_preserves_cursor_pinned_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "runtime" / "events"
            log_path = base / "events.jsonl"
            cursor_path = base / "cursors.json"
            bus = EventBus(log_path=log_path, cursor_path=cursor_path, max_events=20)
            events = []
            for index in range(1, 10):
                events.append(bus.publish(f"event-{index}", source_type="test", source_id=f"src-{index}"))
            bus.set_cursor("ui-main", events[1].event_id)

            for index in range(10, 30):
                bus.publish(f"event-{index}", source_type="test", source_id=f"src-{index}")

            remaining_ids = [event.event_id for event in bus.list_events()]
            self.assertIn(events[1].event_id, remaining_ids)
            self.assertLess(len(remaining_ids), 29)


if __name__ == "__main__":
    unittest.main()
