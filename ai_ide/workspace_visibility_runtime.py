from __future__ import annotations

import logging
from pathlib import Path

from ai_ide.platforms import PlatformAdapter
from ai_ide.workspace_visibility_backend import (
    EventDrivenWorkspaceVisibilityBackend,
    PollingWorkspaceVisibilityBackend,
    WorkspaceVisibilityBackend,
    WorkspaceVisibilityWatcher,
)
from ai_ide.workspace_visibility_source import WorkspaceVisibilitySource

logger = logging.getLogger(__name__)


def build_workspace_visibility_backend(
    source: WorkspaceVisibilitySource,
    *,
    watcher: WorkspaceVisibilityWatcher | None = None,
) -> WorkspaceVisibilityBackend:
    if watcher is None:
        logger.info("workspace_visibility_runtime.backend mode=polling")
        return PollingWorkspaceVisibilityBackend(source)
    logger.info("workspace_visibility_runtime.backend mode=event-driven")
    return EventDrivenWorkspaceVisibilityBackend(source, watcher)


def resolve_workspace_visibility_watcher(
    platform_adapter: PlatformAdapter,
    *,
    project_root: Path,
    runtime_root: Path,
    override: WorkspaceVisibilityWatcher | None = None,
) -> WorkspaceVisibilityWatcher | None:
    if override is not None:
        logger.info("workspace_visibility_runtime.watcher source=override")
        return override
    watcher = platform_adapter.workspace_visibility_watcher(project_root, runtime_root)
    logger.info("workspace_visibility_runtime.watcher source=platform configured=%s", watcher is not None)
    return watcher
