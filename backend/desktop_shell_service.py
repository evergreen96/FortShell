from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app import AIIdeApp

logger = logging.getLogger(__name__)


class DesktopShellService:
    def __init__(self, app: "AIIdeApp") -> None:
        self.app = app

    def snapshot(
        self,
        target: str = ".",
    ) -> dict[str, object]:
        workspace_panel = self.app.workspace_panel.snapshot(target)
        terminal_inspections = self.app.list_terminal_inspections()
        terminal_payloads = [inspection.to_dict() for inspection in terminal_inspections]
        active_terminal = next(
            (payload for payload in terminal_payloads if payload["status"] == "active"),
            terminal_payloads[0] if terminal_payloads else None,
        )

        logger.info(
            "desktop.shell.snapshot target=%s terminal_count=%s",
            target,
            len(terminal_payloads),
        )
        return {
            "kind": "desktop_shell",
            "target": target,
            "workspace_panel": workspace_panel,
            "terminals": {
                "count": len(terminal_payloads),
                "active_terminal_id": active_terminal["terminal_id"] if active_terminal is not None else None,
                "items": terminal_payloads,
            },
            "terminal_profiles": {
                "items": self.app.list_terminal_profiles(),
                "default_profile_id": self.app.terminal_profiles.default_profile_id(),
                "config_path": str(self.app.terminal_profiles.config_path),
            },
        }
