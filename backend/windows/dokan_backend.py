"""Dokan/fusepy passthrough with protection model.

Protected files are VISIBLE but ACCESS-DENIED (EACCES).
Allowed files pass through to the original project directory.

This is the Windows reference implementation of FilteredFSBackend.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

from fuse import FuseOSError, Operations

from core.internal import is_internal_path
from core.workspace_access_service import WorkspaceAccessService


class DokanFilteredOperations(Operations):
    """Dokan/fusepy passthrough — protected files visible but access-denied."""

    def __init__(self, source_root: Path, workspace_access: WorkspaceAccessService) -> None:
        self.source_root = source_root.resolve()
        self.workspace_access = workspace_access

    def _real(self, path: str) -> Path:
        clean = path.replace("\\", "/").strip("/")
        return self.source_root / clean if clean else self.source_root

    def _is_protected(self, real: Path) -> bool:
        """Check if path is protected (deny-listed or internal metadata)."""
        resolved = real.resolve(strict=False)
        if is_internal_path(self.source_root, resolved):
            return True
        return not self.workspace_access.policy_engine.is_allowed(resolved)

    def _deny_if_protected(self, path: str) -> Path:
        """Return real path, or raise EACCES if protected."""
        real = self._real(path)
        if self._is_protected(real):
            raise FuseOSError(errno.EACCES)
        return real

    # -- Directory listing: protected files ARE included --

    def readdir(self, path, fh):
        real = self._real(path)
        if not real.is_dir():
            return []
        entries = [".", ".."]
        try:
            for entry in sorted(real.iterdir(), key=lambda p: p.name.lower()):
                entries.append(entry.name)
        except OSError:
            pass
        return entries

    # -- File metadata: protected files show zero permissions --

    def getattr(self, path, fh=None):
        real = self._real(path)
        if not real.exists():
            raise FuseOSError(errno.ENOENT)
        st = os.lstat(real)
        result = {
            key: getattr(st, key)
            for key in (
                "st_atime", "st_ctime", "st_gid", "st_mode",
                "st_mtime", "st_nlink", "st_size", "st_uid",
            )
        }
        if self._is_protected(real):
            result["st_mode"] = 0  # ---------- (no permissions)
        return result

    def access(self, path, amode):
        real = self._real(path)
        if self._is_protected(real):
            raise FuseOSError(errno.EACCES)
        if not os.access(real, amode):
            raise FuseOSError(errno.EACCES)

    # -- Read: denied for protected --

    def open(self, path, flags):
        real = self._deny_if_protected(path)
        return os.open(real, flags | getattr(os, "O_BINARY", 0))

    def read(self, path, size, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.read(fh, size)

    # -- Write: denied for protected --

    def write(self, path, data, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.write(fh, data)

    def create(self, path, mode, fi=None):
        real = self._deny_if_protected(path)
        real.parent.mkdir(parents=True, exist_ok=True)
        return os.open(real, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)

    def truncate(self, path, length, fh=None):
        self._deny_if_protected(path)
        if fh is not None:
            os.ftruncate(fh, length)
        else:
            real = self._real(path)
            with open(real, "r+b") as handle:
                handle.truncate(length)

    # -- Modify: denied for protected --

    def chmod(self, path, mode):
        self._deny_if_protected(path)
        return os.chmod(self._real(path), mode)

    def unlink(self, path):
        self._deny_if_protected(path)
        return os.unlink(self._real(path))

    def rmdir(self, path):
        self._deny_if_protected(path)
        return os.rmdir(self._real(path))

    def mkdir(self, path, mode):
        self._deny_if_protected(path)
        return os.mkdir(self._real(path), mode)

    def rename(self, old, new):
        old_real = self._deny_if_protected(old)
        new_real = self._deny_if_protected(new)
        new_real.parent.mkdir(parents=True, exist_ok=True)
        return os.rename(old_real, new_real)

    def symlink(self, target, source):
        raise FuseOSError(errno.EACCES)

    def link(self, target, source):
        raise FuseOSError(errno.EACCES)

    def readlink(self, path):
        raise FuseOSError(errno.EACCES)

    # -- Pass-through utilities --

    def flush(self, path, fh):
        return os.fsync(fh)

    def release(self, path, fh):
        return os.close(fh)

    def fsync(self, path, datasync, fh):
        return os.fsync(fh)

    def utimens(self, path, times=None):
        self._deny_if_protected(path)
        os.utime(self._real(path), times)

    def statfs(self, path):
        real = self._real(path)
        if os.name == "nt":
            import ctypes
            free_bytes = ctypes.c_ulonglong(0)
            total_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                str(real), None, ctypes.pointer(total_bytes), ctypes.pointer(free_bytes)
            )
            return {
                "f_bsize": 4096,
                "f_blocks": total_bytes.value // 4096,
                "f_bfree": free_bytes.value // 4096,
                "f_bavail": free_bytes.value // 4096,
            }
        st = os.statvfs(real)
        return {
            key: getattr(st, key)
            for key in (
                "f_bsize", "f_frsize", "f_blocks", "f_bfree", "f_bavail",
                "f_files", "f_ffree", "f_favail", "f_flag", "f_namemax",
            )
        }
