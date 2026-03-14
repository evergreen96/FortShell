from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from ai_ide.atomic_files import atomic_replace
from ai_ide.file_lock import advisory_lock
from ai_ide.workspace_visibility_models import VisibleWorkspaceState


class WorkspaceVisibilityStateStore:
    def __init__(self, path: Path | None) -> None:
        self.path = path.resolve() if path is not None else None
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock") if self.path is not None else None
        self._set_state_signature(None)
        self._set_cached_state(None)

    def load(self) -> VisibleWorkspaceState | None:
        if self.path is None or not self.path.exists():
            self._set_state_signature(None)
            self._set_cached_state(None)
            return None
        assert self.lock_path is not None
        with advisory_lock(self.lock_path):
            if not self.path.exists():
                self._set_state_signature(None)
                self._set_cached_state(None)
                return None
            current_signature = self._signature_for(self.path)
            if self._state_signature == current_signature:
                logger.debug("workspace_visibility_state_store.cache.hit path=%s", self.path)
                return self._cached_state
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        state = VisibleWorkspaceState(
            signature=str(payload.get("signature", "")),
            entry_count=int(payload.get("entry_count", 0)),
            policy_version=int(payload.get("policy_version", 0)),
        )
        self._set_state_signature(current_signature)
        self._set_cached_state(state)
        logger.debug("workspace_visibility_state_store.load path=%s entry_count=%s", self.path, state.entry_count)
        return state

    def save(self, state: VisibleWorkspaceState) -> None:
        if self.path is None:
            return
        assert self.lock_path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with advisory_lock(self.lock_path):
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
            atomic_replace(temp_path, self.path)
        self._set_state_signature(self._signature_for(self.path))
        self._set_cached_state(state)
        logger.debug("workspace_visibility_state_store.save path=%s entry_count=%s", self.path, state.entry_count)

    @staticmethod
    def _signature_for(path: Path) -> tuple[int, int]:
        stat_result = path.stat()
        return stat_result.st_mtime_ns, stat_result.st_size

    def _set_cached_state(self, state: VisibleWorkspaceState | None) -> None:
        self._cached_state = state

    def _set_state_signature(self, signature: tuple[int, int] | None) -> None:
        self._state_signature = signature
