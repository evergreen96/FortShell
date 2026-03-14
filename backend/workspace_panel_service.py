from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.commands.policy import add_policy_rule, policy_snapshot, remove_policy_rule
from core.policy_rule_helpers import deny_rule_for_target
from core.workspace_access_service import NOT_UNDER_ROOT_ERROR

if TYPE_CHECKING:
    from backend.app import AIIdeApp

logger = logging.getLogger(__name__)


class WorkspacePanelService:
    def __init__(self, app: "AIIdeApp") -> None:
        self.app = app

    def snapshot(self, target: str = ".") -> dict[str, object]:
        entries = self.app.tree_workspace_entries(target)
        index_snapshot = self.app.load_workspace_index_snapshot()
        stale_reasons = self.app.workspace_index_stale_reasons(index_snapshot)
        logger.info("workspace.panel.snapshot target=%s entry_count=%s", target, len(entries))
        return {
            "kind": "workspace_panel",
            "target": target,
            "workspace": {
                "entries": [self._entry_payload(entry) for entry in entries],
            },
            "policy": policy_snapshot(self.app),
            "session": {
                "execution_session_id": self.app.sessions.current_session_id,
                "agent_session_id": self.app.sessions.current_agent_session_id,
            },
            "workspace_index": {
                "policy_version": index_snapshot.policy_version,
                "stale": bool(stale_reasons),
                "stale_reasons": stale_reasons,
                "entry_count": index_snapshot.entry_count,
                "file_count": index_snapshot.file_count,
                "directory_count": index_snapshot.directory_count,
            },
        }

    def add_deny_rule(self, rule: str, *, target: str = ".") -> dict[str, object]:
        self._ensure_policy_managed_target(target)
        result = add_policy_rule(self.app, rule)
        logger.info("workspace.panel.policy_add rule=%s target=%s changed=%s", rule, target, result["changed"])
        return {
            "kind": "workspace_panel_policy_change",
            "change": result,
            "panel": self.snapshot(target),
        }

    def remove_deny_rule(self, rule: str, *, target: str = ".") -> dict[str, object]:
        self._ensure_policy_managed_target(target)
        result = remove_policy_rule(self.app, rule)
        logger.info(
            "workspace.panel.policy_remove rule=%s target=%s changed=%s", rule, target, result["changed"]
        )
        return {
            "kind": "workspace_panel_policy_change",
            "change": result,
            "panel": self.snapshot(target),
        }

    def _entry_payload(self, entry) -> dict[str, object]:
        payload = entry.to_dict()
        try:
            payload["suggested_deny_rule"] = deny_rule_for_target(entry.path, is_dir=entry.is_dir)
        except ValueError:
            payload["suggested_deny_rule"] = None
        return payload

    def _ensure_policy_managed_target(self, target: str) -> None:
        try:
            self.app.workspace_access.resolve_under_root(target)
        except PermissionError as exc:
            if str(exc) != NOT_UNDER_ROOT_ERROR:
                raise
            raise PermissionError("Policy mutation is scoped to workspace-internal targets only") from exc
