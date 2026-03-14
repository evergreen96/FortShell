from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.broker import MAX_READ_FILE_BYTES, ToolBroker
from ai_ide.broker_state_store import BrokerStateStore
from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME
from ai_ide.models import AuditEvent
from ai_ide.policy import PolicyEngine
from ai_ide.session import SessionManager


class ToolBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "notes").mkdir()
        (self.root / "secrets").mkdir()
        (self.root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
        (self.root / "notes" / "todo.txt").write_text("visible text", encoding="utf-8")
        (self.root / "secrets" / "token.txt").write_text("secret text", encoding="utf-8")
        (self.root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")

        self.policy = PolicyEngine(self.root)
        self.sessions = SessionManager(self.policy)
        self.state_store = BrokerStateStore(self.root / ".runtime" / "broker" / "state.json")
        self.broker = ToolBroker(self.root, self.policy, self.sessions, state_store=self.state_store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_read_and_write_to_denied_paths_are_blocked(self) -> None:
        self.policy.add_deny_rule("secrets/**")

        with self.assertRaises(PermissionError):
            self.broker.read_file("secrets/token.txt")

        with self.assertRaises(PermissionError):
            self.broker.write_file("secrets/new.txt", "blocked")

        self.assertEqual(2, self.broker.metrics.blocked_count)

    def test_read_file_blocks_oversized_content(self) -> None:
        oversized = self.root / "notes" / "large.txt"
        oversized.write_text("x" * (MAX_READ_FILE_BYTES + 1), encoding="utf-8")

        with self.assertRaises(ValueError):
            self.broker.read_file("notes/large.txt")

        self.assertEqual(1, self.broker.metrics.blocked_count)
        self.assertEqual("read", self.broker.audit_log[-1].action)
        self.assertFalse(self.broker.audit_log[-1].allowed)
        self.assertIn("file too large", self.broker.audit_log[-1].detail)

    def test_metrics_and_audit_log_restore_from_state_store(self) -> None:
        self.broker.read_file("notes/todo.txt")
        self.policy.add_deny_rule("secrets/**")
        with self.assertRaises(PermissionError):
            self.broker.read_file("secrets/token.txt")

        reloaded = ToolBroker(self.root, self.policy, self.sessions, state_store=self.state_store)

        self.assertEqual(2, reloaded.metrics.read_count)
        self.assertEqual(1, reloaded.metrics.blocked_count)
        self.assertEqual(2, len(reloaded.audit_log))

    def test_record_runtime_action_persists_audit_and_optional_write_metric(self) -> None:
        self.broker.record_runtime_action(
            "review.stage",
            "notes/todo.txt",
            allowed=True,
            detail="proposal_id=rev-1234",
            count_as_write=True,
        )

        reloaded = ToolBroker(self.root, self.policy, self.sessions, state_store=self.state_store)

        self.assertEqual(1, reloaded.metrics.write_count)
        self.assertEqual("review.stage", reloaded.audit_log[-1].action)
        self.assertTrue(reloaded.audit_log[-1].allowed)


    def test_audit_log_is_trimmed_when_exceeding_max_entries(self) -> None:
        from ai_ide.broker import MAX_AUDIT_ENTRIES

        for index in range(MAX_AUDIT_ENTRIES + 50):
            (self.root / "notes" / f"file_{index}.txt").write_text(f"content {index}", encoding="utf-8")
            self.broker.read_file(f"notes/file_{index}.txt")

        self.assertLessEqual(len(self.broker.audit_log), MAX_AUDIT_ENTRIES)
        self.assertEqual("read", self.broker.audit_log[-1].action)

    def test_audit_log_is_trimmed_on_reload(self) -> None:
        from ai_ide.broker import MAX_AUDIT_ENTRIES

        for index in range(MAX_AUDIT_ENTRIES + 10):
            self.broker.audit_log.append(
                AuditEvent(
                    timestamp=f"2026-03-13T00:{index % 60:02d}:00Z",
                    session_id=self.sessions.current_session_id,
                    action="read",
                    target=str(self.root / "notes" / f"file_{index}.txt"),
                    allowed=True,
                    detail=f"bytes={index}",
                )
            )
        self.state_store.save(self.broker.metrics, self.broker.audit_log)

        reloaded = ToolBroker(self.root, self.policy, self.sessions, state_store=self.state_store)

        self.assertEqual(MAX_AUDIT_ENTRIES, len(reloaded.audit_log))


if __name__ == "__main__":
    unittest.main()
