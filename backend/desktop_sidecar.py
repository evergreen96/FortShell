"""Desktop sidecar process - reads JSON-line requests from stdin, writes responses to stdout.

Usage::

    python -m backend.desktop_sidecar --project-root /path/to/project [--runtime-root /path/to/runtime]

PTY output events are pushed to stdout as ``{"type":"event",...}`` lines,
interleaved with responses.  A threading.Lock serializes all stdout writes.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from backend.bootstrap import create_app
from backend.desktop_api_service import DesktopApiService
from backend.desktop_sidecar_protocol import (
    encode_error,
    encode_event,
    encode_response,
    parse_request,
)

logger = logging.getLogger(__name__)

# Methods that the sidecar can dispatch.
ALLOWED_METHODS = frozenset({
    "desktop_shell.snapshot",
    "workspace_panel.snapshot",
    "policy.deny",
    "policy.allow",
    "editor.file",
    "editor.save",
    "editor.stage",
    "editor.apply",
    "editor.reject",
    "review.render",
    "review.apply",
    "review.reject",
    "terminal.create",
    "terminal.run",
    "terminal.pty.write",
    "terminal.pty.resize",
})


class SidecarDispatcher:
    """Dispatches method calls to DesktopApiService."""

    def __init__(self, api: DesktopApiService) -> None:
        self.api = api

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method not in ALLOWED_METHODS:
            raise ValueError(f"Unknown method: {method}")

        if method == "desktop_shell.snapshot":
            return self.api.desktop_shell_snapshot(params.get("target", "."))

        if method == "workspace_panel.snapshot":
            return self.api.workspace_panel_snapshot(params.get("target", "."))

        if method == "policy.deny":
            rule = _require_str(params, "rule")
            return self.api.policy_deny(rule, target=params.get("target", "."))

        if method == "policy.allow":
            rule = _require_str(params, "rule")
            return self.api.policy_allow(rule, target=params.get("target", "."))

        if method == "editor.file":
            target = _require_str(params, "target")
            return self.api.editor_file(target)

        if method == "editor.save":
            target = _require_str(params, "target")
            content = _require_str(params, "content")
            return self.api.editor_save(target, content)

        if method == "editor.stage":
            target = _require_str(params, "target")
            content = _require_str(params, "content")
            return self.api.editor_stage(target, content)

        if method == "editor.apply":
            proposal_id = _require_str(params, "proposal_id")
            return self.api.editor_apply(proposal_id)

        if method == "editor.reject":
            proposal_id = _require_str(params, "proposal_id")
            return self.api.editor_reject(proposal_id)

        if method == "review.render":
            proposal_id = _require_str(params, "proposal_id")
            return self.api.review_render(proposal_id)

        if method == "review.apply":
            proposal_id = _require_str(params, "proposal_id")
            target = params.get("target", ".")
            return self.api.review_action("apply", proposal_id, target)

        if method == "review.reject":
            proposal_id = _require_str(params, "proposal_id")
            target = params.get("target", ".")
            return self.api.review_action("reject", proposal_id, target)

        if method == "terminal.create":
            return self.api.terminal_create(
                name=params.get("name"),
                transport=params.get("transport", "runner"),
                runner_mode=params.get("runner_mode"),
                io_mode=params.get("io_mode", "command"),
                profile_id=params.get("profile_id"),
            )

        if method == "terminal.run":
            terminal_id = _require_str(params, "terminal_id")
            command = _require_str(params, "command")
            return self.api.terminal_run(terminal_id, command)

        if method == "terminal.pty.write":
            terminal_id = _require_str(params, "terminal_id")
            data = _require_str(params, "data")
            return self.api.pty_write(terminal_id, data)

        if method == "terminal.pty.resize":
            terminal_id = _require_str(params, "terminal_id")
            cols = _require_int(params, "cols")
            rows = _require_int(params, "rows")
            return self.api.pty_resize(terminal_id, cols, rows)

        raise ValueError(f"Unhandled method: {method}")


class SidecarWriter:
    """Thread-safe stdout writer for interleaved responses and events."""

    def __init__(self, output=None) -> None:
        self._output = output or sys.stdout
        self._lock = threading.Lock()

    def write_line(self, line: str) -> None:
        with self._lock:
            self._output.write(line + "\n")
            self._output.flush()

    def write_response(self, req_id: str, result: Any) -> None:
        self.write_line(encode_response(req_id, result))

    def write_error(self, req_id: str, code: str, message: str) -> None:
        self.write_line(encode_error(req_id, code, message))

    def write_event(self, event: str, payload: dict[str, Any]) -> None:
        self.write_line(encode_event(event, payload))


def _classify_error(exc: Exception) -> str:
    """Map Python exception to an error code string."""
    if isinstance(exc, FileNotFoundError):
        return "file_not_found"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, KeyError):
        return "not_found"
    if isinstance(exc, ValueError):
        return "invalid_request"
    if isinstance(exc, RuntimeError):
        return "runtime_error"
    return "internal_error"


def _require_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected non-empty string field '{key}'")
    return value.strip()


def _require_int(params: dict[str, Any], key: str) -> int:
    value = params.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Expected integer field '{key}'")
    return value


def run_sidecar(project_root: Path, runtime_root: Path | None = None) -> None:
    """Main sidecar loop: read stdin lines, dispatch, write stdout lines."""
    # Force UTF-8 on stdin/stdout to avoid Windows cp949/cp1252 codec errors
    import io

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "buffer"):
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")

    app = create_app(project_root, runtime_root=runtime_root)
    api = DesktopApiService(app)
    dispatcher = SidecarDispatcher(api)
    writer = SidecarWriter()

    # Hook PTY output callback to emit events
    def on_pty_output(terminal_id: str, data: bytes) -> None:
        import base64

        writer.write_event("terminal.pty.data", {
            "terminal_id": terminal_id,
            "data_b64": base64.b64encode(data).decode("ascii"),
        })

    app.terminals.pty_manager._output_callback = on_pty_output

    logger.info("desktop_sidecar.started project_root=%s", project_root)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = parse_request(line)
            except (ValueError, KeyError) as exc:
                # Can't extract id, write error with empty id
                writer.write_error("", "parse_error", str(exc))
                continue

            logger.info("desktop_sidecar.request id=%s method=%s", request.id, request.method)
            try:
                result = dispatcher.dispatch(request.method, request.params)
                writer.write_response(request.id, result)
                logger.info("desktop_sidecar.response id=%s ok=True", request.id)
            except Exception as exc:
                code = _classify_error(exc)
                writer.write_error(request.id, code, str(exc))
                logger.warning(
                    "desktop_sidecar.dispatch_error method=%s id=%s code=%s error=%s",
                    request.method,
                    request.id,
                    code,
                    exc,
                )
    except (EOFError, BrokenPipeError):
        pass
    finally:
        app.close()
        logger.info("desktop_sidecar.stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI IDE Desktop Sidecar")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)
    run_sidecar(args.project_root, runtime_root=args.runtime_root)


if __name__ == "__main__":
    main()
