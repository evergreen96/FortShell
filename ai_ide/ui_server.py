from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from ai_ide.bootstrap import create_app
from ai_ide.desktop_api_service import DesktopApiService

if TYPE_CHECKING:
    from ai_ide.app import AIIdeApp


STATIC_ROOT = Path(__file__).with_name("ui_web")
logger = logging.getLogger(__name__)
API_ALLOW_ORIGIN = "*"


class WorkspacePanelHttpServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], app: "AIIdeApp") -> None:
        super().__init__(server_address, build_handler(app))
        self.app = app

    def server_close(self) -> None:
        try:
            self.app.close()
        finally:
            super().server_close()


def build_handler(app: "AIIdeApp"):
    api = DesktopApiService(app)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AIIdeWorkspacePanel/0.1"

        def do_OPTIONS(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_api_cors_headers()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/editor/file":
                query = parse_qs(parsed.query)
                target = query.get("target", [""])[0].strip()
                if not target:
                    self._send_json({"error": "Expected non-empty query parameter 'target'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.editor_file(target))
                except FileNotFoundError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                except PermissionError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/review/render":
                query = parse_qs(parsed.query)
                proposal_id = query.get("proposal_id", [""])[0].strip()
                if not proposal_id:
                    self._send_json({"error": "Expected non-empty query parameter 'proposal_id'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.review_render(proposal_id))
                except (ValueError, RuntimeError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/desktop-shell":
                query = parse_qs(parsed.query)
                target = query.get("target", ["."])[0]
                logger.info("ui.desktop_shell target=%s", target)
                try:
                    self._send_json(api.desktop_shell_snapshot(target))
                except FileNotFoundError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                except PermissionError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/workspace-panel":
                query = parse_qs(parsed.query)
                target = query.get("target", ["."])[0]
                logger.info("ui.workspace_panel target=%s", target)
                try:
                    self._send_json(api.workspace_panel_snapshot(target))
                except FileNotFoundError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                except PermissionError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return

            if parsed.path == "/api/terminal/pty/stream":
                query = parse_qs(parsed.query)
                terminal_id = query.get("terminal_id", [""])[0].strip()
                if not terminal_id:
                    self._send_json({"error": "Expected query parameter 'terminal_id'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._stream_pty_output(terminal_id)
                return

            self._serve_static(parsed.path)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/editor/stage":
                target = payload.get("target")
                content = payload.get("content")
                if not isinstance(target, str) or not target.strip():
                    self._send_json({"error": "Expected non-empty string field 'target'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(content, str):
                    self._send_json({"error": "Expected string field 'content'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.editor_stage(target.strip(), content))
                except (ValueError, RuntimeError, PermissionError, FileNotFoundError, IsADirectoryError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/editor/save":
                target = payload.get("target")
                content = payload.get("content")
                if not isinstance(target, str) or not target.strip():
                    self._send_json({"error": "Expected non-empty string field 'target'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(content, str):
                    self._send_json({"error": "Expected string field 'content'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.editor_save(target.strip(), content))
                except (ValueError, RuntimeError, PermissionError, FileNotFoundError, IsADirectoryError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/editor/apply":
                proposal_id = payload.get("proposal_id")
                if not isinstance(proposal_id, str) or not proposal_id.strip():
                    self._send_json({"error": "Expected non-empty string field 'proposal_id'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.editor_apply(proposal_id.strip()))
                except (ValueError, RuntimeError, PermissionError, FileNotFoundError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/editor/reject":
                proposal_id = payload.get("proposal_id")
                if not isinstance(proposal_id, str) or not proposal_id.strip():
                    self._send_json({"error": "Expected non-empty string field 'proposal_id'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.editor_reject(proposal_id.strip()))
                except (ValueError, RuntimeError, PermissionError, FileNotFoundError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path in {"/api/review/apply", "/api/review/reject"}:
                proposal_id = payload.get("proposal_id")
                target = payload.get("target", ".")
                if not isinstance(proposal_id, str) or not proposal_id.strip():
                    self._send_json({"error": "Expected non-empty string field 'proposal_id'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(target, str):
                    self._send_json({"error": "Expected string field 'target'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                action = "apply" if parsed.path.endswith("/apply") else "reject"
                try:
                    self._send_json(api.review_action(action, proposal_id.strip(), target))
                except (ValueError, RuntimeError, PermissionError, FileNotFoundError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/terminal/create":
                name = payload.get("name")
                transport = payload.get("transport", "runner")
                runner_mode = payload.get("runner_mode")
                io_mode = payload.get("io_mode", "command")
                if name is not None and not isinstance(name, str):
                    self._send_json({"error": "Expected 'name' to be a string"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(transport, str):
                    self._send_json({"error": "Expected 'transport' to be a string"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if runner_mode is not None and not isinstance(runner_mode, str):
                    self._send_json({"error": "Expected 'runner_mode' to be a string"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(io_mode, str):
                    self._send_json({"error": "Expected 'io_mode' to be a string"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.terminal_create(
                        name=name or None,
                        transport=transport,
                        runner_mode=runner_mode,
                        io_mode=io_mode,
                    ))
                except (PermissionError, ValueError, RuntimeError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/terminal/run":
                terminal_id = payload.get("terminal_id")
                command = payload.get("command")
                if not isinstance(terminal_id, str) or not terminal_id.strip():
                    self._send_json({"error": "Expected non-empty string field 'terminal_id'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(command, str) or not command.strip():
                    self._send_json({"error": "Expected non-empty string field 'command'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.terminal_run(terminal_id.strip(), command.strip()))
                except (PermissionError, ValueError, KeyError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/terminal/pty/write":
                terminal_id = payload.get("terminal_id")
                data = payload.get("data")
                if not isinstance(terminal_id, str) or not terminal_id.strip():
                    self._send_json({"error": "Expected non-empty string field 'terminal_id'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(data, str):
                    self._send_json({"error": "Expected string field 'data'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.pty_write(terminal_id.strip(), data))
                except (ValueError, KeyError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/terminal/pty/resize":
                terminal_id = payload.get("terminal_id")
                cols = payload.get("cols")
                rows = payload.get("rows")
                if not isinstance(terminal_id, str) or not terminal_id.strip():
                    self._send_json({"error": "Expected non-empty string field 'terminal_id'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(cols, int) or not isinstance(rows, int):
                    self._send_json({"error": "Expected integer fields 'cols' and 'rows'"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if cols < 1 or rows < 1:
                    self._send_json({"error": "cols and rows must be >= 1"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(api.pty_resize(terminal_id.strip(), cols, rows))
                except (ValueError, KeyError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/policy/deny":
                target = payload.get("target", ".")
                rule = payload.get("rule", "")
                if not isinstance(target, str) or not isinstance(rule, str) or not rule.strip():
                    self._send_json(
                        {"error": "Expected JSON body with non-empty string field 'rule'"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                logger.info("ui.policy_deny target=%s rule=%s", target, rule.strip())
                try:
                    self._send_json(api.policy_deny(rule.strip(), target=target))
                except PermissionError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/policy/allow":
                target = payload.get("target", ".")
                rule = payload.get("rule", "")
                if not isinstance(target, str) or not isinstance(rule, str) or not rule.strip():
                    self._send_json(
                        {"error": "Expected JSON body with non-empty string field 'rule'"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                logger.info("ui.policy_allow target=%s rule=%s", target, rule.strip())
                try:
                    self._send_json(api.policy_allow(rule.strip(), target=target))
                except PermissionError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return

            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _read_json_body(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                raise ValueError("Invalid JSON body") from None
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object body")
            return payload

        def _serve_static(self, raw_path: str) -> None:
            relative = "index.html" if raw_path in {"", "/"} else raw_path.lstrip("/")
            candidate = (STATIC_ROOT / relative).resolve()
            try:
                candidate.relative_to(STATIC_ROOT.resolve())
            except ValueError:
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            if not candidate.exists() or not candidate.is_file():
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return

            content_type = {
                ".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json; charset=utf-8",
            }.get(candidate.suffix, "application/octet-stream")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(candidate.read_bytes())

        def _stream_pty_output(self, terminal_id: str) -> None:
            """SSE endpoint: streams PTY output via DesktopApiService.pty_stream."""
            try:
                stream = api.pty_stream(terminal_id)
            except (KeyError, ValueError) as exc:
                status = HTTPStatus.NOT_FOUND if isinstance(exc, KeyError) else HTTPStatus.BAD_REQUEST
                self._send_json({"error": str(exc)}, status=status)
                return

            self.send_response(HTTPStatus.OK)
            self._send_api_cors_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            try:
                for chunk in stream:
                    event_type = chunk.get("event", "")
                    if event_type == "terminal.pty.data":
                        self.wfile.write(f"data: {chunk['data_b64']}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    elif event_type == "terminal.pty.close":
                        self.wfile.write(f"event: close\ndata: {chunk['reason']}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        break
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                pass

        def _send_json(self, payload: dict[str, object], *, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self._send_api_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_api_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", API_ALLOW_ORIGIN)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    return Handler


def serve_workspace_panel(
    project_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    runtime_root: Path | None = None,
) -> WorkspacePanelHttpServer:
    app = create_app(project_root, runtime_root=runtime_root)
    return WorkspacePanelHttpServer((host, port), app)


def main() -> None:
    server = serve_workspace_panel(Path.cwd())
    address = server.server_address
    print(f"workspace panel available at http://{address[0]}:{address[1]}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
