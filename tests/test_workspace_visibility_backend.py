from __future__ import annotations

import unittest

from ai_ide.workspace_visibility_backend import (
    EventDrivenWorkspaceVisibilityBackend,
    PollingWorkspaceVisibilityBackend,
)
from ai_ide.workspace_visibility_models import VisibleWorkspaceState


class WorkspaceVisibilityBackendTests(unittest.TestCase):
    def test_polling_backend_caches_current_state_until_sync(self) -> None:
        states = iter(
            [
                VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1),
                VisibleWorkspaceState(signature="sig-2", entry_count=2, policy_version=1),
            ]
        )

        class StubSource:
            def current_state(self) -> VisibleWorkspaceState:
                return next(states)

        backend = PollingWorkspaceVisibilityBackend(StubSource())

        self.assertEqual("sig-1", backend.current_state().signature)
        self.assertEqual("sig-1", backend.current_state().signature)
        self.assertTrue(backend.requires_command_boundary_poll())
        self.assertEqual("sig-2", backend.sync().signature)
        self.assertEqual("sig-2", backend.current_state().signature)

    def test_polling_backend_start_and_close_are_noop_lifecycle_hooks(self) -> None:
        state = VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1)

        class StubSource:
            def current_state(self) -> VisibleWorkspaceState:
                return state

        backend = PollingWorkspaceVisibilityBackend(StubSource())
        callback_calls: list[VisibleWorkspaceState] = []

        backend.start(callback_calls.append)
        self.assertEqual("sig-1", backend.current_state().signature)

        backend.close()
        self.assertEqual([], callback_calls)

    def test_event_driven_backend_refreshes_on_watcher_event_and_notifies_callback(self) -> None:
        states = iter(
            [
                VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1),
                VisibleWorkspaceState(signature="sig-2", entry_count=2, policy_version=1),
            ]
        )

        class StubSource:
            def current_state(self) -> VisibleWorkspaceState:
                return next(states)

        class StubWatcher:
            def __init__(self) -> None:
                self.callback = None

            def start(self, on_event) -> None:
                self.callback = on_event

            def close(self) -> None:
                self.callback = None

            def emit(self) -> None:
                assert self.callback is not None
                self.callback()

        watcher = StubWatcher()
        backend = EventDrivenWorkspaceVisibilityBackend(StubSource(), watcher)
        published: list[VisibleWorkspaceState] = []

        backend.start(published.append)
        self.assertEqual("sig-1", backend.current_state().signature)
        self.assertFalse(backend.requires_command_boundary_poll())

        watcher.emit()

        self.assertEqual("sig-2", backend.current_state().signature)
        self.assertEqual(["sig-2"], [state.signature for state in published])

    def test_event_driven_backend_close_delegates_to_watcher(self) -> None:
        state = VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1)

        class StubSource:
            def current_state(self) -> VisibleWorkspaceState:
                return state

        class StubWatcher:
            def __init__(self) -> None:
                self.closed = False

            def start(self, on_event) -> None:
                return None

            def close(self) -> None:
                self.closed = True

        watcher = StubWatcher()
        backend = EventDrivenWorkspaceVisibilityBackend(StubSource(), watcher)
        backend.start()

        backend.close()

        self.assertTrue(watcher.closed)


if __name__ == "__main__":
    unittest.main()
