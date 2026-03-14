"""Filtered passthrough filesystem using Dokan (via FUSE compatibility).

Mirrors a source directory, hiding files/folders matching deny rules.
All reads/writes go through to original files.

Usage:
    python dokan_filtered.py C:/path/to/project Z: secrets .env

License: Dokan is LGPL — free for commercial use without modification.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
import time
from fnmatch import fnmatch
from pathlib import Path

# fusepy uses FUSE API — on Windows it connects to Dokan's FUSE layer
from fuse import FUSE, FuseOSError, Operations


class DenyPolicy:
    def __init__(self, deny_globs: list[str] | None = None) -> None:
        self._deny_globs = [g.strip().replace("\\", "/").rstrip("/") for g in (deny_globs or []) if g.strip()]

    def is_denied(self, relative_path: str) -> bool:
        clean = relative_path.replace("\\", "/").strip("/")
        if not clean:
            return False
        parts = clean.split("/")
        for i in range(len(parts)):
            partial = "/".join(parts[: i + 1])
            for glob in self._deny_globs:
                if partial == glob or partial.startswith(glob + "/"):
                    return True
                if fnmatch(partial, glob):
                    return True
        return False


class FilteredPassthrough(Operations):
    """FUSE passthrough with deny-list filtering."""

    def __init__(self, source_root: str, policy: DenyPolicy):
        self.source_root = os.path.realpath(source_root)
        self.policy = policy

    def _real(self, path: str) -> str:
        """Convert FUSE path to real path."""
        clean = path.lstrip("/")
        return os.path.join(self.source_root, clean) if clean else self.source_root

    def _relative(self, path: str) -> str:
        return path.replace("\\", "/").strip("/")

    def _check(self, path: str) -> None:
        if self.policy.is_denied(self._relative(path)):
            raise FuseOSError(errno.ENOENT)

    # -- Filesystem methods --

    def getattr(self, path, fh=None):
        self._check(path)
        real = self._real(path)
        st = os.lstat(real)
        return dict(
            (key, getattr(st, key))
            for key in (
                "st_atime", "st_ctime", "st_gid", "st_mode",
                "st_mtime", "st_nlink", "st_size", "st_uid",
            )
        )

    def readdir(self, path, fh):
        self._check(path)
        real = self._real(path)
        entries = [".", ".."]
        for name in os.listdir(real):
            rel = self._relative(path + "/" + name)
            if not self.policy.is_denied(rel):
                entries.append(name)
        return entries

    def open(self, path, flags):
        self._check(path)
        real = self._real(path)
        return os.open(real, flags)

    def read(self, path, size, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.read(fh, size)

    def write(self, path, data, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.write(fh, data)

    def create(self, path, mode, fi=None):
        self._check(path)
        real = self._real(path)
        return os.open(real, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)

    def truncate(self, path, length, fh=None):
        self._check(path)
        real = self._real(path)
        with open(real, "r+b") as f:
            f.truncate(length)

    def flush(self, path, fh):
        return os.fsync(fh)

    def release(self, path, fh):
        return os.close(fh)

    def fsync(self, path, datasync, fh):
        return os.fsync(fh)

    def mkdir(self, path, mode):
        self._check(path)
        return os.mkdir(self._real(path), mode)

    def rmdir(self, path):
        self._check(path)
        return os.rmdir(self._real(path))

    def unlink(self, path):
        self._check(path)
        return os.unlink(self._real(path))

    def rename(self, old, new):
        self._check(old)
        self._check(new)
        return os.rename(self._real(old), self._real(new))

    def chmod(self, path, mode):
        self._check(path)
        return os.chmod(self._real(path), mode)

    def statfs(self, path):
        real = self._real(path)
        if sys.platform == "win32":
            import ctypes
            free_bytes = ctypes.c_ulonglong(0)
            total_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                real, None, ctypes.pointer(total_bytes), ctypes.pointer(free_bytes)
            )
            return {
                "f_bsize": 4096,
                "f_blocks": total_bytes.value // 4096,
                "f_bfree": free_bytes.value // 4096,
                "f_bavail": free_bytes.value // 4096,
            }
        st = os.statvfs(real)
        return dict((key, getattr(st, key)) for key in (
            "f_bsize", "f_frsize", "f_blocks", "f_bfree", "f_bavail",
            "f_files", "f_ffree", "f_favail", "f_flag", "f_namemax",
        ))

    def access(self, path, amode):
        self._check(path)
        real = self._real(path)
        if not os.access(real, amode):
            raise FuseOSError(errno.EACCES)

    def utimens(self, path, times=None):
        self._check(path)
        real = self._real(path)
        os.utime(real, times)


def main():
    if len(sys.argv) < 3:
        print("Usage: python dokan_filtered.py <source_dir> <mount_point> [deny_glob ...]")
        print("Example: python dokan_filtered.py C:/projects/myapp Z: secrets .env")
        sys.exit(1)

    source = sys.argv[1]
    mount_point = sys.argv[2]
    deny_globs = sys.argv[3:] if len(sys.argv) > 3 else []

    policy = DenyPolicy(deny_globs)

    print(f"Source:      {source}")
    print(f"Mount point: {mount_point}")
    print(f"Deny globs:  {deny_globs}")
    print(f"Backend:     Dokan (FUSE compat)")
    print()
    print(f"Mounting... Ctrl+C to unmount.")

    FUSE(
        FilteredPassthrough(source, policy),
        mount_point,
        foreground=True,
        allow_other=False,
        nothreads=False,
    )


if __name__ == "__main__":
    main()
