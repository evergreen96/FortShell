from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from ai_ide.atomic_files import atomic_replace
from ai_ide.file_lock import advisory_lock
from ai_ide.workspace_models import WorkspaceIndexEntry, WorkspaceIndexSnapshot


class WorkspaceIndexStateStore:
    def __init__(self, path: Path | None) -> None:
        self.path = path.resolve() if path is not None else None
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock") if self.path is not None else None
        self._set_state_signature(None)
        self._set_cached_snapshot(WorkspaceIndexSnapshot(policy_version=0, signature="", entries=[]))

    def load(self) -> WorkspaceIndexSnapshot:
        if self.path is None or not self.path.exists():
            self._set_state_signature(None)
            self._set_cached_snapshot(WorkspaceIndexSnapshot(policy_version=0, signature="", entries=[]))
            return WorkspaceIndexSnapshot(policy_version=0, signature="", entries=[])
        assert self.lock_path is not None
        with advisory_lock(self.lock_path):
            if not self.path.exists():
                self._set_state_signature(None)
                self._set_cached_snapshot(WorkspaceIndexSnapshot(policy_version=0, signature="", entries=[]))
                return WorkspaceIndexSnapshot(policy_version=0, signature="", entries=[])
            current_signature = self._signature_for(self.path)
            if self._state_signature == current_signature:
                logger.debug("workspace_index_state_store.cache.hit path=%s", self.path)
                return WorkspaceIndexSnapshot(
                    policy_version=self._cached_snapshot.policy_version,
                    signature=self._cached_snapshot.signature,
                    entries=list(self._cached_snapshot.entries),
                )
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        snapshot = WorkspaceIndexSnapshot(
            policy_version=int(payload.get("policy_version", 0)),
            signature=str(payload.get("signature", "")),
            entries=[
                WorkspaceIndexEntry(
                    path=str(item["path"]),
                    is_dir=bool(item["is_dir"]),
                    size=int(item["size"]),
                    modified_ns=int(item["modified_ns"]),
                )
                for item in payload.get("entries", [])
            ],
        )
        self._set_state_signature(current_signature)
        self._set_cached_snapshot(snapshot)
        logger.debug("workspace_index_state_store.load path=%s entries=%s", self.path, len(snapshot.entries))
        return snapshot

    def save(self, snapshot: WorkspaceIndexSnapshot) -> None:
        if self.path is None:
            return
        assert self.lock_path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "policy_version": snapshot.policy_version,
            "signature": snapshot.signature,
            "entries": [entry.to_dict() for entry in snapshot.entries],
        }
        with advisory_lock(self.lock_path):
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            atomic_replace(temp_path, self.path)
        self._set_state_signature(self._signature_for(self.path))
        self._set_cached_snapshot(
            WorkspaceIndexSnapshot(
                policy_version=snapshot.policy_version,
                signature=snapshot.signature,
                entries=list(snapshot.entries),
            )
        )
        logger.debug("workspace_index_state_store.save path=%s entries=%s", self.path, len(snapshot.entries))

    @staticmethod
    def _signature_for(path: Path) -> tuple[int, int]:
        stat_result = path.stat()
        return stat_result.st_mtime_ns, stat_result.st_size

    def _set_cached_snapshot(self, snapshot: WorkspaceIndexSnapshot) -> None:
        self._cached_snapshot = snapshot

    def _set_state_signature(self, signature: tuple[int, int] | None) -> None:
        self._state_signature = signature
