"""Factory for creating the appropriate FilteredFSBackend.

Selects the best available backend for the current platform.
Falls back to MirrorBackend for tests and unsupported environments.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from core.filtered_fs_backend import FilteredFSBackend, FilteredFSStatus, MountResult

if TYPE_CHECKING:
    from core.policy import PolicyEngine
    from core.workspace_access_service import WorkspaceAccessService

logger = logging.getLogger(__name__)


class MirrorFilteredFSBackend(FilteredFSBackend):
    """Fallback backend for tests and environments without a real FS driver.

    Uses the legacy copy-based projection. Degraded mode.
    """

    def __init__(
        self,
        project_root: Path,
        policy_engine: "PolicyEngine",
        runtime_root: Path,
        *,
        workspace_access: "WorkspaceAccessService",
    ) -> None:
        self.project_root = project_root.resolve()
        self.policy_engine = policy_engine
        self.workspace_access = workspace_access
        from backend.projection import ProjectedWorkspaceManager
        self._projection = ProjectedWorkspaceManager(
            self.project_root,
            self.policy_engine,
            runtime_root,
            workspace_access=self.workspace_access,
        )
        self._mounted = False
        self._mount_path: Path | None = None

    def mount(self, session_id: str) -> MountResult:
        manifest = self._projection.materialize(session_id)
        self._mounted = True
        self._mount_path = manifest.root
        return MountResult(
            mount_root=manifest.root,
            session_id=session_id,
            policy_version=self.policy_engine.state.version,
        )

    def unmount(self) -> None:
        self._mounted = False
        self._mount_path = None

    def update_policy(self) -> None:
        logger.info("filtered_fs.mirror_policy_updated version=%s", self.policy_engine.state.version)

    def status(self) -> FilteredFSStatus:
        return FilteredFSStatus(
            backend="mirror",
            driver_installed=False,
            mounted=self._mounted,
            mount_point=str(self._mount_path) if self._mount_path else None,
            degraded=True,
            detail="Using copy-based fallback. Install Dokan for filesystem-level protection.",
        )

    @property
    def mount_root(self) -> Path | None:
        return self._mount_path

    # -- Compatibility methods for existing app.py code --

    def materialize(self, session_id: str):
        """Legacy compat: delegates to mount()."""
        result = self.mount(session_id)
        from backend.projection import ProjectionManifest
        return ProjectionManifest(
            session_id=session_id,
            root=result.mount_root,
            file_count=0,
            directory_count=0,
            policy_version=result.policy_version,
        )

    def projection_root(self, session_id: str) -> Path:
        """Legacy compat."""
        return self._projection.projection_root(session_id)

    def cleanup_stale(self, current_session_id: str) -> None:
        """Legacy compat."""
        self._projection.cleanup_stale(current_session_id)

    @property
    def internal_runtime_dir(self) -> Path:
        """Legacy compat."""
        return self._projection.internal_runtime_dir


def create_filtered_fs_backend(
    project_root: Path,
    policy_engine: "PolicyEngine",
    runtime_root: Path,
    *,
    workspace_access: "WorkspaceAccessService",
) -> FilteredFSBackend:
    """Create the best available backend for the current platform."""
    preference = os.environ.get("AI_IDE_FILTERED_FS_BACKEND", "auto").strip().lower()

    if preference == "mirror" or os.environ.get("PYTEST_CURRENT_TEST"):
        return MirrorFilteredFSBackend(
            project_root, policy_engine, runtime_root, workspace_access=workspace_access,
        )

    if os.name == "nt" and preference in {"auto", "dokan"} and _dokan_supported():
        try:
            from backend.windows.dokan_fs_backend import DokanFilteredFSBackend
            return DokanFilteredFSBackend(
                project_root, policy_engine, runtime_root, workspace_access=workspace_access,
            )
        except Exception as exc:
            if preference == "dokan":
                raise
            logger.warning("filtered_fs.dokan_unavailable fallback=mirror error=%s", exc)

    return MirrorFilteredFSBackend(
        project_root, policy_engine, runtime_root, workspace_access=workspace_access,
    )


def _dokan_supported() -> bool:
    if os.name != "nt":
        return False
    dll_name = ctypes.util.find_library("dokan2") or "dokan2.dll"
    try:
        ctypes.WinDLL(dll_name)
        return True
    except OSError:
        return False
