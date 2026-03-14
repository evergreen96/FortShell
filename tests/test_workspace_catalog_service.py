from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME
from ai_ide.policy import PolicyEngine
from ai_ide.workspace_access_service import WorkspaceAccessService
from ai_ide.workspace_catalog_service import MAX_GREP_FILE_BYTES, WorkspaceCatalogService


class WorkspaceCatalogServiceTests(unittest.TestCase):
    def test_list_dir_returns_typed_visible_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            access = WorkspaceAccessService(root, policy)
            catalog = WorkspaceCatalogService(root, access)

            entries = catalog.list_dir(".")

            self.assertEqual(["notes"], [entry.path for entry in entries])
            self.assertEqual(["notes/"], [entry.display_name for entry in entries])
            self.assertTrue(entries[0].is_dir)

    def test_iter_tree_prunes_hidden_subtrees_and_uses_root_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes").mkdir()
            (root / "notes" / "nested").mkdir()
            (root / "notes" / "nested" / "deep.txt").write_text("deep text", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            access = WorkspaceAccessService(root, policy)
            catalog = WorkspaceCatalogService(root, access)

            tree = [entry.path for entry in catalog.iter_tree("notes")]

            self.assertEqual(["notes/nested", "notes/nested/deep.txt"], tree)

    def test_grep_returns_typed_matches_and_skips_hidden_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible text\nother line", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("secret text", encoding="utf-8")
            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            access = WorkspaceAccessService(root, policy)
            catalog = WorkspaceCatalogService(root, access)

            matches = catalog.grep("text")

            self.assertEqual(1, len(matches))
            self.assertEqual("notes/todo.txt", matches[0].path)
            self.assertEqual(1, matches[0].line_number)
            self.assertEqual("visible text", matches[0].line_text)
            self.assertEqual("notes/todo.txt:1:visible text", matches[0].format_cli())

    def test_grep_skips_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible text", encoding="utf-8")
            (root / "notes" / "large.txt").write_text("x" * (MAX_GREP_FILE_BYTES + 1), encoding="utf-8")
            access = WorkspaceAccessService(root, PolicyEngine(root))
            catalog = WorkspaceCatalogService(root, access)

            matches = catalog.grep("visible")

            self.assertEqual(["notes/todo.txt"], [match.path for match in matches])



if __name__ == "__main__":
    unittest.main()
