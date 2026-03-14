from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import shutil
import string
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ai_ide.internal import is_internal_path
from ai_ide.projection import ProjectionManifest, ProjectedWorkspaceManager

if TYPE_CHECKING:
    from ai_ide.policy import PolicyEngine
    from ai_ide.workspace_access_service import WorkspaceAccessService

logger = logging.getLogger(__name__)


class FilteredFSBackend(ABC):
    project_root: Path
    internal_runtime_dir: Path
    workspace_access: "WorkspaceAccessService"
    policy_engine: "PolicyEngine"

    @abstractmethod
    def materialize(self, session_id: str) -> ProjectionManifest:
        raise NotImplementedError

    @abstractmethod
    def projection_root(self, session_id: str) -> Path:
        raise NotImplementedError

    @abstractmethod
    def update_policy(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def cleanup_stale(self, current_session_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def unmount(self, session_id: str | None = None) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def mount_root(self) -> Path | None:
        raise NotImplementedError

    @property
    def root(self) -> Path:
        return self.project_root


class MirrorFilteredFSBackend(FilteredFSBackend):
    """Compatibility backend for tests and environments without Dokan."""

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
        self._projection = ProjectedWorkspaceManager(
            self.project_root,
            self.policy_engine,
            runtime_root,
            workspace_access=self.workspace_access,
        )
        self.internal_runtime_dir = self._projection.internal_runtime_dir
        self._current_manifest: ProjectionManifest | None = None

    def materialize(self, session_id: str) -> ProjectionManifest:
        self._current_manifest = self._projection.materialize(session_id)
        return self._current_manifest

    def projection_root(self, session_id: str) -> Path:
        return self._projection.projection_root(session_id)

    def update_policy(self) -> None:
        logger.info("filtered_fs.mirror_policy_updated version=%s", self.policy_engine.state.version)

    def cleanup_stale(self, current_session_id: str) -> None:
        self._projection.cleanup_stale(current_session_id)
        if self._current_manifest is not None and self._current_manifest.session_id != current_session_id:
            self._current_manifest = None

    def unmount(self, session_id: str | None = None) -> None:
        if session_id is None:
            if self._current_manifest is None:
                return
            session_id = self._current_manifest.session_id
        self._projection.cleanup(session_id)
        if self._current_manifest is not None and self._current_manifest.session_id == session_id:
            self._current_manifest = None

    @property
    def mount_root(self) -> Path | None:
        return None if self._current_manifest is None else self._current_manifest.root


@dataclass(frozen=True)
class _DokanMountConfig:
    mount_point: str
    session_id: str


class DokanFilteredFSBackend(FilteredFSBackend):
    """Windows Dokan backend using the fusepy prototype in a managed thread."""

    def __init__(
        self,
        project_root: Path,
        policy_engine: "PolicyEngine",
        runtime_root: Path,
        *,
        workspace_access: "WorkspaceAccessService",
        fuse_factory,
        operations_factory,
    ) -> None:
        self.project_root = project_root.resolve()
        self.policy_engine = policy_engine
        self.workspace_access = workspace_access
        self.internal_runtime_dir = runtime_root.resolve()
        self._fuse_factory = fuse_factory
        self._operations_factory = operations_factory
        self._lock = threading.RLock()
        self._mount_thread: threading.Thread | None = None
        self._mounted_config: _DokanMountConfig | None = None
        self._mount_error: BaseException | None = None
        self._dokanctl = _resolve_dokanctl()

    def materialize(self, session_id: str) -> ProjectionManifest:
        mount_point = self._ensure_mounted(session_id)
        return ProjectionManifest(
            session_id=session_id,
            root=mount_point,
            file_count=0,
            directory_count=0,
            policy_version=self.policy_engine.state.version,
        )

    def projection_root(self, session_id: str) -> Path:
        config = self._mounted_config
        if config is not None and config.session_id == session_id:
            return self._mount_path(config.mount_point)
        return self._mount_path(self._preferred_mount_point())

    def update_policy(self) -> None:
        # The Dokan operations object reads from workspace_access / policy_engine directly,
        # so policy updates are visible without rebuilding copies.
        logger.info("filtered_fs.policy_updated version=%s", self.policy_engine.state.version)

    def cleanup_stale(self, current_session_id: str) -> None:
        with self._lock:
            if self._mounted_config is None:
                return
            if self._mounted_config.session_id == current_session_id:
                return
        self.unmount()

    def unmount(self, session_id: str | None = None) -> None:
        with self._lock:
            config = self._mounted_config
            thread = self._mount_thread
            if config is None:
                return
            if session_id is not None and config.session_id != session_id:
                return
            mount_point = config.mount_point
            self._mounted_config = None
            self._mount_thread = None

        self._request_unmount(mount_point)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    @property
    def mount_root(self) -> Path | None:
        config = self._mounted_config
        return None if config is None else self._mount_path(config.mount_point)

    def _ensure_mounted(self, session_id: str) -> Path:
        with self._lock:
            config = self._mounted_config
            if config is not None and config.session_id == session_id:
                return Path(config.mount_point)
        self.unmount()

        mount_point = self._preferred_mount_point()
        config = _DokanMountConfig(mount_point=mount_point, session_id=session_id)
        self._mount_error = None
        thread = threading.Thread(
            target=self._run_mount,
            args=(config,),
            name=f"dokan-mount-{session_id}",
            daemon=True,
        )
        with self._lock:
            self._mounted_config = config
            self._mount_thread = thread
        thread.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            if self._mount_error is not None:
                self.unmount(session_id)
                raise RuntimeError(f"Dokan mount failed: {self._mount_error}") from self._mount_error
            if self._mount_path(mount_point).exists():
                return self._mount_path(mount_point)
            time.sleep(0.05)
        if self._mount_error is not None:
            self.unmount(session_id)
            raise RuntimeError(f"Dokan mount failed: {self._mount_error}") from self._mount_error
        self.unmount(session_id)
        raise RuntimeError("Timed out waiting for Dokan mount to become ready")

    def _run_mount(self, config: _DokanMountConfig) -> None:
        try:
            operations = self._operations_factory(self.project_root, self.workspace_access)
            self._fuse_factory(
                operations,
                config.mount_point,
                foreground=True,
                allow_other=False,
                nothreads=False,
            )
        except BaseException as exc:  # noqa: BLE001
            self._mount_error = exc
            logger.exception("filtered_fs.mount_failed mount_point=%s", config.mount_point)

    def _preferred_mount_point(self) -> str:
        override = os.environ.get("AI_IDE_FILTERED_MOUNT_POINT", "").strip()
        if override:
            return override
        used_mask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in reversed(string.ascii_uppercase):
            if letter in {"A", "B"}:
                continue
            index = ord(letter) - ord("A")
            if not (used_mask & (1 << index)):
                return f"{letter}:"
        raise RuntimeError("No free drive letters available for Dokan mount")

    @staticmethod
    def _mount_path(mount_point: str) -> Path:
        return Path(f"{mount_point}\\") if mount_point.endswith(":") else Path(mount_point)

    def _request_unmount(self, mount_point: str) -> None:
        mount_arg = mount_point.rstrip("\\/")
        commands: list[list[str]] = []
        if self._dokanctl is not None:
            commands.append([self._dokanctl, "/u", mount_arg])
        commands.append(["mountvol", mount_arg, "/d"])
        for command in commands:
            try:
                subprocess.run(command, capture_output=True, check=False, timeout=10)
            except OSError:
                continue


def create_filtered_fs_backend(
    project_root: Path,
    policy_engine: "PolicyEngine",
    runtime_root: Path,
    *,
    workspace_access: "WorkspaceAccessService",
) -> FilteredFSBackend:
    preference = os.environ.get("AI_IDE_FILTERED_FS_BACKEND", "auto").strip().lower()
    if preference == "mirror" or os.environ.get("PYTEST_CURRENT_TEST"):
        return MirrorFilteredFSBackend(
            project_root,
            policy_engine,
            runtime_root,
            workspace_access=workspace_access,
        )

    dokan_supported = _dokan_supported()
    if os.name == "nt" and preference in {"auto", "dokan"} and dokan_supported:
        try:
            from fuse import FUSE

            from ai_ide.dokan_backend import DokanFilteredOperations

            return DokanFilteredFSBackend(
                project_root,
                policy_engine,
                runtime_root,
                workspace_access=workspace_access,
                fuse_factory=FUSE,
                operations_factory=DokanFilteredOperations,
            )
        except Exception as exc:  # noqa: BLE001
            if preference == "dokan":
                raise
            logger.warning("filtered_fs.dokan_unavailable fallback=mirror error=%s", exc)

    return MirrorFilteredFSBackend(
        project_root,
        policy_engine,
        runtime_root,
        workspace_access=workspace_access,
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


def _resolve_dokanctl() -> str | None:
    override = os.environ.get("AI_IDE_DOKANCTL", "").strip()
    if override:
        return override
    for candidate in [
        shutil.which("dokanctl.exe"),
        shutil.which("dokanctl"),
    ]:
        if candidate:
            return candidate
    program_files = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    for base in program_files:
        for pattern in ("Dokan*\\dokanctl.exe", "Dokan*\\Dokan Library-*\\dokanctl.exe"):
            for path in base.glob(pattern):
                if path.is_file():
                    return str(path)
    return None
