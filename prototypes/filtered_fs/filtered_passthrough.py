"""Filtered passthrough filesystem using WinFsp.

Mounts a virtual drive that mirrors a source directory but hides
files/folders matching deny rules. All reads and writes go through
to the original files — no copies, no sync needed.

Usage:
    python filtered_passthrough.py C:/path/to/project Z: secrets .env

This mounts C:/path/to/project as Z: with secrets/ and .env hidden.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
import threading
import time
from fnmatch import fnmatch
from functools import wraps
from pathlib import Path, PureWindowsPath
from typing import List

from winfspy import (
    FILE_ATTRIBUTE,
    BaseFileSystemOperations,
    FileSystem,
    NTStatusAccessDenied,
    NTStatusEndOfFile,
    NTStatusObjectNameCollision,
    NTStatusObjectNameNotFound,
    NTStatusNotADirectory,
    enable_debug_log,
)
from winfspy.plumbing.win32_filetime import filetime_now
from winfspy.plumbing.security_descriptor import SecurityDescriptor

logger = logging.getLogger(__name__)

# Default security descriptor — everyone full access
DEFAULT_SD = SecurityDescriptor.from_string("O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;WD)")


# ---------------------------------------------------------------------------
# Deny-list policy
# ---------------------------------------------------------------------------

class DenyPolicy:
    def __init__(self, deny_globs: list[str] | None = None) -> None:
        self._deny_globs: list[str] = [g.strip().replace("\\", "/").rstrip("/") for g in (deny_globs or []) if g.strip()]

    def is_denied(self, relative_path: str) -> bool:
        if not relative_path or relative_path == "\\" or relative_path == "/":
            return False
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


# ---------------------------------------------------------------------------
# Thread-safe operation decorator (from memfs example)
# ---------------------------------------------------------------------------

def operation(fn):
    name = fn.__name__

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        head = args[0] if args else None
        try:
            with self._thread_lock:
                result = fn(self, *args, **kwargs)
        except Exception as exc:
            logger.debug(f"  NOK | {name:20} | {head!r}")
            raise
        else:
            logger.debug(f"  OK  | {name:20} | {head!r}")
            return result

    return wrapper


# ---------------------------------------------------------------------------
# File context
# ---------------------------------------------------------------------------

class OpenedFile:
    """Wraps an open file handle for passthrough I/O."""

    def __init__(self, real_path: Path, is_dir: bool, handle=None):
        self.real_path = real_path
        self.is_dir = is_dir
        self.handle = handle  # open('r+b') for files, None for dirs

    def __repr__(self):
        return f"OpenedFile({self.real_path.name}, dir={self.is_dir})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _epoch_to_filetime(epoch: float) -> int:
    return int((epoch + 11644473600) * 10_000_000)


def _file_info(real_path: Path) -> dict:
    st = real_path.stat()
    attrs = FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL
    if stat.S_ISDIR(st.st_mode):
        attrs = FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY
    win_attrs = getattr(st, "st_file_attributes", 0)
    if win_attrs & 0x02:  # FILE_ATTRIBUTE_HIDDEN
        attrs |= FILE_ATTRIBUTE.FILE_ATTRIBUTE_HIDDEN
    if win_attrs & 0x01:  # FILE_ATTRIBUTE_READONLY
        attrs |= FILE_ATTRIBUTE.FILE_ATTRIBUTE_READONLY
    return {
        "file_attributes": attrs,
        "allocation_size": (st.st_size + 4095) & ~4095,
        "file_size": st.st_size,
        "creation_time": _epoch_to_filetime(getattr(st, "st_ctime", st.st_mtime)),
        "last_access_time": _epoch_to_filetime(st.st_atime),
        "last_write_time": _epoch_to_filetime(st.st_mtime),
        "change_time": _epoch_to_filetime(st.st_mtime),
        "index_number": 0,
    }


# ---------------------------------------------------------------------------
# Filesystem operations
# ---------------------------------------------------------------------------

class FilteredPassthroughOps(BaseFileSystemOperations):

    def __init__(self, source_root: Path, policy: DenyPolicy) -> None:
        super().__init__()
        self.source_root = source_root.resolve()
        self.policy = policy
        self._thread_lock = threading.Lock()

    def _real(self, virt_name: str) -> Path:
        clean = virt_name.replace("\\", "/").strip("/")
        return self.source_root / clean if clean else self.source_root

    def _relative(self, virt_name: str) -> str:
        return virt_name.replace("\\", "/").strip("/")

    def _check(self, virt_name: str) -> None:
        if self.policy.is_denied(self._relative(virt_name)):
            raise NTStatusObjectNameNotFound()

    # -- Volume --

    @operation
    def get_volume_info(self):
        return {
            "total_size": 100 * 1024 * 1024 * 1024,
            "free_size": 50 * 1024 * 1024 * 1024,
            "volume_label": "FilteredFS",
        }

    # -- Security --

    @operation
    def get_security_by_name(self, file_name: str):
        self._check(file_name)
        real = self._real(file_name)
        if not real.exists():
            raise NTStatusObjectNameNotFound()
        st = real.stat()
        attrs = FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL
        if stat.S_ISDIR(st.st_mode):
            attrs = FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY
        return attrs, DEFAULT_SD.handle, DEFAULT_SD.size

    @operation
    def get_security(self, file_context):
        return DEFAULT_SD

    # -- Open / Create / Close --

    @operation
    def open(self, file_name, create_options, granted_access):
        self._check(file_name)
        real = self._real(file_name)
        if not real.exists():
            raise NTStatusObjectNameNotFound()
        is_dir = real.is_dir()
        handle = None
        if not is_dir:
            handle = open(real, "r+b")
        return OpenedFile(real, is_dir, handle)

    @operation
    def create(self, file_name, create_options, granted_access,
               file_attributes, security_descriptor, allocation_size):
        self._check(file_name)
        real = self._real(file_name)
        if real.exists():
            raise NTStatusObjectNameCollision()
        is_dir = bool(create_options & 0x01)
        if is_dir:
            real.mkdir(parents=True, exist_ok=True)
            return OpenedFile(real, True)
        else:
            real.parent.mkdir(parents=True, exist_ok=True)
            handle = open(real, "w+b")
            return OpenedFile(real, False, handle)

    @operation
    def close(self, file_context: OpenedFile):
        if file_context.handle is not None:
            file_context.handle.close()
            file_context.handle = None

    @operation
    def overwrite(self, file_context: OpenedFile, file_attributes,
                  replace_file_attributes, allocation_size):
        if file_context.handle:
            file_context.handle.seek(0)
            file_context.handle.truncate(0)

    # -- Read / Write --

    @operation
    def read(self, file_context: OpenedFile, offset, length):
        if file_context.handle is None:
            raise NTStatusAccessDenied()
        file_context.handle.seek(offset)
        data = file_context.handle.read(length)
        if not data:
            raise NTStatusEndOfFile()
        return data

    @operation
    def write(self, file_context: OpenedFile, buffer, offset,
              write_to_end_of_file, constrained_io):
        if file_context.handle is None:
            raise NTStatusAccessDenied()
        data = bytes(buffer)
        if write_to_end_of_file:
            file_context.handle.seek(0, 2)
        else:
            file_context.handle.seek(offset)
        file_context.handle.write(data)
        file_context.handle.flush()
        return len(data)

    @operation
    def flush(self, file_context: OpenedFile):
        if file_context.handle:
            file_context.handle.flush()

    # -- File info --

    @operation
    def get_file_info(self, file_context: OpenedFile):
        return _file_info(file_context.real_path)

    @operation
    def set_basic_info(self, file_context, file_attributes, creation_time,
                       last_access_time, last_write_time, change_time, file_info):
        return _file_info(file_context.real_path)

    @operation
    def set_file_size(self, file_context: OpenedFile, new_size, set_allocation_size):
        if file_context.handle and not set_allocation_size:
            file_context.handle.truncate(new_size)

    # -- Directory listing (core filtering) --

    @operation
    def read_directory(self, file_context: OpenedFile, marker):
        if not file_context.is_dir:
            raise NTStatusNotADirectory()

        entries = []
        try:
            children = sorted(file_context.real_path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return []

        for child in children:
            # Apply deny policy
            try:
                rel = child.relative_to(self.source_root).as_posix()
            except ValueError:
                rel = child.name
            if self.policy.is_denied(rel):
                continue

            try:
                info = _file_info(child)
            except OSError:
                continue
            info["file_name"] = child.name
            entries.append(info)

        # Sort
        entries.sort(key=lambda e: e["file_name"])

        # Apply marker (pagination)
        if marker is not None:
            for i, entry in enumerate(entries):
                if entry["file_name"] == marker:
                    return entries[i + 1:]
            return []

        return entries

    # -- Delete --

    @operation
    def can_delete(self, file_context, file_name):
        pass

    @operation
    def cleanup(self, file_context: OpenedFile, file_name, flags):
        FspCleanupDelete = 0x01
        if flags & FspCleanupDelete:
            if file_context.handle:
                file_context.handle.close()
                file_context.handle = None
            try:
                if file_context.is_dir:
                    file_context.real_path.rmdir()
                else:
                    file_context.real_path.unlink()
            except OSError:
                pass

    # -- Rename --

    @operation
    def rename(self, file_context: OpenedFile, file_name, new_file_name, replace_if_exists):
        new_real = self._real(new_file_name)
        if new_real.exists() and not replace_if_exists:
            raise NTStatusObjectNameCollision()
        if file_context.handle:
            file_context.handle.close()
            file_context.handle = None
        file_context.real_path.rename(new_real)
        file_context.real_path = new_real


# ---------------------------------------------------------------------------
# Mount
# ---------------------------------------------------------------------------

def mount(source: str | Path, mount_point: str, deny_globs: list[str],
          *, debug: bool = False) -> FileSystem:
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError(f"Source must be a directory: {source}")

    policy = DenyPolicy(deny_globs)
    if debug:
        enable_debug_log()

    ops = FilteredPassthroughOps(source, policy)

    mp = Path(mount_point)
    is_drive = mp.parent == mp

    fs = FileSystem(
        str(mount_point),
        ops,
        sector_size=512,
        sectors_per_allocation_unit=1,
        volume_creation_time=filetime_now(),
        volume_serial_number=0x12345678,
        file_info_timeout=1000,
        case_sensitive_search=False,
        case_preserved_names=True,
        unicode_on_disk=True,
        persistent_acls=True,
        post_cleanup_when_modified_only=True,
        um_file_context_is_user_context2=True,
        prefix="",
        file_system_name="FilteredFS",
        reject_irp_prior_to_transact0=not is_drive,
    )
    fs.start()
    return fs


def main():
    if len(sys.argv) < 3:
        print("Usage: python filtered_passthrough.py <source_dir> <mount_point> [deny_glob ...]")
        print("Example: python filtered_passthrough.py C:/projects/myapp Z: secrets .env")
        sys.exit(1)

    source = sys.argv[1]
    mount_point = sys.argv[2]
    deny_globs = sys.argv[3:] if len(sys.argv) > 3 else []

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print(f"Source:      {source}")
    print(f"Mount point: {mount_point}")
    print(f"Deny globs:  {deny_globs}")

    fs = mount(source, mount_point, deny_globs, debug=False)

    print(f"\nMounted: {source} -> {mount_point}")
    print(f"Hidden:  {deny_globs}")
    print(f"\nTry:  dir {mount_point}\\")
    print(f"      ls {mount_point}/")
    print(f"\nCtrl+C to unmount.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nUnmounting...")
        fs.stop()
        print("Done.")


if __name__ == "__main__":
    main()
