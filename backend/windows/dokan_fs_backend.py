"""Dokan-based FilteredFSBackend implementation for Windows.

Manages the Dokan mount lifecycle. Uses DokanFilteredOperations for the
actual filesystem operations (protection model).
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import string
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from core.filtered_fs_backend import FilteredFSBackend, FilteredFSStatus, MountResult

if TYPE_CHECKING:
    from core.policy import PolicyEngine
    from core.workspace_access_service import WorkspaceAccessService

logger = logging.getLogger(__name__)


class DokanFilteredFSBackend(FilteredFSBackend):
    """Windows Dokan backend using fusepy in a managed thread."""

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
        self._lock = threading.RLock()
        self._mount_thread: threading.Thread | None = None
        self._mount_point: str | None = None
        self._session_id: str | None = None
        self._mount_error: BaseException | None = None
        self._dokanctl = _resolve_dokanctl()

    def mount(self, session_id: str) -> MountResult:
        self.unmount()
        mount_point = self._preferred_mount_point()
        self._mount_error = None

        thread = threading.Thread(
            target=self._run_mount,
            args=(mount_point,),
            name=f"dokan-mount-{session_id}",
            daemon=True,
        )
        with self._lock:
            self._mount_point = mount_point
            self._session_id = session_id
            self._mount_thread = thread
        thread.start()

        deadline = time.time() + 5
        while time.time() < deadline:
            if self._mount_error is not None:
                self.unmount()
                raise RuntimeError(f"Dokan mount failed: {self._mount_error}") from self._mount_error
            if self._as_path(mount_point).exists():
                return MountResult(
                    mount_root=self._as_path(mount_point),
                    session_id=session_id,
                    policy_version=self.policy_engine.state.version,
                )
            time.sleep(0.05)

        self.unmount()
        raise RuntimeError("Timed out waiting for Dokan mount")

    def unmount(self) -> None:
        with self._lock:
            mount_point = self._mount_point
            thread = self._mount_thread
            self._mount_point = None
            self._session_id = None
            self._mount_thread = None

        if mount_point:
            self._request_unmount(mount_point)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def update_policy(self) -> None:
        logger.info("filtered_fs.policy_updated version=%s", self.policy_engine.state.version)

    def status(self) -> FilteredFSStatus:
        mounted = self._mount_point is not None
        error = self._mount_error
        if error is not None:
            detail = f"Dokan mount failed: {error}"
        elif mounted:
            detail = f"Dokan filesystem active at {self._mount_point}"
        else:
            detail = "Dokan ready, not yet mounted"
        return FilteredFSStatus(
            backend="dokan",
            driver_installed=True,
            mounted=mounted,
            mount_point=self._mount_point,
            degraded=False,
            detail=detail,
        )

    @property
    def mount_root(self) -> Path | None:
        mp = self._mount_point
        return self._as_path(mp) if mp else None

    def _run_mount(self, mount_point: str) -> None:
        try:
            from fuse import FUSE
            from backend.windows.dokan_backend import DokanFilteredOperations

            operations = DokanFilteredOperations(self.project_root, self.workspace_access)
            FUSE(operations, mount_point, foreground=True, nothreads=False)
        except BaseException as exc:
            self._mount_error = exc
            logger.exception("filtered_fs.mount_failed mount_point=%s", mount_point)

    def _preferred_mount_point(self) -> str:
        override = os.environ.get("AI_IDE_FILTERED_MOUNT_POINT", "").strip()
        if override:
            return override
        used_mask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in reversed(string.ascii_uppercase):
            if letter in {"A", "B"}:
                continue
            if not (used_mask & (1 << (ord(letter) - ord("A")))):
                return f"{letter}:"
        raise RuntimeError("No free drive letters available")

    @staticmethod
    def _as_path(mount_point: str) -> Path:
        return Path(f"{mount_point}\\") if mount_point.endswith(":") else Path(mount_point)

    def _request_unmount(self, mount_point: str) -> None:
        arg = mount_point.rstrip("\\/")
        commands: list[list[str]] = []
        if self._dokanctl:
            commands.append([self._dokanctl, "/u", arg])
        commands.append(["mountvol", arg, "/d"])
        for cmd in commands:
            try:
                subprocess.run(cmd, capture_output=True, check=False, timeout=10)
            except OSError:
                continue


def _resolve_dokanctl() -> str | None:
    override = os.environ.get("AI_IDE_DOKANCTL", "").strip()
    if override:
        return override
    for candidate in [shutil.which("dokanctl.exe"), shutil.which("dokanctl")]:
        if candidate:
            return candidate
    for base in [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]:
        for pattern in ("Dokan*\\dokanctl.exe", "Dokan*\\Dokan Library-*\\dokanctl.exe"):
            for path in base.glob(pattern):
                if path.is_file():
                    return str(path)
    return None
