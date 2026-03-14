"""Tests for DesktopApiService."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from backend.desktop_api_service import DesktopApiService


class TestDesktopApiService(unittest.TestCase):
    def setUp(self) -> None:
        self.app = MagicMock()
        self.service = DesktopApiService(self.app)
        # Patch sub-services with mocks
        self.service.desktop = MagicMock()
        self.service.editor = MagicMock()
        self.service.panel = MagicMock()

    def test_desktop_shell_snapshot_delegates(self) -> None:
        self.service.desktop.snapshot.return_value = {"kind": "desktop_shell"}
        result = self.service.desktop_shell_snapshot(".")
        self.service.desktop.snapshot.assert_called_once_with(".")
        self.assertEqual(result["kind"], "desktop_shell")

    def test_editor_file_delegates(self) -> None:
        self.service.editor.snapshot.return_value = {"kind": "editor_file"}
        result = self.service.editor_file("src/main.py")
        self.service.editor.snapshot.assert_called_once_with("src/main.py")

    def test_editor_save_delegates(self) -> None:
        self.service.editor.save.return_value = {"kind": "editor_save"}
        result = self.service.editor_save("src/main.py", "content")
        self.service.editor.save.assert_called_once_with("src/main.py", "content")
        self.assertEqual(result["kind"], "editor_save")

    def test_editor_stage_delegates(self) -> None:
        self.service.editor.stage.return_value = {"kind": "editor_stage"}
        result = self.service.editor_stage("a.py", "content")
        self.service.editor.stage.assert_called_once_with("a.py", "content")

    def test_policy_deny_delegates(self) -> None:
        self.service.panel.add_deny_rule.return_value = {"kind": "policy"}
        result = self.service.policy_deny("*.log", target=".")
        self.service.panel.add_deny_rule.assert_called_once_with("*.log", target=".")

    def test_terminal_create_delegates(self) -> None:
        terminal = MagicMock()
        terminal.terminal_id = "term-1"
        self.app.create_terminal.return_value = terminal
        inspection = MagicMock()
        inspection.to_dict.return_value = {"terminal_id": "term-1"}
        self.app.inspect_terminal.return_value = inspection

        result = self.service.terminal_create(name="test", transport="host", io_mode="pty")
        self.assertEqual(result["kind"], "terminal_create")
        self.app.create_terminal.assert_called_once_with(
            name="test", transport="host", runner_mode=None, io_mode="pty",
        )

    def test_terminal_run_delegates(self) -> None:
        self.app.run_terminal_command.return_value = "output"
        inspection = MagicMock()
        inspection.to_dict.return_value = {"terminal_id": "term-1"}
        self.app.inspect_terminal.return_value = inspection

        result = self.service.terminal_run("term-1", "ls")
        self.assertEqual(result["kind"], "terminal_run")
        self.assertEqual(result["output"], "output")

    def test_pty_write_delegates(self) -> None:
        result = self.service.pty_write("term-1", "hello")
        self.app.write_to_pty.assert_called_once_with("term-1", "hello")
        self.assertEqual(result, {"kind": "pty_write", "ok": True})

    def test_pty_resize_delegates(self) -> None:
        result = self.service.pty_resize("term-1", 120, 40)
        self.app.resize_pty.assert_called_once_with("term-1", 120, 40)
        self.assertEqual(result, {"kind": "pty_resize", "ok": True})

    def test_review_action_apply(self) -> None:
        proposal = MagicMock()
        proposal.to_dict.return_value = {"proposal_id": "p-1"}
        self.app.apply_review.return_value = proposal
        self.service.desktop.snapshot.return_value = {"kind": "desktop_shell"}

        result = self.service.review_action("apply", "p-1", ".")
        self.assertEqual(result["kind"], "review_action")
        self.assertEqual(result["action"], "apply")
        self.app.apply_review.assert_called_once_with("p-1")

    def test_review_render(self) -> None:
        self.app.render_review.return_value = "diff text"
        result = self.service.review_render("p-1")
        self.assertEqual(result["kind"], "review_render")
        self.assertEqual(result["content"], "diff text")


if __name__ == "__main__":
    unittest.main()
