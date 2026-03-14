from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from core.atomic_files import atomic_replace
from core.file_lock import advisory_lock
from core.internal import INTERNAL_PROJECT_METADATA_DIR_NAME, INTERNAL_POLICY_STATE_FILENAME
from core.models import PolicyState


class PolicyStateStore:
    def __init__(self, root: Path, path: Path | None = None) -> None:
        self.root = root.resolve()
        self.path = (path or self.root / INTERNAL_PROJECT_METADATA_DIR_NAME / INTERNAL_POLICY_STATE_FILENAME).resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._set_state_signature(None)
        self._set_cached_state(PolicyState(deny_globs=[]))

    def load(self) -> PolicyState:
        if not self.path.exists():
            self._set_state_signature(None)
            self._set_cached_state(PolicyState(deny_globs=[]))
            return PolicyState(deny_globs=[])
        with advisory_lock(self.lock_path):
            if not self.path.exists():
                self._set_state_signature(None)
                self._set_cached_state(PolicyState(deny_globs=[]))
                return PolicyState(deny_globs=[])
            current_signature = self._signature_for(self.path)
            if self._state_signature == current_signature:
                logger.debug("policy_state_store.cache.hit path=%s", self.path)
                return PolicyState(
                    deny_globs=list(self._cached_state.deny_globs),
                    version=self._cached_state.version,
                )
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        deny_globs = [str(rule) for rule in payload.get("deny_globs", [])]
        version = int(payload.get("version", 1))
        state = PolicyState(deny_globs=deny_globs, version=max(1, version))
        self._set_state_signature(current_signature)
        self._set_cached_state(state)
        logger.debug("policy_state_store.load path=%s deny_rules=%s", self.path, len(state.deny_globs))
        return state

    def save(self, state: PolicyState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "deny_globs": list(state.deny_globs),
            "version": int(state.version),
        }
        with advisory_lock(self.lock_path):
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            atomic_replace(temp_path, self.path)
        self._set_state_signature(self._signature_for(self.path))
        self._set_cached_state(PolicyState(deny_globs=list(state.deny_globs), version=state.version))
        logger.debug("policy_state_store.save path=%s deny_rules=%s version=%s", self.path, len(state.deny_globs), state.version)

    @staticmethod
    def _signature_for(path: Path) -> tuple[int, int]:
        stat_result = path.stat()
        return stat_result.st_mtime_ns, stat_result.st_size

    def _set_cached_state(self, state: PolicyState) -> None:
        self._cached_state = state

    def _set_state_signature(self, signature: tuple[int, int] | None) -> None:
        self._state_signature = signature
