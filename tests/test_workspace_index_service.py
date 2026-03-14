from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME
from ai_ide.policy import PolicyEngine
from ai_ide.workspace_access_service import WorkspaceAccessService
from ai_ide.workspace_index_service import WorkspaceIndexService
from ai_ide.workspace_index_state_store import WorkspaceIndexStateStore


class WorkspaceIndexServiceTests(unittest.TestCase):
    def test_refresh_persists_visible_entries_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "nested").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "notes" / "nested" / "deep.txt").write_text("deep plan", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")

            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            access = WorkspaceAccessService(root, policy)
            service = WorkspaceIndexService(
                root,
                policy,
                access,
                state_store=WorkspaceIndexStateStore(base / "runtime" / "workspace" / "index.json"),
            )

            snapshot = service.refresh()

            self.assertFalse(service.is_stale(snapshot))
            self.assertEqual(policy.state.version, snapshot.policy_version)
            self.assertTrue(snapshot.signature)
            self.assertEqual(
                ["notes", "notes/nested", "notes/nested/deep.txt", "notes/todo.txt"],
                [entry.path for entry in snapshot.entries],
            )
            self.assertEqual(2, snapshot.file_count)
            self.assertEqual(2, snapshot.directory_count)
            self.assertTrue(all(entry.modified_ns > 0 for entry in snapshot.entries))
            self.assertTrue(all(entry.size >= 0 for entry in snapshot.entries))

    def test_snapshot_becomes_stale_after_policy_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")

            policy = PolicyEngine(root)
            access = WorkspaceAccessService(root, policy)
            service = WorkspaceIndexService(
                root,
                policy,
                access,
                state_store=WorkspaceIndexStateStore(base / "runtime" / "workspace" / "index.json"),
            )

            snapshot = service.refresh()
            policy.add_deny_rule("notes/**")

            self.assertEqual(1, snapshot.policy_version)
            self.assertTrue(service.is_stale(snapshot))

    def test_snapshot_becomes_stale_after_visible_workspace_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            policy = PolicyEngine(root)
            access = WorkspaceAccessService(root, policy)
            service = WorkspaceIndexService(
                root,
                policy,
                access,
                state_store=WorkspaceIndexStateStore(base / "runtime" / "workspace" / "index.json"),
            )

            snapshot = service.refresh()
            target.write_text("changed visible plan", encoding="utf-8")

            self.assertTrue(service.is_stale(snapshot))


if __name__ == "__main__":
    unittest.main()
