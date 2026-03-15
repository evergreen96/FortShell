"""Tests for desktop sidecar dispatcher."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.desktop_api_service import DesktopApiService
from backend.desktop_sidecar import SidecarDispatcher, SidecarWriter, _classify_error


class TestSidecarDispatcher(unittest.TestCase):
    def setUp(self) -> None:
        self.api = MagicMock(spec=DesktopApiService)
        self.dispatcher = SidecarDispatcher(self.api)

    def test_desktop_shell_snapshot(self) -> None:
        self.api.desktop_shell_snapshot.return_value = {"kind": "desktop_shell"}
        result = self.dispatcher.dispatch("desktop_shell.snapshot", {"target": "."})
        self.api.desktop_shell_snapshot.assert_called_once_with(".")
        self.assertEqual(result, {"kind": "desktop_shell"})

    def test_editor_file(self) -> None:
        self.api.editor_file.return_value = {"kind": "editor_file"}
        result = self.dispatcher.dispatch("editor.file", {"target": "src/main.py"})
        self.api.editor_file.assert_called_once_with("src/main.py")
        self.assertEqual(result, {"kind": "editor_file"})

    def test_editor_save(self) -> None:
        self.api.editor_save.return_value = {"kind": "editor_save"}
        result = self.dispatcher.dispatch("editor.save", {"target": "a.py", "content": "x"})
        self.api.editor_save.assert_called_once_with("a.py", "x")
        self.assertEqual(result, {"kind": "editor_save"})

    def test_editor_stage(self) -> None:
        self.api.editor_stage.return_value = {"kind": "editor_stage"}
        result = self.dispatcher.dispatch("editor.stage", {"target": "a.py", "content": "x"})
        self.api.editor_stage.assert_called_once_with("a.py", "x")

    def test_editor_apply(self) -> None:
        self.api.editor_apply.return_value = {"kind": "editor_apply"}
        result = self.dispatcher.dispatch("editor.apply", {"proposal_id": "p-1"})
        self.api.editor_apply.assert_called_once_with("p-1")

    def test_editor_reject(self) -> None:
        self.api.editor_reject.return_value = {"kind": "editor_reject"}
        result = self.dispatcher.dispatch("editor.reject", {"proposal_id": "p-2"})
        self.api.editor_reject.assert_called_once_with("p-2")

    def test_policy_deny(self) -> None:
        self.api.policy_deny.return_value = {"kind": "policy"}
        result = self.dispatcher.dispatch("policy.deny", {"rule": "*.log", "target": "."})
        self.api.policy_deny.assert_called_once_with("*.log", target=".")

    def test_policy_allow(self) -> None:
        self.api.policy_allow.return_value = {"kind": "policy"}
        result = self.dispatcher.dispatch("policy.allow", {"rule": "*.log", "target": "."})
        self.api.policy_allow.assert_called_once_with("*.log", target=".")

    def test_terminal_create(self) -> None:
        self.api.terminal_create.return_value = {"kind": "terminal_create"}
        result = self.dispatcher.dispatch("terminal.create", {
            "name": "test",
            "transport": "host",
            "io_mode": "pty",
        })
        self.api.terminal_create.assert_called_once_with(
            name="test",
            transport="host",
            runner_mode=None,
            io_mode="pty",
            profile_id=None,
        )

    def test_terminal_run(self) -> None:
        self.api.terminal_run.return_value = {"kind": "terminal_run"}
        result = self.dispatcher.dispatch("terminal.run", {
            "terminal_id": "term-1",
            "command": "ls",
        })
        self.api.terminal_run.assert_called_once_with("term-1", "ls")

    def test_pty_write(self) -> None:
        self.api.pty_write.return_value = {"kind": "pty_write", "ok": True}
        result = self.dispatcher.dispatch("terminal.pty.write", {
            "terminal_id": "term-1",
            "data": "hello",
        })
        self.api.pty_write.assert_called_once_with("term-1", "hello")

    def test_pty_resize(self) -> None:
        self.api.pty_resize.return_value = {"kind": "pty_resize", "ok": True}
        result = self.dispatcher.dispatch("terminal.pty.resize", {
            "terminal_id": "term-1",
            "cols": 120,
            "rows": 40,
        })
        self.api.pty_resize.assert_called_once_with("term-1", 120, 40)

    def test_review_apply(self) -> None:
        self.api.review_action.return_value = {"kind": "review_action"}
        result = self.dispatcher.dispatch("review.apply", {
            "proposal_id": "p-3",
            "target": ".",
        })
        self.api.review_action.assert_called_once_with("apply", "p-3", ".")

    def test_review_reject(self) -> None:
        self.api.review_action.return_value = {"kind": "review_action"}
        result = self.dispatcher.dispatch("review.reject", {"proposal_id": "p-4"})
        self.api.review_action.assert_called_once_with("reject", "p-4", ".")

    def test_unknown_method_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.dispatcher.dispatch("unknown.method", {})

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.dispatcher.dispatch("editor.file", {})  # target missing

    def test_missing_required_field_empty_string_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.dispatcher.dispatch("editor.file", {"target": ""})


class TestClassifyError(unittest.TestCase):
    def test_file_not_found(self) -> None:
        self.assertEqual(_classify_error(FileNotFoundError("x")), "file_not_found")

    def test_permission_denied(self) -> None:
        self.assertEqual(_classify_error(PermissionError("x")), "permission_denied")

    def test_key_error(self) -> None:
        self.assertEqual(_classify_error(KeyError("x")), "not_found")

    def test_value_error(self) -> None:
        self.assertEqual(_classify_error(ValueError("x")), "invalid_request")

    def test_runtime_error(self) -> None:
        self.assertEqual(_classify_error(RuntimeError("x")), "runtime_error")

    def test_generic(self) -> None:
        self.assertEqual(_classify_error(Exception("x")), "internal_error")


class TestSidecarWriter(unittest.TestCase):
    def test_write_response(self) -> None:
        import io
        output = io.StringIO()
        writer = SidecarWriter(output)
        writer.write_response("req-1", {"ok": True})
        line = output.getvalue().strip()
        import json
        obj = json.loads(line)
        self.assertEqual(obj["type"], "response")
        self.assertEqual(obj["id"], "req-1")
        self.assertTrue(obj["ok"])

    def test_write_error(self) -> None:
        import io
        import json
        output = io.StringIO()
        writer = SidecarWriter(output)
        writer.write_error("req-2", "bad", "oops")
        obj = json.loads(output.getvalue().strip())
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["error"]["code"], "bad")

    def test_write_event(self) -> None:
        import io
        import json
        output = io.StringIO()
        writer = SidecarWriter(output)
        writer.write_event("pty.data", {"x": 1})
        obj = json.loads(output.getvalue().strip())
        self.assertEqual(obj["type"], "event")
        self.assertEqual(obj["event"], "pty.data")


if __name__ == "__main__":
    unittest.main()
