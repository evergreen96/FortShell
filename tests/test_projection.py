from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME, INTERNAL_RUNTIME_DIR_NAME
from ai_ide.policy import PolicyEngine
from ai_ide.projection import ProjectedWorkspaceManager


class ProjectedWorkspaceManagerTests(unittest.TestCase):
    def test_materialize_copies_only_allowed_files_and_skips_internal_project_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / INTERNAL_RUNTIME_DIR_NAME).mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / "safe" / "todo.txt").write_text("visible", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
            (root / INTERNAL_RUNTIME_DIR_NAME / "trace.log").write_text("runtime", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("metadata", encoding="utf-8")

            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            projection = ProjectedWorkspaceManager(root, policy, runtime_root)

            manifest = projection.materialize("sess-test")

            self.assertTrue((manifest.root / "safe" / "todo.txt").exists())
            self.assertFalse((manifest.root / "secrets").exists())
            self.assertFalse((manifest.root / INTERNAL_RUNTIME_DIR_NAME).exists())
            self.assertFalse((manifest.root / INTERNAL_PROJECT_METADATA_DIR_NAME).exists())
            self.assertEqual(1, manifest.file_count)
            self.assertFalse(manifest.root.is_relative_to(root))

            stored_manifest = projection.read_manifest("sess-test")
            self.assertEqual("sess-test", stored_manifest.session_id)
            self.assertEqual(policy.state.version, stored_manifest.policy_version)

    def test_materialize_cleans_up_stale_projection_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible", encoding="utf-8")

            policy = PolicyEngine(root)
            projection = ProjectedWorkspaceManager(root, policy, runtime_root)

            first = projection.materialize("sess-a")
            second = projection.materialize("sess-b")

            self.assertFalse(first.root.exists())
            self.assertTrue(second.root.exists())

    def test_materialize_skips_symlink_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            link = root / "notes-link"
            try:
                link.symlink_to(root / "notes", target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink not supported: {exc}")

            policy = PolicyEngine(root)
            projection = ProjectedWorkspaceManager(root, policy, runtime_root)

            manifest = projection.materialize("sess-test")

            self.assertTrue((manifest.root / "notes" / "todo.txt").exists())
            self.assertFalse((manifest.root / "notes-link").exists())

    def test_materialize_skips_hardlink_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "secrets").mkdir()
            target = root / "secrets" / "token.txt"
            target.write_text("hidden", encoding="utf-8")
            alias = root / "notes" / "token-alias.txt"
            try:
                alias.hardlink_to(target)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"hardlink not supported: {exc}")

            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            projection = ProjectedWorkspaceManager(root, policy, runtime_root)

            manifest = projection.materialize("sess-test")

            self.assertFalse((manifest.root / "notes" / "token-alias.txt").exists())


if __name__ == "__main__":
    unittest.main()
