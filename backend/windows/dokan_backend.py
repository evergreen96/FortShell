from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from fuse import FuseOSError, Operations

from core.workspace_access_service import WorkspaceAccessService


class DokanFilteredOperations(Operations):
    """Dokan/fusepy passthrough that hides denied paths as ENOENT."""

    def __init__(self, source_root: Path, workspace_access: WorkspaceAccessService) -> None:
        self.source_root = source_root.resolve()
        self.workspace_access = workspace_access

    def _relative(self, path: str) -> str:
        return path.replace("\\", "/").strip("/")

    def _real(self, path: str) -> Path:
        clean = self._relative(path)
        return self.source_root / clean if clean else self.source_root

    def _check(self, path: str) -> Path:
        real = self._real(path)
        try:
            self.workspace_access.assert_allowed(real)
        except PermissionError as exc:
            raise FuseOSError(errno.ENOENT) from exc
        return real

    def getattr(self, path, fh=None):
        real = self._check(path)
        st = os.lstat(real)
        return {
            key: getattr(st, key)
            for key in (
                "st_atime",
                "st_ctime",
                "st_gid",
                "st_mode",
                "st_mtime",
                "st_nlink",
                "st_size",
                "st_uid",
            )
        }

    def readdir(self, path, fh):
        real = self._check(path)
        entries = [".", ".."]
        for entry in sorted(real.iterdir(), key=lambda item: item.name.lower()):
            if self.workspace_access.is_visible(entry):
                entries.append(entry.name)
        return entries

    def open(self, path, flags):
        real = self._check(path)
        return os.open(real, flags)

    def read(self, path, size, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.read(fh, size)

    def write(self, path, data, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.write(fh, data)

    def create(self, path, mode, fi=None):
        real = self._check(path)
        real.parent.mkdir(parents=True, exist_ok=True)
        return os.open(real, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)

    def truncate(self, path, length, fh=None):
        real = self._check(path)
        with open(real, "r+b") as handle:
            handle.truncate(length)

    def flush(self, path, fh):
        return os.fsync(fh)

    def release(self, path, fh):
        return os.close(fh)

    def fsync(self, path, datasync, fh):
        return os.fsync(fh)

    def mkdir(self, path, mode):
        real = self._check(path)
        return os.mkdir(real, mode)

    def rmdir(self, path):
        real = self._check(path)
        return os.rmdir(real)

    def unlink(self, path):
        real = self._check(path)
        return os.unlink(real)

    def rename(self, old, new):
        old_real = self._check(old)
        new_real = self._check(new)
        new_real.parent.mkdir(parents=True, exist_ok=True)
        return os.rename(old_real, new_real)

    def chmod(self, path, mode):
        real = self._check(path)
        return os.chmod(real, mode)

    def statfs(self, path):
        real = self._check(path)
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
                "f_bsize",
                "f_frsize",
                "f_blocks",
                "f_bfree",
                "f_bavail",
                "f_files",
                "f_ffree",
                "f_favail",
                "f_flag",
                "f_namemax",
            )
        }

    def access(self, path, amode):
        real = self._check(path)
        if not os.access(real, amode):
            raise FuseOSError(errno.EACCES)

    def utimens(self, path, times=None):
        real = self._check(path)
        os.utime(real, times)

    def readlink(self, path):
        self._check(path)
        raise FuseOSError(errno.ENOENT)
