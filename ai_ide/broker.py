from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import List

from ai_ide.broker_state_store import BrokerStateStore
from ai_ide.models import AuditEvent, UsageMetrics
from ai_ide.policy import PolicyEngine
from ai_ide.session import SessionManager
from ai_ide.workspace_access_service import NOT_UNDER_ROOT_ERROR, WorkspaceAccessService
from ai_ide.workspace_catalog_service import WorkspaceCatalogService

logger = logging.getLogger(__name__)


MAX_READ_FILE_BYTES = 1_048_576
MAX_AUDIT_ENTRIES = 500


class ToolBroker:
    def __init__(
        self,
        root: Path,
        policy_engine: PolicyEngine,
        session_manager: SessionManager,
        state_store: BrokerStateStore | None = None,
        *,
        workspace_access: WorkspaceAccessService | None = None,
        workspace_catalog: WorkspaceCatalogService | None = None,
    ) -> None:
        self.root = root.resolve()
        self.policy_engine = policy_engine
        self.session_manager = session_manager
        self.state_store = state_store or BrokerStateStore(None)
        snapshot = self.state_store.load()
        self._set_metrics(snapshot.metrics)
        self._set_audit_log([])
        self._set_audit_log(snapshot.audit_log)
        self.workspace_access = workspace_access or WorkspaceAccessService(self.root, self.policy_engine)
        self.workspace_catalog = workspace_catalog or WorkspaceCatalogService(self.root, self.workspace_access)

    def list_dir(self, target: str) -> List[str]:
        self.metrics.increment_list()
        try:
            path = self.workspace_access.resolve_readable_path(target)
            visible = [entry.display_name for entry in self.workspace_catalog.list_dir(path)]
            self._record("list", path, allowed=True, detail=f"items={len(visible)}")
            return visible
        except PermissionError as exc:
            self._record_permission_denial("list", target, exc)
            raise
        finally:
            self._persist_state()

    def read_file(self, target: str) -> str:
        self.metrics.increment_read()
        try:
            path = self.workspace_access.resolve_readable_path(target)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"File not found: {target}")

            size = path.stat().st_size
            if size > MAX_READ_FILE_BYTES:
                self._record("read", path, allowed=False, detail=f"file too large: {size} bytes")
                raise ValueError(
                    f"File too large to read safely: {target} ({size} bytes > {MAX_READ_FILE_BYTES} bytes)"
                )

            text = path.read_text(encoding="utf-8")
            self._record("read", path, allowed=True, detail=f"bytes={len(text)}")
            return text
        except PermissionError as exc:
            self._record_permission_denial("read", target, exc)
            raise
        finally:
            self._persist_state()

    def write_file(self, target: str, text: str, *, action: str = "write") -> None:
        self.metrics.increment_write()
        try:
            path = self.workspace_access.resolve_allowed_path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self._record(action, path, allowed=True, detail=f"bytes={len(text)}")
        except PermissionError as exc:
            self._record_permission_denial(action, target, exc)
            raise
        finally:
            self._persist_state()

    def grep(self, pattern: str, target_dir: str = ".") -> List[str]:
        self.metrics.increment_grep()
        try:
            root = self.workspace_access.resolve_readable_path(target_dir)
            matches = [match.format_cli() for match in self.workspace_catalog.grep(pattern, root)]
            self._record("grep", root, allowed=True, detail=f"matches={len(matches)}")
            return matches
        except PermissionError as exc:
            self._record_permission_denial("grep", target_dir, exc)
            raise
        finally:
            self._persist_state()

    def list_audit_events(self, limit: int = 20, *, allowed: bool | None = None) -> list[AuditEvent]:
        events = self.audit_log
        if allowed is not None:
            events = [event for event in events if event.allowed is allowed]
        return events[-limit:]

    def record_runtime_action(
        self,
        action: str,
        target: str,
        *,
        allowed: bool,
        detail: str = "",
        count_as_write: bool = False,
    ) -> None:
        if count_as_write:
            self.metrics.increment_write()
        try:
            path = (self.root / target).resolve(strict=False)
            self._record(action, path, allowed=allowed, detail=detail)
        finally:
            self._persist_state()

    def _record_permission_denial(self, action: str, target: str, exc: PermissionError) -> None:
        logger.warning("permission_denied action=%s target=%s reason=%s", action, target, exc)
        path = (self.root / target).resolve(strict=False)
        detail = str(exc)
        if detail == NOT_UNDER_ROOT_ERROR:
            self._record("resolve", path, allowed=False, detail="path not under workspace root")
            return
        if detail.startswith("Blocked internal path:"):
            self._record(action, path, allowed=False, detail="blocked internal runtime path")
            return
        if detail.startswith("Blocked by policy:"):
            self._record(action, path, allowed=False, detail="denied by policy")
            return
        self._record(action, path, allowed=False, detail=detail)

    def _record(self, action: str, target: Path, allowed: bool, detail: str = "") -> None:
        if not allowed:
            self.metrics.increment_blocked()
        self._append_audit(
            AuditEvent(
                timestamp=self._now(),
                session_id=self.session_manager.current_session_id,
                action=action,
                target=str(target),
                allowed=allowed,
                detail=detail,
            )
        )

    def _append_audit(self, event: AuditEvent) -> None:
        self._set_audit_log([*self.audit_log, event])

    def _persist_state(self) -> None:
        self.state_store.save(self.metrics, self.audit_log)

    @staticmethod
    def _now() -> str:
        return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    @staticmethod
    def _trim_audit(audit_log: List[AuditEvent]) -> List[AuditEvent]:
        if len(audit_log) <= MAX_AUDIT_ENTRIES:
            return audit_log
        removed = len(audit_log) - MAX_AUDIT_ENTRIES
        logger.info("broker.audit.trim removed=%s", removed)
        return list(audit_log[-MAX_AUDIT_ENTRIES:])

    def _set_audit_log(self, audit_log: List[AuditEvent] | tuple[AuditEvent, ...]) -> None:
        self.audit_log = self._trim_audit(list(audit_log))

    def _set_metrics(self, metrics: UsageMetrics) -> None:
        self.metrics = metrics
