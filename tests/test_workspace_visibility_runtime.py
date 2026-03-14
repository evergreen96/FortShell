from __future__ import annotations

import unittest
from pathlib import Path

from ai_ide.workspace_visibility_backend import (
    EventDrivenWorkspaceVisibilityBackend,
    PollingWorkspaceVisibilityBackend,
)
from ai_ide.workspace_visibility_runtime import (
    build_workspace_visibility_backend,
    resolve_workspace_visibility_watcher,
)
from ai_ide.workspace_visibility_models import VisibleWorkspaceState


class WorkspaceVisibilityRuntimeTests(unittest.TestCase):
    def test_build_workspace_visibility_backend_defaults_to_polling(self) -> None:
        class StubSource:
            def current_state(self) -> VisibleWorkspaceState:
                return VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1)

        backend = build_workspace_visibility_backend(StubSource())
        self.assertIsInstance(backend, PollingWorkspaceVisibilityBackend)

    def test_build_workspace_visibility_backend_uses_event_backend_when_watcher_is_present(self) -> None:
        class StubSource:
            def current_state(self) -> VisibleWorkspaceState:
                return VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1)

        class StubWatcher:
            def start(self, on_event) -> None:
                return None

            def close(self) -> None:
                return None

        backend = build_workspace_visibility_backend(StubSource(), watcher=StubWatcher())
        self.assertIsInstance(backend, EventDrivenWorkspaceVisibilityBackend)

    def test_resolve_workspace_visibility_watcher_prefers_override(self) -> None:
        class StubPlatform:
            def workspace_visibility_watcher(self, project_root, runtime_root):
                return "platform-watcher"

        watcher = resolve_workspace_visibility_watcher(
            StubPlatform(),
            project_root=Path("."),
            runtime_root=Path("."),
            override="override-watcher",
        )

        self.assertEqual("override-watcher", watcher)

    def test_resolve_workspace_visibility_watcher_uses_platform_default(self) -> None:
        class StubPlatform:
            def workspace_visibility_watcher(self, project_root, runtime_root):
                return "platform-watcher"

        watcher = resolve_workspace_visibility_watcher(
            StubPlatform(),
            project_root=Path("."),
            runtime_root=Path("."),
        )

        self.assertEqual("platform-watcher", watcher)


if __name__ == "__main__":
    unittest.main()
