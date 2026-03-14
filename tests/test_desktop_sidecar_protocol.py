"""Tests for desktop sidecar JSON-line protocol."""

from __future__ import annotations

import json
import unittest

from ai_ide.desktop_sidecar_protocol import (
    SidecarEvent,
    SidecarRequest,
    SidecarResponse,
    encode_error,
    encode_event,
    encode_response,
    parse_message,
    parse_request,
)


class TestParseRequest(unittest.TestCase):
    def test_valid_request(self) -> None:
        line = json.dumps({
            "type": "request",
            "id": "req-001",
            "method": "editor.file",
            "params": {"target": "src/app.py"},
        })
        req = parse_request(line)
        self.assertEqual(req.id, "req-001")
        self.assertEqual(req.method, "editor.file")
        self.assertEqual(req.params, {"target": "src/app.py"})

    def test_missing_type_raises(self) -> None:
        line = json.dumps({"id": "req-001", "method": "test"})
        with self.assertRaises(ValueError):
            parse_request(line)

    def test_missing_id_raises(self) -> None:
        line = json.dumps({"type": "request", "method": "test"})
        with self.assertRaises(ValueError):
            parse_request(line)

    def test_missing_method_raises(self) -> None:
        line = json.dumps({"type": "request", "id": "req-001"})
        with self.assertRaises(ValueError):
            parse_request(line)

    def test_empty_params_defaults_to_dict(self) -> None:
        line = json.dumps({"type": "request", "id": "req-001", "method": "test"})
        req = parse_request(line)
        self.assertEqual(req.params, {})

    def test_wrong_type_raises(self) -> None:
        line = json.dumps({"type": "event", "id": "req-001", "method": "test"})
        with self.assertRaises(ValueError):
            parse_request(line)


class TestEncodeResponse(unittest.TestCase):
    def test_success_response(self) -> None:
        line = encode_response("req-001", {"kind": "editor_file"})
        obj = json.loads(line)
        self.assertEqual(obj["type"], "response")
        self.assertEqual(obj["id"], "req-001")
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["result"], {"kind": "editor_file"})

    def test_error_response(self) -> None:
        line = encode_error("req-002", "file_not_found", "File not found")
        obj = json.loads(line)
        self.assertEqual(obj["type"], "response")
        self.assertEqual(obj["id"], "req-002")
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["error"]["code"], "file_not_found")
        self.assertEqual(obj["error"]["message"], "File not found")


class TestEncodeEvent(unittest.TestCase):
    def test_event(self) -> None:
        line = encode_event("terminal.pty.data", {"terminal_id": "term-1", "data_b64": "SGVsbG8="})
        obj = json.loads(line)
        self.assertEqual(obj["type"], "event")
        self.assertEqual(obj["event"], "terminal.pty.data")
        self.assertEqual(obj["payload"]["terminal_id"], "term-1")


class TestParseMessage(unittest.TestCase):
    def test_parse_success_response(self) -> None:
        line = encode_response("req-001", {"kind": "ok"})
        msg = parse_message(line)
        self.assertIsInstance(msg, SidecarResponse)
        assert isinstance(msg, SidecarResponse)
        self.assertTrue(msg.ok)
        self.assertEqual(msg.id, "req-001")
        self.assertEqual(msg.result, {"kind": "ok"})

    def test_parse_error_response(self) -> None:
        line = encode_error("req-002", "bad", "oops")
        msg = parse_message(line)
        self.assertIsInstance(msg, SidecarResponse)
        assert isinstance(msg, SidecarResponse)
        self.assertFalse(msg.ok)
        self.assertEqual(msg.error_code, "bad")

    def test_parse_event(self) -> None:
        line = encode_event("pty.data", {"x": 1})
        msg = parse_message(line)
        self.assertIsInstance(msg, SidecarEvent)
        assert isinstance(msg, SidecarEvent)
        self.assertEqual(msg.event, "pty.data")
        self.assertEqual(msg.payload, {"x": 1})

    def test_unknown_type_raises(self) -> None:
        line = json.dumps({"type": "unknown"})
        with self.assertRaises(ValueError):
            parse_message(line)

    def test_roundtrip(self) -> None:
        """Encode then parse should recover the same data."""
        original_result = {"kind": "test", "value": 42}
        line = encode_response("req-999", original_result)
        msg = parse_message(line)
        assert isinstance(msg, SidecarResponse)
        self.assertEqual(msg.result, original_result)


if __name__ == "__main__":
    unittest.main()
