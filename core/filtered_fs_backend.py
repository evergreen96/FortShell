"""FilteredFSBackend — common interface for platform-specific filtered filesystems.

This is the core contract that all OS backends must implement.
The protection model: protected files are VISIBLE but ACCESS-DENIED (EACCES).

See docs/backend-api-spec.md for the full specification.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.policy import PolicyEngine
    from core.workspace_access_service import WorkspaceAccessService


@dataclass(frozen=True)
class FilteredFSStatus:
    """Status of the filtered filesystem backend."""

    backend: str  # "dokan", "fuse", "macfuse", "mirror"
    driver_installed: bool
    mounted: bool
    mount_point: str | None
    degraded: bool  # True if using fallback instead of preferred backend
    detail: str  # human-readable explanation


@dataclass(frozen=True)
class MountResult:
    """Result of a mount operation."""

    mount_root: Path
    session_id: str
    policy_version: int


class FilteredFSBackend(ABC):
    """Abstract interface for filtered filesystem backends.

    Every platform backend (Dokan, FUSE, macFUSE) must implement this.
    The backend provides a filesystem view where:
    - Protected files are VISIBLE (listed in readdir, stat returns metadata)
    - Protected files are ACCESS-DENIED (open/read/write/chmod/rm → EACCES)
    - Allowed files pass through to the original project directory
    - Writes to allowed files update the original immediately
    """

    project_root: Path
    policy_engine: "PolicyEngine"
    workspace_access: "WorkspaceAccessService"

    @abstractmethod
    def mount(self, session_id: str) -> MountResult:
        """Mount the filtered filesystem view.

        Returns the mount point path where terminals should set their CWD.
        """
        ...

    @abstractmethod
    def unmount(self) -> None:
        """Unmount the filtered filesystem view."""
        ...

    @abstractmethod
    def update_policy(self) -> None:
        """Notify the backend that policy rules changed.

        For live backends (Dokan/FUSE): protection changes take effect immediately.
        No terminal restart needed.
        """
        ...

    @abstractmethod
    def status(self) -> FilteredFSStatus:
        """Return current backend status for UI display."""
        ...

    @property
    @abstractmethod
    def mount_root(self) -> Path | None:
        """Current mount point, or None if not mounted."""
        ...

    @property
    def root(self) -> Path:
        """Original project root."""
        return self.project_root

    def is_protected(self, path: Path) -> bool:
        """Check if a path should be protected (access-denied).

        Default implementation uses workspace_access.inspect_path().
        Backends can override for performance.
        """
        from core.internal import is_internal_path

        resolved = path.resolve(strict=False)
        if is_internal_path(self.project_root, resolved):
            return True
        return not self.policy_engine.is_allowed(resolved)
