from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_ide.app import AIIdeApp

logger = logging.getLogger(__name__)

MAX_EDITOR_FILE_BYTES = 1_048_576


class EditorService:
    def __init__(self, app: "AIIdeApp") -> None:
        self.app = app

    def snapshot(self, target: str) -> dict[str, object]:
        path = self.app.workspace_access.resolve_readable_path(target)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"File not found: {target}")

        size = path.stat().st_size
        if size > MAX_EDITOR_FILE_BYTES:
            raise ValueError(
                f"File too large to edit safely: {target} ({size} bytes > {MAX_EDITOR_FILE_BYTES} bytes)"
            )

        content = path.read_text(encoding="utf-8")
        normalized_target, managed = self._display_path(path, self.app.root)
        proposal = self._find_pending_proposal(normalized_target)
        logger.info("editor.snapshot target=%s size=%s managed=%s", normalized_target, size, managed)
        return {
            "kind": "editor_file",
            "target": target,
            "path": normalized_target,
            "managed": managed,
            "byte_size": size,
            "content": content,
            "proposal": proposal.to_dict() if proposal is not None else None,
            "rendered": self.app.render_review(proposal.proposal_id) if proposal is not None else None,
        }

    def save(self, target: str, content: str) -> dict[str, object]:
        self.app.save_editor_file(target, content)
        logger.info("editor.save target=%s", target)
        snapshot = self.snapshot(target)
        return {
            "kind": "editor_save",
            **{key: value for key, value in snapshot.items() if key != "kind"},
        }

    def stage(self, target: str, content: str) -> dict[str, object]:
        proposal = self.app.stage_review(
            target,
            content,
            session_id=self.app.sessions.current_session_id,
            agent_session_id=self.app.sessions.current_agent_session_id,
        )
        logger.info("editor.stage proposal_id=%s target=%s", proposal.proposal_id, proposal.target)
        return {
            "kind": "editor_stage",
            "proposal": proposal.to_dict(),
            "rendered": self.app.render_review(proposal.proposal_id),
        }

    def apply(self, proposal_id: str) -> dict[str, object]:
        proposal = self.app.apply_review(proposal_id)
        logger.info("editor.apply proposal_id=%s target=%s", proposal.proposal_id, proposal.target)
        return {
            "kind": "editor_apply",
            "proposal": proposal.to_dict(),
            "rendered": self.app.render_review(proposal.proposal_id),
        }

    def reject(self, proposal_id: str) -> dict[str, object]:
        proposal = self.app.reject_review(proposal_id)
        logger.info("editor.reject proposal_id=%s target=%s", proposal.proposal_id, proposal.target)
        return {
            "kind": "editor_reject",
            "proposal": proposal.to_dict(),
            "rendered": self.app.render_review(proposal.proposal_id),
        }

    @staticmethod
    def _display_path(path: Path, root: Path) -> tuple[str, bool]:
        try:
            return path.relative_to(root).as_posix(), True
        except ValueError:
            return path.as_posix(), False

    def _find_pending_proposal(self, target: str):
        proposals = self.app.list_review_proposals(status="pending", limit=500)
        for proposal in reversed(proposals):
            if proposal.target == target:
                return proposal
        return None
