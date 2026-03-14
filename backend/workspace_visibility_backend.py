from __future__ import annotations

from collections.abc import Callable
import logging
from threading import RLock
from typing import Protocol

from backend.workspace_visibility_models import VisibleWorkspaceState
from backend.workspace_visibility_source import WorkspaceVisibilitySource

logger = logging.getLogger(__name__)


class WorkspaceVisibilityBackend(Protocol):
    def current_state(self) -> VisibleWorkspaceState: ...

    def sync(self) -> VisibleWorkspaceState: ...

    def requires_command_boundary_poll(self) -> bool: ...

    def start(self, on_change: Callable[[VisibleWorkspaceState], None] | None = None) -> None: ...

    def close(self) -> None: ...


class WorkspaceVisibilityWatcher(Protocol):
    def start(self, on_event: Callable[[], None]) -> None: ...

    def close(self) -> None: ...


class PollingWorkspaceVisibilityBackend:
    def __init__(self, source: WorkspaceVisibilitySource) -> None:
        self.source = source
        self._set_state(self.source.current_state())
        self._set_on_change(None)

    def current_state(self) -> VisibleWorkspaceState:
        return self._state

    def sync(self) -> VisibleWorkspaceState:
        self._set_state(self.source.current_state())
        return self._state

    def requires_command_boundary_poll(self) -> bool:
        return True

    def start(self, on_change: Callable[[VisibleWorkspaceState], None] | None = None) -> None:
        self._set_on_change(on_change)
        logger.info("workspace_visibility_backend.polling.started")

    def close(self) -> None:
        self._set_on_change(None)
        logger.info("workspace_visibility_backend.polling.closed")

    def _set_state(self, state: VisibleWorkspaceState) -> None:
        self._state = state

    def _set_on_change(self, on_change: Callable[[VisibleWorkspaceState], None] | None) -> None:
        self._on_change = on_change


class EventDrivenWorkspaceVisibilityBackend:
    def __init__(
        self,
        source: WorkspaceVisibilitySource,
        watcher: WorkspaceVisibilityWatcher,
    ) -> None:
        self.source = source
        self.watcher = watcher
        self._lock = RLock()
        self._set_state(self.source.current_state())
        self._set_on_change(None)
        self._set_started(False)

    def current_state(self) -> VisibleWorkspaceState:
        with self._lock:
            return self._state

    def sync(self) -> VisibleWorkspaceState:
        with self._lock:
            self._set_state(self.source.current_state())
            return self._state

    def requires_command_boundary_poll(self) -> bool:
        return False

    def start(self, on_change: Callable[[VisibleWorkspaceState], None] | None = None) -> None:
        with self._lock:
            if self._started:
                self._set_on_change(on_change)
                return
            self._set_on_change(on_change)
            self.watcher.start(self._handle_event)
            self._set_started(True)
            logger.info("workspace_visibility_backend.event.started")

    def close(self) -> None:
        with self._lock:
            self.watcher.close()
            self._set_on_change(None)
            self._set_started(False)
            logger.info("workspace_visibility_backend.event.closed")

    def _handle_event(self) -> None:
        callback: Callable[[VisibleWorkspaceState], None] | None
        with self._lock:
            self._set_state(self.source.current_state())
            callback = self._on_change
            current = self._state
            logger.info(
                "workspace_visibility_backend.event.changed signature=%s entry_count=%s",
                current.signature,
                current.entry_count,
            )
        if callback is not None:
            callback(current)

    def _set_state(self, state: VisibleWorkspaceState) -> None:
        self._state = state

    def _set_on_change(self, on_change: Callable[[VisibleWorkspaceState], None] | None) -> None:
        self._on_change = on_change

    def _set_started(self, started: bool) -> None:
        self._started = started
