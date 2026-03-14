from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import AIIdeApp
from backend.workspace_panel_service import WorkspacePanelService


class WorkspacePanelServiceTests(unittest.TestCase):
    def test_snapshot_returns_visible_tree_policy_and_index_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("policy add secrets/**")
            app.handle_command("workspace index refresh json")

            panel = WorkspacePanelService(app).snapshot()

            self.assertEqual("workspace_panel", panel["kind"])
            self.assertEqual(".", panel["target"])
            self.assertEqual(["secrets/**"], panel["policy"]["deny_globs"])
            self.assertEqual(2, panel["policy"]["version"])
            self.assertFalse(panel["workspace_index"]["stale"])
            self.assertEqual(
                ["notes", "notes/todo.txt"],
                [entry["path"] for entry in panel["workspace"]["entries"]],
            )
            self.assertEqual(
                ["notes/**", "notes/todo.txt"],
                [entry["suggested_deny_rule"] for entry in panel["workspace"]["entries"]],
            )

    def test_add_and_remove_deny_rule_return_updated_panel_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            service = WorkspacePanelService(app)

            added = service.add_deny_rule("notes/**")
            removed = service.remove_deny_rule("notes/**")

            self.assertTrue(added["change"]["changed"])
            self.assertEqual(["notes/**"], added["panel"]["policy"]["deny_globs"])
            self.assertEqual([], added["panel"]["workspace"]["entries"])
            self.assertTrue(removed["change"]["changed"])
            self.assertEqual([], removed["panel"]["policy"]["deny_globs"])


if __name__ == "__main__":
    unittest.main()
