from __future__ import annotations

import logging
from threading import RLock
from typing import Callable

from backend.events import EventBus
from backend.workspace_visibility_backend import WorkspaceVisibilityBackend
from backend.workspace_visibility_models import VisibleWorkspaceState
from backend.workspace_visibility_state_store import WorkspaceVisibilityStateStore

logger = logging.getLogger(__name__)


class WorkspaceVisibilityMonitor:
    def __init__(
        self,
        backend: WorkspaceVisibilityBackend,
        *,
        event_bus: EventBus | None,
        execution_session_id_provider: Callable[[], str],
        state_store: WorkspaceVisibilityStateStore | None = None,
    ) -> None:
        self.backend = backend
        self.event_bus = event_bus
        self.execution_session_id_provider = execution_session_id_provider
        self.state_store = state_store
        self._lock = RLock()
        self._set_state(self.backend.current_state())
        self._set_baseline_state(self.state_store.load() if self.state_store is not None else None)
        if self._baseline_state is None:
            self._set_baseline_state(self._state)
            if self.state_store is not None:
                self.state_store.save(self._state)
        self._set_started(False)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self.backend.start(self._handle_backend_change)
            self._set_started(True)
            logger.info("workspace_visibility_monitor.started")

    def close(self) -> None:
        with self._lock:
            self.backend.close()
            self._set_started(False)
            logger.info("workspace_visibility_monitor.closed")

    def poll(self) -> VisibleWorkspaceState | None:
        return self._publish_if_changed(
            current=self.backend.sync(),
            reason="poll",
            origin="external_or_unknown",
        )

    def requires_command_boundary_poll(self) -> bool:
        return self.backend.requires_command_boundary_poll()

    def record_change(self, reason: str, *, target: str | None = None) -> VisibleWorkspaceState | None:
        return self._publish_if_changed(
            current=self.backend.sync(),
            reason=reason,
            origin="app",
            target=target,
        )

    def current_state(self) -> VisibleWorkspaceState:
        with self._lock:
            self._set_state(self.backend.current_state())
            return self._state

    def _handle_backend_change(self, current: VisibleWorkspaceState) -> None:
        self._publish_if_changed(current=current, reason="watch", origin="backend")

    def _publish_if_changed(
        self,
        *,
        current: VisibleWorkspaceState,
        reason: str,
        origin: str,
        target: str | None = None,
    ) -> VisibleWorkspaceState | None:
        with self._lock:
            previous = self._baseline_state
            self._set_state(current)
            if current == previous:
                return None
            self._set_baseline_state(current)
            if self.state_store is not None:
                self.state_store.save(current)
            logger.info(
                "workspace_visibility_monitor.changed reason=%s origin=%s signature=%s previous_signature=%s entry_count=%s",
                reason,
                origin,
                current.signature,
                previous.signature,
                current.entry_count,
            )
            if self.event_bus is not None:
                self.event_bus.publish(
                    "workspace.visible.changed",
                    source_type="workspace",
                    source_id="visible-tree",
                    execution_session_id=self.execution_session_id_provider(),
                    payload={
                        "reason": reason,
                        "origin": origin,
                        "target": target,
                        "previous_signature": previous.signature,
                        "signature": current.signature,
                        "entry_count": current.entry_count,
                        "policy_version": current.policy_version,
                    },
                )
            return current

    def _set_state(self, state: VisibleWorkspaceState) -> None:
        self._state = state

    def _set_baseline_state(self, state: VisibleWorkspaceState) -> None:
        self._baseline_state = state

    def _set_started(self, started: bool) -> None:
        self._started = started
