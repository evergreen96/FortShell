from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import logging
import uuid
from pathlib import Path

from ai_ide.models import WriteProposal
from ai_ide.policy import PolicyEngine
from ai_ide.review_state_store import ReviewStateStore
from ai_ide.workspace_access_service import WorkspaceAccessService

MAX_REVIEW_FILE_BYTES = 1_048_576
MAX_REVIEW_PROPOSAL_HISTORY = 500
logger = logging.getLogger(__name__)


class ReviewManager:
    def __init__(
        self,
        root: Path,
        policy_engine: PolicyEngine,
        state_store: ReviewStateStore | None = None,
        workspace_access: WorkspaceAccessService | None = None,
    ) -> None:
        self.root = root.resolve()
        self.policy_engine = policy_engine
        self.state_store = state_store or ReviewStateStore(None)
        self.workspace_access = workspace_access or WorkspaceAccessService(self.root, self.policy_engine)
        snapshot = self.state_store.load()
        self._set_proposals(self._normalize_loaded_proposals(snapshot.proposals))

    def stage_write(
        self,
        target: str,
        proposed_text: str,
        *,
        session_id: str,
        agent_session_id: str,
    ) -> WriteProposal:
        path = self.workspace_access.resolve_allowed_path(target)
        if path.exists() and not path.is_file():
            raise IsADirectoryError(f"Target is not a file: {target}")
        base_text = self._read_reviewable_text(path, target=target)
        proposal = WriteProposal(
            proposal_id=self._new_proposal_id(),
            target=self._target_label(path),
            session_id=session_id,
            agent_session_id=agent_session_id,
            created_at=self._now(),
            updated_at=self._now(),
            status="pending",
            base_sha256=self._sha256(base_text) if base_text is not None else None,
            base_text=base_text,
            proposed_text=proposed_text,
        )
        self._append_proposal(proposal)
        self._persist()
        logger.info("review.staged proposal_id=%s target=%s", proposal.proposal_id, proposal.target)
        return proposal

    def replace_proposals(self, proposals: list[WriteProposal]) -> None:
        self._set_proposals(self._normalize_loaded_proposals(proposals))

    def list_proposals(self, *, status: str | None = None, limit: int = 20) -> list[WriteProposal]:
        proposals = self.proposals
        if status is not None:
            proposals = [item for item in proposals if item.status == status]
        return proposals[-limit:]

    def count_proposals(self, *, status: str | None = None) -> int:
        if status is None:
            return len(self.proposals)
        return sum(1 for item in self.proposals if item.status == status)

    def get_proposal(self, proposal_id: str) -> WriteProposal:
        for proposal in self.proposals:
            if proposal.proposal_id == proposal_id:
                return proposal
        raise ValueError(f"Unknown review proposal: {proposal_id}")

    def render_proposal(self, proposal_id: str) -> str:
        proposal = self.get_proposal(proposal_id)
        diff_lines = difflib.unified_diff(
            [] if proposal.base_text is None else proposal.base_text.splitlines(),
            proposal.proposed_text.splitlines(),
            fromfile=f"a/{proposal.target}",
            tofile=f"b/{proposal.target}",
            lineterm="",
        )
        diff_text = "\n".join(diff_lines) or "(no diff)"
        return (
            f"proposal_id={proposal.proposal_id} target={proposal.target} status={proposal.status} "
            f"session_id={proposal.session_id} agent_session_id={proposal.agent_session_id}\n"
            f"{diff_text}"
        )

    def apply_proposal(self, proposal_id: str) -> WriteProposal:
        proposal = self.get_proposal(proposal_id)
        if proposal.status != "pending":
            raise ValueError(f"Proposal is not pending: {proposal.proposal_id}")

        path = self.workspace_access.resolve_allowed_path(proposal.target)
        if path.exists() and not path.is_file():
            updated = self._replace_proposal(proposal, status="conflict")
            logger.warning("review.conflict proposal_id=%s target=%s reason=not_file", updated.proposal_id, updated.target)
            raise RuntimeError(f"Proposal conflicted with current file state: {updated.proposal_id}")
        current_text = self._read_reviewable_text(path, target=proposal.target)
        current_sha256 = self._sha256(current_text) if current_text is not None else None
        if current_sha256 != proposal.base_sha256:
            updated = self._replace_proposal(proposal, status="conflict")
            logger.warning("review.conflict proposal_id=%s target=%s reason=sha_mismatch", updated.proposal_id, updated.target)
            raise RuntimeError(f"Proposal conflicted with current file state: {updated.proposal_id}")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(proposal.proposed_text, encoding="utf-8")
        updated = self._replace_proposal(proposal, status="applied")
        logger.info("review.applied proposal_id=%s target=%s", updated.proposal_id, updated.target)
        return updated

    def reject_proposal(self, proposal_id: str) -> WriteProposal:
        proposal = self.get_proposal(proposal_id)
        if proposal.status != "pending":
            raise ValueError(f"Proposal is not pending: {proposal.proposal_id}")
        updated = self._replace_proposal(proposal, status="rejected")
        logger.info("review.rejected proposal_id=%s target=%s", updated.proposal_id, updated.target)
        return updated

    def _replace_proposal(self, proposal: WriteProposal, *, status: str) -> WriteProposal:
        updated = WriteProposal(
            proposal_id=proposal.proposal_id,
            target=proposal.target,
            session_id=proposal.session_id,
            agent_session_id=proposal.agent_session_id,
            created_at=proposal.created_at,
            updated_at=self._now(),
            status=status,
            base_sha256=proposal.base_sha256,
            base_text=proposal.base_text,
            proposed_text=proposal.proposed_text,
        )
        proposals = list(self.proposals)
        for index, item in enumerate(self.proposals):
            if item.proposal_id == proposal.proposal_id:
                proposals = self._replace_proposal_at(proposals, index, updated)
                self._set_proposals(proposals)
                self._persist()
                return updated
        raise ValueError(f"Unknown review proposal: {proposal.proposal_id}")

    def _persist(self) -> None:
        self.state_store.save(self.proposals)

    def _set_proposals(self, proposals: list[WriteProposal]) -> None:
        self.proposals = self._trim_proposals(proposals)

    def _append_proposal(self, proposal: WriteProposal) -> None:
        proposals = list(self.proposals)
        proposals.append(proposal)
        self._set_proposals(proposals)

    @staticmethod
    def _replace_proposal_at(
        proposals: list[WriteProposal],
        index: int,
        proposal: WriteProposal,
    ) -> list[WriteProposal]:
        updated = list(proposals)
        updated[index] = proposal
        return updated

    @staticmethod
    def _trim_proposals(proposals: list[WriteProposal]) -> list[WriteProposal]:
        if len(proposals) <= MAX_REVIEW_PROPOSAL_HISTORY:
            return proposals
        removed = len(proposals) - MAX_REVIEW_PROPOSAL_HISTORY
        logger.info("review.trim removed=%s", removed)
        return list(proposals[-MAX_REVIEW_PROPOSAL_HISTORY:])

    def _normalize_loaded_proposals(self, proposals: list[WriteProposal]) -> list[WriteProposal]:
        normalized: list[WriteProposal] = []
        for proposal in proposals[-MAX_REVIEW_PROPOSAL_HISTORY:]:
            try:
                self.workspace_access.resolve_allowed_path(proposal.target)
            except (PermissionError, ValueError):
                logger.info(
                    "review.drop_invalid_loaded_proposal proposal_id=%s target=%s",
                    proposal.proposal_id,
                    proposal.target,
                )
                continue
            normalized.append(proposal)
        return normalized

    def _target_label(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _new_proposal_id() -> str:
        return f"rev-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _sha256(text: str | None) -> str | None:
        if text is None:
            return None
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _now() -> str:
        return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    @staticmethod
    def _read_reviewable_text(path: Path, *, target: str) -> str | None:
        if not path.exists():
            return None
        size = path.stat().st_size
        if size > MAX_REVIEW_FILE_BYTES:
            logger.warning("review.blocked oversized target=%s size=%s", target, size)
            raise ValueError(
                f"File too large to review safely: {target} ({size} bytes > {MAX_REVIEW_FILE_BYTES} bytes)"
            )
        return path.read_text(encoding="utf-8")
