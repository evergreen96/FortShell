"""Unified desktop API service — shared by ui_server.py and desktop_sidecar.py.

Each method validates inputs, delegates to the app layer, and returns a
plain dict ready for JSON serialization.  No HTTP, no Tauri — pure logic.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from backend.app import AIIdeApp

from backend.desktop_shell_service import DesktopShellService
from backend.editor_service import EditorService
from backend.workspace_panel_service import WorkspacePanelService

logger = logging.getLogger(__name__)

PTY_STREAM_POLL_INTERVAL = 0.05  # 50 ms


class DesktopApiService:
    """Transport-agnostic API surface for the desktop UI."""

    def __init__(self, app: "AIIdeApp") -> None:
        self.app = app
        self.panel = WorkspacePanelService(app)
        self.desktop = DesktopShellService(app)
        self.editor = EditorService(app)

    # ------------------------------------------------------------------
    # desktop_shell
    # ------------------------------------------------------------------

    def desktop_shell_snapshot(self, target: str = ".") -> dict[str, Any]:
        return self.desktop.snapshot(target)

    def workspace_panel_snapshot(self, target: str = ".") -> dict[str, Any]:
        return self.panel.snapshot(target)

    # ------------------------------------------------------------------
    # policy
    # ------------------------------------------------------------------

    def policy_deny(self, rule: str, target: str = ".") -> dict[str, Any]:
        return self.panel.add_deny_rule(rule, target=target)

    def policy_allow(self, rule: str, target: str = ".") -> dict[str, Any]:
        return self.panel.remove_deny_rule(rule, target=target)

    # ------------------------------------------------------------------
    # editor
    # ------------------------------------------------------------------

    def editor_file(self, target: str) -> dict[str, Any]:
        return self.editor.snapshot(target)

    def editor_save(self, target: str, content: str) -> dict[str, Any]:
        return self.editor.save(target, content)

    def editor_stage(self, target: str, content: str) -> dict[str, Any]:
        return self.editor.stage(target, content)

    def editor_apply(self, proposal_id: str) -> dict[str, Any]:
        return self.editor.apply(proposal_id)

    def editor_reject(self, proposal_id: str) -> dict[str, Any]:
        return self.editor.reject(proposal_id)

    # ------------------------------------------------------------------
    # review (apply/reject with desktop refresh)
    # ------------------------------------------------------------------

    def review_render(self, proposal_id: str) -> dict[str, Any]:
        return {
            "kind": "review_render",
            "proposal_id": proposal_id,
            "content": self.app.render_review(proposal_id),
        }

    def review_action(self, action: str, proposal_id: str, target: str = ".") -> dict[str, Any]:
        proposal = (
            self.app.apply_review(proposal_id)
            if action == "apply"
            else self.app.reject_review(proposal_id)
        )
        return {
            "kind": "review_action",
            "action": action,
            "proposal": proposal.to_dict(),
            "desktop": self.desktop.snapshot(target),
        }

    # ------------------------------------------------------------------
    # terminal
    # ------------------------------------------------------------------

    def terminal_create(
        self,
        *,
        name: str | None = None,
        transport: str = "runner",
        runner_mode: str | None = None,
        io_mode: str = "command",
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        terminal = self.app.create_terminal(
            name=name,
            transport=transport,
            runner_mode=runner_mode,
            io_mode=io_mode,
            profile_id=profile_id,
        )
        inspection = self.app.inspect_terminal(terminal.terminal_id)
        return {
            "kind": "terminal_create",
            "terminal": inspection.to_dict(),
        }

    def terminal_run(self, terminal_id: str, command: str) -> dict[str, Any]:
        output = self.app.run_terminal_command(terminal_id, command)
        inspection = self.app.inspect_terminal(terminal_id)
        return {
            "kind": "terminal_run",
            "terminal": inspection.to_dict(),
            "output": output,
        }

    # ------------------------------------------------------------------
    # pty
    # ------------------------------------------------------------------

    def pty_write(self, terminal_id: str, data: str) -> dict[str, Any]:
        self.app.write_to_pty(terminal_id, data)
        return {"kind": "pty_write", "ok": True}

    def pty_resize(self, terminal_id: str, cols: int, rows: int) -> dict[str, Any]:
        self.app.resize_pty(terminal_id, cols, rows)
        return {"kind": "pty_resize", "ok": True}

    def pty_stream(self, terminal_id: str) -> Iterator[dict[str, Any]]:
        """Yield PTY output chunks as dicts.  Caller decides transport (SSE / event)."""
        inspection = self.app.inspect_terminal(terminal_id)
        if inspection.to_dict().get("io_mode") != "pty":
            raise ValueError("Terminal is not in PTY mode")

        while True:
            try:
                output = self.app.get_pty_output(terminal_id)
            except KeyError:
                yield {"event": "terminal.pty.close", "terminal_id": terminal_id, "reason": "terminal_destroyed"}
                break

            if output:
                yield {
                    "event": "terminal.pty.data",
                    "terminal_id": terminal_id,
                    "data_b64": base64.b64encode(output).decode("ascii"),
                }

            if not self.app.terminals.pty_manager.is_alive(terminal_id):
                # Drain remaining
                try:
                    remaining = self.app.get_pty_output(terminal_id)
                    if remaining:
                        yield {
                            "event": "terminal.pty.data",
                            "terminal_id": terminal_id,
                            "data_b64": base64.b64encode(remaining).decode("ascii"),
                        }
                except KeyError:
                    pass
                yield {"event": "terminal.pty.close", "terminal_id": terminal_id, "reason": "process_exited"}
                break

            time.sleep(PTY_STREAM_POLL_INTERVAL)
