"""Desktop sidecar JSON-line protocol.

Wire format
-----------
Request:   {"type":"request","id":"req-001","method":"editor.file","params":{"target":"src/app.py"}}
Response:  {"type":"response","id":"req-001","ok":true,"result":{...}}
Error:     {"type":"response","id":"req-001","ok":false,"error":{"code":"file_not_found","message":"..."}}
Event:     {"type":"event","event":"terminal.pty.data","payload":{"terminal_id":"term-123","data_b64":"..."}}

All messages are single JSON lines terminated by ``\\n``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SidecarRequest:
    id: str
    method: str
    params: dict[str, Any]


def parse_request(line: str) -> SidecarRequest:
    """Parse a single JSON line into a SidecarRequest."""
    obj = json.loads(line)
    if obj.get("type") != "request":
        raise ValueError(f"Expected type 'request', got {obj.get('type')!r}")
    req_id = obj.get("id")
    if not isinstance(req_id, str) or not req_id:
        raise ValueError("Missing or empty 'id' field")
    method = obj.get("method")
    if not isinstance(method, str) or not method:
        raise ValueError("Missing or empty 'method' field")
    params = obj.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("'params' must be an object")
    return SidecarRequest(id=req_id, method=method, params=params)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

def encode_response(req_id: str, result: Any) -> str:
    """Encode a success response as a JSON line."""
    return _to_json_line({
        "type": "response",
        "id": req_id,
        "ok": True,
        "result": result,
    })


def encode_error(req_id: str, code: str, message: str) -> str:
    """Encode an error response as a JSON line."""
    return _to_json_line({
        "type": "response",
        "id": req_id,
        "ok": False,
        "error": {"code": code, "message": message},
    })


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

def encode_event(event: str, payload: dict[str, Any]) -> str:
    """Encode a push event as a JSON line."""
    return _to_json_line({
        "type": "event",
        "event": event,
        "payload": payload,
    })


# ---------------------------------------------------------------------------
# Response parsing (for Rust/frontend consumers)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SidecarResponse:
    id: str
    ok: bool
    result: Any | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class SidecarEvent:
    event: str
    payload: dict[str, Any]


def parse_message(line: str) -> SidecarResponse | SidecarEvent:
    """Parse a response or event line from the sidecar."""
    obj = json.loads(line)
    msg_type = obj.get("type")
    if msg_type == "response":
        error = obj.get("error") or {}
        return SidecarResponse(
            id=obj["id"],
            ok=obj.get("ok", False),
            result=obj.get("result"),
            error_code=error.get("code"),
            error_message=error.get("message"),
        )
    if msg_type == "event":
        return SidecarEvent(
            event=obj["event"],
            payload=obj.get("payload", {}),
        )
    raise ValueError(f"Unknown message type: {msg_type!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_json_line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)
