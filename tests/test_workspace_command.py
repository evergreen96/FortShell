from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app import AIIdeApp
from core.internal import INTERNAL_PROJECT_METADATA_DIR_NAME


class WorkspaceCommandTests(unittest.TestCase):
    def test_workspace_json_queries_hide_denied_paths_without_touching_broker_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("policy add secrets/**")
            before_metrics = app.handle_command("metrics json")

            listing = json.loads(app.handle_command("workspace list . json"))
            tree = json.loads(app.handle_command("workspace tree . json"))
            grep = json.loads(app.handle_command("workspace grep plan . json"))
            after_metrics = app.handle_command("metrics json")

            self.assertEqual(before_metrics, after_metrics)
            self.assertEqual(["notes"], [entry["path"] for entry in listing["entries"]])
            self.assertEqual(
                ["notes", "notes/todo.txt"],
                [entry["path"] for entry in tree["entries"]],
            )
            self.assertEqual(["notes/todo.txt"], [match["path"] for match in grep["matches"]])

    def test_workspace_panel_json_exposes_tree_entries_and_deny_rule_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "nested").mkdir()
            (root / "notes" / "nested" / "deep.txt").write_text("deep plan", encoding="utf-8")
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("policy add secrets/**")
            before_metrics = app.handle_command("metrics json")

            panel = json.loads(app.handle_command("workspace panel notes json"))

            after_metrics = app.handle_command("metrics json")
            self.assertEqual(before_metrics, after_metrics)
            self.assertEqual("panel", panel["kind"])
            self.assertEqual("notes", panel["target"])
            self.assertEqual(["secrets/**"], panel["deny_rules"])
            self.assertEqual(app.policy.state.version, panel["policy_version"])
            self.assertEqual(app.sessions.current_session_id, panel["execution_session_id"])
            self.assertEqual(app.sessions.current_agent_session_id, panel["agent_session_id"])
            self.assertEqual(
                [
                    {
                        "path": "notes/nested",
                        "deny_rule": "notes/nested/**",
                    },
                    {
                        "path": "notes/nested/deep.txt",
                        "deny_rule": "notes/nested/deep.txt",
                    },
                    {
                        "path": "notes/todo.txt",
                        "deny_rule": "notes/todo.txt",
                    },
                ],
                [
                    {
                        "path": entry["path"],
                        "deny_rule": entry["deny_rule"],
                    }
                    for entry in panel["entries"]
                ],
            )

    def test_workspace_text_queries_return_catalog_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "nested").mkdir()
            (root / "notes" / "nested" / "deep.txt").write_text("deep text", encoding="utf-8")
            (root / "notes" / "todo.txt").write_text("visible text", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)

            listing = app.handle_command("workspace list .")
            tree = app.handle_command("workspace tree notes")
            grep = app.handle_command("workspace grep text .")

            self.assertEqual("notes/", listing)
            self.assertEqual("notes/nested/\nnotes/nested/deep.txt\nnotes/todo.txt", tree)
            self.assertEqual(
                "notes/nested/deep.txt:1:deep text\nnotes/todo.txt:1:visible text",
                grep,
            )

    def test_workspace_index_commands_expose_cached_visible_snapshot_without_touching_broker_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            before_metrics = app.handle_command("metrics json")
            initial = json.loads(app.handle_command("workspace index show json"))
            app.handle_command("policy add secrets/**")
            refreshed = json.loads(app.handle_command("workspace index refresh json"))
            stale = json.loads(app.handle_command("workspace index show json"))
            after_metrics = app.handle_command("metrics json")

            self.assertEqual(before_metrics, after_metrics)
            self.assertTrue(initial["stale"])
            self.assertEqual(["policy"], initial["stale_reasons"])
            self.assertFalse(refreshed["stale"])
            self.assertEqual([], refreshed["stale_reasons"])
            self.assertFalse(stale["stale"])
            self.assertEqual([], stale["stale_reasons"])
            self.assertEqual(["notes", "notes/todo.txt"], [entry["path"] for entry in refreshed["entries"]])
            self.assertFalse(any(entry["path"].startswith("secrets") for entry in refreshed["entries"]))
            self.assertEqual(refreshed["entries"], stale["entries"])
            self.assertEqual(refreshed["policy_version"], stale["policy_version"])

    def test_workspace_index_show_marks_workspace_stale_after_external_visible_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("workspace index refresh json")
            target.write_text("changed visible plan", encoding="utf-8")

            shown = json.loads(app.handle_command("workspace index show json"))

            self.assertTrue(shown["stale"])
            self.assertEqual(["workspace"], shown["stale_reasons"])


if __name__ == "__main__":
    unittest.main()
