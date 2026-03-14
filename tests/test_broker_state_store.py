from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.broker_state_store import BrokerStateStore
from ai_ide.models import AuditEvent, UsageMetrics


class BrokerStateStoreTests(unittest.TestCase):
    def test_load_returns_default_snapshot_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = BrokerStateStore(Path(temp_dir) / "broker" / "state.json")

            snapshot = store.load()

            self.assertEqual(0, snapshot.metrics.read_count)
            self.assertEqual([], snapshot.audit_log)

    def test_save_and_load_round_trip_metrics_and_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = BrokerStateStore(Path(temp_dir) / "broker" / "state.json")
            metrics = UsageMetrics(read_count=2, grep_count=1, blocked_count=3)
            audit_log = [
                AuditEvent(
                    timestamp="2026-03-07T00:00:00Z",
                    session_id="sess-1",
                    action="read",
                    target="C:/repo/file.txt",
                    allowed=True,
                    detail="bytes=10",
                ),
                AuditEvent(
                    timestamp="2026-03-07T00:00:01Z",
                    session_id="sess-1",
                    action="read",
                    target="C:/repo/secret.txt",
                    allowed=False,
                    detail="denied by policy",
                ),
            ]
            store.save(metrics, audit_log)

            snapshot = store.load()

            self.assertEqual(2, snapshot.metrics.read_count)
            self.assertEqual(1, snapshot.metrics.grep_count)
            self.assertEqual(3, snapshot.metrics.blocked_count)
            self.assertEqual(2, len(snapshot.audit_log))
            self.assertFalse(snapshot.audit_log[-1].allowed)

    def test_load_reuses_cached_snapshot_when_file_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = BrokerStateStore(Path(temp_dir) / "broker" / "state.json")
            metrics = UsageMetrics(read_count=1)
            audit_log = [
                AuditEvent(
                    timestamp="2026-03-07T00:00:00Z",
                    session_id="sess-1",
                    action="read",
                    target="C:/repo/file.txt",
                    allowed=True,
                    detail="bytes=10",
                )
            ]
            store.save(metrics, audit_log)
            first_snapshot = store.load()

            with patch.object(Path, "read_text", side_effect=AssertionError("should reuse cached snapshot")):
                second_snapshot = store.load()

        self.assertEqual(first_snapshot.metrics.read_count, second_snapshot.metrics.read_count)
        self.assertEqual(first_snapshot.audit_log[0].target, second_snapshot.audit_log[0].target)


if __name__ == "__main__":
    unittest.main()
