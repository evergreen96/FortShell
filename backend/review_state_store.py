from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from core.atomic_files import atomic_replace
from core.file_lock import advisory_lock
from core.models import WriteProposal


@dataclass(frozen=True)
class ReviewStateSnapshot:
    proposals: list[WriteProposal]


class ReviewStateStore:
    def __init__(self, path: Path | None) -> None:
        self.path = path.resolve() if path is not None else None
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock") if self.path is not None else None
        self._set_state_signature(None)
        self._set_cached_snapshot(ReviewStateSnapshot(proposals=[]))

    def load(self) -> ReviewStateSnapshot:
        if self.path is None or not self.path.exists():
            self._set_state_signature(None)
            self._set_cached_snapshot(ReviewStateSnapshot(proposals=[]))
            return ReviewStateSnapshot(proposals=[])
        assert self.lock_path is not None
        with advisory_lock(self.lock_path):
            if not self.path.exists():
                self._set_state_signature(None)
                self._set_cached_snapshot(ReviewStateSnapshot(proposals=[]))
                return ReviewStateSnapshot(proposals=[])
            current_signature = self._signature_for(self.path)
            if self._state_signature == current_signature:
                logger.debug("review_state_store.cache.hit path=%s", self.path)
                return ReviewStateSnapshot(proposals=list(self._cached_snapshot.proposals))
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        proposals = [WriteProposal(**item) for item in payload.get("proposals", [])]
        snapshot = ReviewStateSnapshot(proposals=proposals)
        self._set_state_signature(current_signature)
        self._set_cached_snapshot(snapshot)
        logger.debug("review_state_store.load path=%s proposals=%s", self.path, len(proposals))
        return snapshot

    def save(self, proposals: list[WriteProposal]) -> None:
        if self.path is None:
            return
        assert self.lock_path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"proposals": [asdict(item) for item in proposals]}
        with advisory_lock(self.lock_path):
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            atomic_replace(temp_path, self.path)
        self._set_state_signature(self._signature_for(self.path))
        self._set_cached_snapshot(ReviewStateSnapshot(proposals=list(proposals)))
        logger.debug("review_state_store.save path=%s proposals=%s", self.path, len(proposals))

    @staticmethod
    def _signature_for(path: Path) -> tuple[int, int]:
        stat_result = path.stat()
        return stat_result.st_mtime_ns, stat_result.st_size

    def _set_cached_snapshot(self, snapshot: ReviewStateSnapshot) -> None:
        self._cached_snapshot = snapshot

    def _set_state_signature(self, signature: tuple[int, int] | None) -> None:
        self._state_signature = signature
