from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME
from ai_ide.policy import PolicyEngine
from ai_ide.workspace_access_service import WorkspaceAccessService
from ai_ide.workspace_index_freshness_service import WorkspaceIndexFreshnessService
from ai_ide.workspace_index_snapshot_builder import WorkspaceIndexSnapshotBuilder


class WorkspaceIndexFreshnessServiceTests(unittest.TestCase):
    def test_stale_reasons_report_workspace_drift_after_visible_file_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            policy = PolicyEngine(root)
            access = WorkspaceAccessService(root, policy)
            builder = WorkspaceIndexSnapshotBuilder(root, access)
            freshness = WorkspaceIndexFreshnessService(builder)

            snapshot = builder.build(policy_version=policy.state.version)
            target.write_text("updated visible plan", encoding="utf-8")

            self.assertEqual(["workspace"], freshness.stale_reasons(snapshot, policy_version=policy.state.version))

    def test_stale_reasons_ignore_denied_and_internal_path_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")

            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            access = WorkspaceAccessService(root, policy)
            builder = WorkspaceIndexSnapshotBuilder(root, access)
            freshness = WorkspaceIndexFreshnessService(builder)

            snapshot = builder.build(policy_version=policy.state.version)
            (root / "secrets" / "token.txt").write_text("changed hidden plan", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "events.jsonl").write_text("secret", encoding="utf-8")

            self.assertEqual([], freshness.stale_reasons(snapshot, policy_version=policy.state.version))

    def test_stale_reasons_can_use_external_current_signature_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            policy = PolicyEngine(root)
            access = WorkspaceAccessService(root, policy)
            builder = WorkspaceIndexSnapshotBuilder(root, access)
            snapshot = builder.build(policy_version=policy.state.version)
            target.write_text("changed visible plan", encoding="utf-8")
            current_signature = builder.build_signature()
            freshness = WorkspaceIndexFreshnessService(
                builder,
                current_signature_provider=lambda: current_signature,
            )

            builder.build_signature = lambda: (_ for _ in ()).throw(AssertionError("should not rescan"))  # type: ignore[method-assign]

            self.assertEqual(["workspace"], freshness.stale_reasons(snapshot, policy_version=policy.state.version))


if __name__ == "__main__":
    unittest.main()
