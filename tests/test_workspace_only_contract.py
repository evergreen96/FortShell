"""Workspace-only product contract tests.

These tests define what the product guarantees:
- Deny-listed paths inside the workspace are invisible to agents.
- Internal metadata (.ai-ide/) is always hidden.
- Policy mutation is scoped to workspace-internal targets only.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.app import AIIdeApp
from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME
from ai_ide.policy import PolicyEngine
from ai_ide.workspace_access_service import WorkspaceAccessService
from ai_ide.workspace_panel_service import WorkspacePanelService


class WorkspaceOnlyContractTests(unittest.TestCase):
    def test_deny_list_blocks_denied_paths_and_hides_internal_metadata(self) -> None:
        """Denied workspace paths and internal metadata are blocked by the access service."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")

            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            access = WorkspaceAccessService(root, policy)

            self.assertTrue(access.inspect_path("notes/todo.txt").allowed)
            self.assertFalse(access.inspect_path("secrets/token.txt").allowed)
            self.assertFalse(access.inspect_path(f"{INTERNAL_PROJECT_METADATA_DIR_NAME}/policy.json").allowed)

    def test_broker_hides_denied_workspace_entries(self) -> None:
        """Denied paths are invisible to broker read/list/grep, internal metadata is blocked."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("policy add secrets/**")

            self.assertEqual("notes/", app.handle_command("ai ls ."))
            self.assertEqual("notes/todo.txt:1:visible", app.handle_command("ai grep visible ."))
            with self.assertRaises(PermissionError):
                app.handle_command("ai read secrets/token.txt")
            with self.assertRaises(PermissionError):
                app.handle_command(f"ai read {INTERNAL_PROJECT_METADATA_DIR_NAME}/policy.json")

    def test_workspace_hides_denied_and_internal_entries(self) -> None:
        """Workspace list/grep/tree hide denied subtrees and internal metadata."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("policy add secrets/**")

            self.assertEqual("notes/", app.handle_command("workspace list ."))
            self.assertEqual("notes/todo.txt:1:visible", app.handle_command("workspace grep visible ."))
            with self.assertRaises(PermissionError):
                app.handle_command("workspace tree secrets")
            with self.assertRaises(PermissionError):
                app.handle_command(f"workspace tree {INTERNAL_PROJECT_METADATA_DIR_NAME}")

    def test_workspace_panel_hides_internal_metadata(self) -> None:
        """Workspace panel blocks access to internal metadata directories."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)

            with self.assertRaises(PermissionError):
                app.handle_command(f"workspace panel {INTERNAL_PROJECT_METADATA_DIR_NAME} json")
            with self.assertRaises(PermissionError):
                app.handle_command(f"workspace panel {INTERNAL_PROJECT_METADATA_DIR_NAME}")

    def test_policy_mutation_is_workspace_scoped(self) -> None:
        """Policy add/remove only applies to workspace-internal targets."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            app = AIIdeApp(root, runtime_root=runtime_root)
            panel = WorkspacePanelService(app)

            with self.assertRaises(PermissionError):
                panel.add_deny_rule("notes/**", target="../outside-panel")

            with self.assertRaises(PermissionError):
                panel.remove_deny_rule("notes/**", target="../outside-panel")


if __name__ == "__main__":
    unittest.main()
