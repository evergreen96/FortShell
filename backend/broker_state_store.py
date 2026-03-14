from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from core.atomic_files import atomic_replace
from core.file_lock import advisory_lock
from core.models import AuditEvent, UsageMetrics


@dataclass(frozen=True)
class BrokerStateSnapshot:
    metrics: UsageMetrics
    audit_log: list[AuditEvent]


class BrokerStateStore:
    def __init__(self, path: Path | None) -> None:
        self.path = path.resolve() if path is not None else None
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock") if self.path is not None else None
        self._set_state_signature(None)
        self._set_cached_snapshot(BrokerStateSnapshot(metrics=UsageMetrics(), audit_log=[]))

    def load(self) -> BrokerStateSnapshot:
        if self.path is None or not self.path.exists():
            self._set_state_signature(None)
            self._set_cached_snapshot(BrokerStateSnapshot(metrics=UsageMetrics(), audit_log=[]))
            return BrokerStateSnapshot(metrics=UsageMetrics(), audit_log=[])
        assert self.lock_path is not None
        with advisory_lock(self.lock_path):
            if not self.path.exists():
                self._set_state_signature(None)
                self._set_cached_snapshot(BrokerStateSnapshot(metrics=UsageMetrics(), audit_log=[]))
                return BrokerStateSnapshot(metrics=UsageMetrics(), audit_log=[])
            current_signature = self._signature_for(self.path)
            if self._state_signature == current_signature:
                logger.debug("broker_state_store.cache.hit path=%s", self.path)
                return BrokerStateSnapshot(
                    metrics=UsageMetrics(**asdict(self._cached_snapshot.metrics)),
                    audit_log=list(self._cached_snapshot.audit_log),
                )
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        metrics = UsageMetrics(**payload.get("metrics", {}))
        audit_log = [AuditEvent(**item) for item in payload.get("audit_log", [])]
        snapshot = BrokerStateSnapshot(metrics=metrics, audit_log=audit_log)
        self._set_state_signature(current_signature)
        self._set_cached_snapshot(snapshot)
        logger.debug("broker_state_store.load path=%s audit_entries=%s", self.path, len(audit_log))
        return snapshot

    def save(self, metrics: UsageMetrics, audit_log: list[AuditEvent]) -> None:
        if self.path is None:
            return
        assert self.lock_path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metrics": asdict(metrics),
            "audit_log": [asdict(event) for event in audit_log],
        }
        with advisory_lock(self.lock_path):
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            atomic_replace(temp_path, self.path)
        self._set_state_signature(self._signature_for(self.path))
        self._set_cached_snapshot(
            BrokerStateSnapshot(
                metrics=UsageMetrics(**asdict(metrics)),
                audit_log=list(audit_log),
            )
        )
        logger.debug("broker_state_store.save path=%s audit_entries=%s", self.path, len(audit_log))

    @staticmethod
    def _signature_for(path: Path) -> tuple[int, int]:
        stat_result = path.stat()
        return stat_result.st_mtime_ns, stat_result.st_size

    def _set_cached_snapshot(self, snapshot: BrokerStateSnapshot) -> None:
        self._cached_snapshot = snapshot

    def _set_state_signature(self, signature: tuple[int, int] | None) -> None:
        self._state_signature = signature
