from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.internal import INTERNAL_PROJECT_METADATA_DIR_NAME
from core.policy import PolicyEngine
from core.workspace_access_service import WorkspaceAccessService


class WorkspaceAccessServiceTests(unittest.TestCase):
    def test_iter_visible_paths_hide_internal_and_policy_blocked_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes").mkdir()
            (root / "notes" / "nested").mkdir()
            (root / "secrets").mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            (root / "notes" / "nested" / "deep.txt").write_text("deeper", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")
            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            service = WorkspaceAccessService(root, policy)

            children = [path.name for path in service.iter_visible_children(root)]
            files = [path.relative_to(root).as_posix() for path in service.iter_visible_files(root)]
            tree = [path.relative_to(root).as_posix() for path in service.iter_visible_tree()]

            self.assertEqual(["notes"], children)
            self.assertEqual(["notes/nested/deep.txt", "notes/todo.txt"], files)
            self.assertEqual(["notes", "notes/nested", "notes/nested/deep.txt", "notes/todo.txt"], tree)

    def test_resolve_allowed_path_rejects_internal_policy_blocked_and_escaped_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes").mkdir()
            (root / "secrets").mkdir()
            (root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            service = WorkspaceAccessService(root, policy)

            allowed = service.resolve_allowed_path("notes/todo.txt")
            self.assertEqual(root / "notes" / "todo.txt", allowed)

            with self.assertRaises(PermissionError):
                service.resolve_allowed_path("secrets/token.txt")

            with self.assertRaises(PermissionError):
                service.resolve_allowed_path(".ai-ide/policy.json")

    def test_iter_visible_accessors_reject_blocked_directory_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes").mkdir()
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            service = WorkspaceAccessService(root, policy)

            with self.assertRaises(PermissionError):
                list(service.iter_visible_children(root / "secrets"))

            with self.assertRaises(PermissionError):
                list(service.iter_visible_files(root / "secrets"))

    def test_symlink_paths_are_hidden_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            link = root / "notes-link"
            try:
                link.symlink_to(root / "notes", target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink not supported: {exc}")

            policy = PolicyEngine(root)
            service = WorkspaceAccessService(root, policy)

            children = [path.name for path in service.iter_visible_children(root)]

            self.assertNotIn("notes-link", children)
            with self.assertRaises(PermissionError):
                service.resolve_allowed_path("notes-link/todo.txt")

    def test_inspect_path_reports_denied_as_policy_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            service = WorkspaceAccessService(root, policy)

            decision = service.inspect_path("secrets/token.txt")

            self.assertFalse(decision.allowed)
            self.assertEqual("policy", decision.access_reason)
            self.assertEqual("secrets/**", decision.matched_rule)

    def test_inspect_path_reports_allowed_for_visible_workspace_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            policy = PolicyEngine(root)
            service = WorkspaceAccessService(root, policy)

            decision = service.inspect_path("notes/todo.txt")

            self.assertTrue(decision.allowed)
            self.assertEqual("allowed", decision.access_reason)

    def test_hardlink_paths_are_hidden_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
            service = WorkspaceAccessService(root, policy)

            files = [path.relative_to(root).as_posix() for path in service.iter_visible_files(root)]

            self.assertNotIn("notes/token-alias.txt", files)
            with self.assertRaises(PermissionError):
                service.resolve_allowed_path("notes/token-alias.txt")


if __name__ == "__main__":
    unittest.main()
