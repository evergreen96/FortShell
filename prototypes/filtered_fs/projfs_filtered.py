"""Filtered passthrough filesystem using ProjFS (Windows Projected File System).

ProjFS projects files from a source directory into a virtualization root.
Hidden files are simply not projected. Writes to projected files go to disk
in the virtualization root as "full" files (hydrated + dirty).

Usage:
    python projfs_filtered.py C:/path/to/project C:/vfs-view secrets .env

Note: The vfs-view directory will be created if it doesn't exist.
      ProjFS feature must be enabled first.

License: Windows built-in (no additional license needed).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import shutil
import sys
import time
import uuid
from fnmatch import fnmatch
from pathlib import Path

# ---------------------------------------------------------------------------
# ProjFS ctypes bindings (minimal subset needed for passthrough)
# ---------------------------------------------------------------------------

projfs = ctypes.WinDLL(os.path.join(os.environ["SystemRoot"], "System32", "ProjectedFSLib.dll"))

HRESULT = ctypes.HRESULT
S_OK = 0
ERROR_FILE_NOT_FOUND = 0x80070002
ERROR_INSUFFICIENT_BUFFER = 0x8007007A

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]

class LARGE_INTEGER(ctypes.Union):
    class _anon(ctypes.Structure):
        _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]
    _anonymous_ = ("_anon",)
    _fields_ = [("_anon", _anon), ("QuadPart", ctypes.c_int64)]

class PRJ_FILE_BASIC_INFO(ctypes.Structure):
    _fields_ = [
        ("IsDirectory", ctypes.c_bool),
        ("FileSize", ctypes.c_int64),
        ("CreationTime", LARGE_INTEGER),
        ("LastAccessTime", LARGE_INTEGER),
        ("LastWriteTime", LARGE_INTEGER),
        ("ChangeTime", LARGE_INTEGER),
        ("FileAttributes", ctypes.c_uint32),
    ]

class PRJ_PLACEHOLDER_INFO(ctypes.Structure):
    _fields_ = [
        ("FileBasicInfo", PRJ_FILE_BASIC_INFO),
        ("EaInformation_EaBufferSize", ctypes.c_uint32),
        ("EaInformation_OffsetToFirstEa", ctypes.c_uint32),
        ("SecurityInformation_SecurityBufferSize", ctypes.c_uint32),
        ("SecurityInformation_OffsetToSecurityDescriptor", ctypes.c_uint32),
        ("StreamsInformation_StreamsInfoBufferSize", ctypes.c_uint32),
        ("StreamsInformation_OffsetToFirstStreamInfo", ctypes.c_uint32),
        ("VersionInfo_ProviderID", ctypes.c_uint8 * 128),
        ("VersionInfo_ContentID", ctypes.c_uint8 * 128),
    ]

class PRJ_CALLBACK_DATA(ctypes.Structure):
    _fields_ = [
        ("Size", ctypes.c_uint32),
        ("Flags", ctypes.c_uint32),
        ("NamespaceVirtualizationContext", ctypes.c_void_p),
        ("CommandId", ctypes.c_int32),
        ("FileId", GUID),
        ("DataStreamId", GUID),
        ("FilePathName", ctypes.c_wchar_p),
        ("VersionInfo", ctypes.c_void_p),
        ("TriggeringProcessId", ctypes.c_uint32),
        ("TriggeringProcessImageFileName", ctypes.c_wchar_p),
        ("InstanceContext", ctypes.c_void_p),
    ]

# Callback types
PRJ_START_DIRECTORY_ENUMERATION_CB = ctypes.WINFUNCTYPE(HRESULT, ctypes.POINTER(PRJ_CALLBACK_DATA), ctypes.POINTER(GUID))
PRJ_END_DIRECTORY_ENUMERATION_CB = ctypes.WINFUNCTYPE(HRESULT, ctypes.POINTER(PRJ_CALLBACK_DATA), ctypes.POINTER(GUID))
PRJ_GET_DIRECTORY_ENUMERATION_CB = ctypes.WINFUNCTYPE(HRESULT, ctypes.POINTER(PRJ_CALLBACK_DATA), ctypes.POINTER(GUID), ctypes.c_wchar_p, ctypes.c_void_p)
PRJ_GET_PLACEHOLDER_INFO_CB = ctypes.WINFUNCTYPE(HRESULT, ctypes.POINTER(PRJ_CALLBACK_DATA))
PRJ_GET_FILE_DATA_CB = ctypes.WINFUNCTYPE(HRESULT, ctypes.POINTER(PRJ_CALLBACK_DATA), ctypes.c_uint64, ctypes.c_uint32)

class PRJ_CALLBACKS(ctypes.Structure):
    _fields_ = [
        ("StartDirectoryEnumerationCallback", PRJ_START_DIRECTORY_ENUMERATION_CB),
        ("EndDirectoryEnumerationCallback", PRJ_END_DIRECTORY_ENUMERATION_CB),
        ("GetDirectoryEnumerationCallback", PRJ_GET_DIRECTORY_ENUMERATION_CB),
        ("GetPlaceholderInfoCallback", PRJ_GET_PLACEHOLDER_INFO_CB),
        ("GetFileDataCallback", PRJ_GET_FILE_DATA_CB),
        ("QueryFileNameCallback", ctypes.c_void_p),
        ("NotificationCallback", ctypes.c_void_p),
        ("CancelCommandCallback", ctypes.c_void_p),
    ]

# ProjFS API functions
PrjMarkDirectoryAsPlaceholder = projfs.PrjMarkDirectoryAsPlaceholder
PrjMarkDirectoryAsPlaceholder.restype = HRESULT
PrjMarkDirectoryAsPlaceholder.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(GUID)]

PrjStartVirtualizing = projfs.PrjStartVirtualizing
PrjStartVirtualizing.restype = HRESULT
PrjStartVirtualizing.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(PRJ_CALLBACKS), ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]

PrjStopVirtualizing = projfs.PrjStopVirtualizing
PrjStopVirtualizing.restype = None
PrjStopVirtualizing.argtypes = [ctypes.c_void_p]

PrjFillDirEntryBuffer = projfs.PrjFillDirEntryBuffer
PrjFillDirEntryBuffer.restype = HRESULT
PrjFillDirEntryBuffer.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(PRJ_FILE_BASIC_INFO), ctypes.c_void_p]

PrjWritePlaceholderInfo = projfs.PrjWritePlaceholderInfo
PrjWritePlaceholderInfo.restype = HRESULT
PrjWritePlaceholderInfo.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(PRJ_PLACEHOLDER_INFO), ctypes.c_uint32]

PrjAllocateAlignedBuffer = projfs.PrjAllocateAlignedBuffer
PrjAllocateAlignedBuffer.restype = ctypes.c_void_p
PrjAllocateAlignedBuffer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]

PrjFreeAlignedBuffer = projfs.PrjFreeAlignedBuffer
PrjFreeAlignedBuffer.restype = None
PrjFreeAlignedBuffer.argtypes = [ctypes.c_void_p]

PrjWriteFileData = projfs.PrjWriteFileData
PrjWriteFileData.restype = HRESULT
PrjWriteFileData.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32]

PrjFileNameMatch = projfs.PrjFileNameMatch
PrjFileNameMatch.restype = ctypes.c_bool
PrjFileNameMatch.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]

PRJ_CB_DATA_FLAG_ENUM_RESTART_SCAN = 0x00000001

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class DenyPolicy:
    def __init__(self, deny_globs: list[str] | None = None):
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _epoch_to_filetime(epoch: float) -> int:
    return int((epoch + 11644473600) * 10_000_000)

def _file_basic_info(real_path: Path) -> PRJ_FILE_BASIC_INFO:
    st = real_path.stat()
    info = PRJ_FILE_BASIC_INFO()
    info.IsDirectory = real_path.is_dir()
    info.FileSize = st.st_size if not real_path.is_dir() else 0
    ft = _epoch_to_filetime(st.st_mtime)
    info.CreationTime.QuadPart = _epoch_to_filetime(getattr(st, "st_ctime", st.st_mtime))
    info.LastAccessTime.QuadPart = _epoch_to_filetime(st.st_atime)
    info.LastWriteTime.QuadPart = ft
    info.ChangeTime.QuadPart = ft
    info.FileAttributes = 0x10 if real_path.is_dir() else 0x20  # DIRECTORY or ARCHIVE
    return info

# ---------------------------------------------------------------------------
# Provider state
# ---------------------------------------------------------------------------

source_root: Path
policy: DenyPolicy
enum_sessions: dict = {}

def _source_path(relative: str) -> Path:
    clean = relative.replace("\\", "/").strip("/")
    return source_root / clean if clean else source_root

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@PRJ_START_DIRECTORY_ENUMERATION_CB
def start_enum(callback_data, enumeration_id):
    enum_sessions[enumeration_id.contents.Data1] = {"completed": False}
    return S_OK

@PRJ_END_DIRECTORY_ENUMERATION_CB
def end_enum(callback_data, enumeration_id):
    enum_sessions.pop(enumeration_id.contents.Data1, None)
    return S_OK

@PRJ_GET_DIRECTORY_ENUMERATION_CB
def get_enum(callback_data, enumeration_id, search_expression, dir_entry_buffer):
    try:
        session = enum_sessions.get(enumeration_id.contents.Data1)
        if session is None:
            return S_OK

        restart = bool(callback_data.contents.Flags & PRJ_CB_DATA_FLAG_ENUM_RESTART_SCAN)
        if session["completed"] and not restart:
            return S_OK

        rel_dir = callback_data.contents.FilePathName or ""
        real_dir = _source_path(rel_dir)

        if not real_dir.is_dir():
            return ERROR_FILE_NOT_FOUND

        for child in sorted(real_dir.iterdir(), key=lambda p: p.name.lower()):
            child_rel = (rel_dir + "\\" + child.name).strip("\\") if rel_dir else child.name
            if policy.is_denied(child_rel):
                continue
            if search_expression and not PrjFileNameMatch(child.name, search_expression):
                continue
            info = _file_basic_info(child)
            hr = PrjFillDirEntryBuffer(child.name, ctypes.byref(info), dir_entry_buffer)
            if hr == ERROR_INSUFFICIENT_BUFFER:
                return S_OK

        session["completed"] = True
        return S_OK
    except Exception as e:
        print(f"ERROR in get_enum: {e}", file=sys.stderr)
        return ERROR_FILE_NOT_FOUND

@PRJ_GET_PLACEHOLDER_INFO_CB
def get_placeholder(callback_data):
    try:
        rel_path = callback_data.contents.FilePathName
        if policy.is_denied(rel_path):
            return ERROR_FILE_NOT_FOUND

        real = _source_path(rel_path)
        if not real.exists():
            return ERROR_FILE_NOT_FOUND

        placeholder = PRJ_PLACEHOLDER_INFO()
        placeholder.FileBasicInfo = _file_basic_info(real)

        ctx = callback_data.contents.NamespaceVirtualizationContext
        return PrjWritePlaceholderInfo(ctx, rel_path, ctypes.byref(placeholder), ctypes.sizeof(placeholder))
    except Exception as e:
        print(f"ERROR in get_placeholder: {e}", file=sys.stderr)
        return ERROR_FILE_NOT_FOUND

@PRJ_GET_FILE_DATA_CB
def get_file_data(callback_data, byte_offset, length):
    try:
        rel_path = callback_data.contents.FilePathName
        if policy.is_denied(rel_path):
            return ERROR_FILE_NOT_FOUND

        real = _source_path(rel_path)
        if not real.exists():
            return ERROR_FILE_NOT_FOUND

        ctx = callback_data.contents.NamespaceVirtualizationContext
        data = real.read_bytes()

        buf = PrjAllocateAlignedBuffer(ctx, len(data))
        if not buf:
            return 0x8007000E  # E_OUTOFMEMORY

        ctypes.memmove(buf, data, len(data))
        hr = PrjWriteFileData(
            ctx,
            ctypes.byref(callback_data.contents.DataStreamId),
            buf,
            0,
            len(data),
        )
        PrjFreeAlignedBuffer(buf)
        return hr
    except Exception as e:
        print(f"ERROR in get_file_data: {e}", file=sys.stderr)
        return ERROR_FILE_NOT_FOUND


# ---------------------------------------------------------------------------
# Mount
# ---------------------------------------------------------------------------

def main():
    global source_root, policy

    if len(sys.argv) < 3:
        print("Usage: python projfs_filtered.py <source_dir> <virt_root> [deny_glob ...]")
        print("Example: python projfs_filtered.py C:/projects/myapp C:/vfs-view secrets .env")
        sys.exit(1)

    source_root = Path(sys.argv[1]).resolve()
    virt_root = Path(sys.argv[2]).resolve()
    deny_globs = sys.argv[3:] if len(sys.argv) > 3 else []
    policy = DenyPolicy(deny_globs)

    print(f"Source:    {source_root}")
    print(f"Virt root: {virt_root}")
    print(f"Deny:      {deny_globs}")

    # Prepare virtualization root
    if virt_root.exists():
        shutil.rmtree(virt_root)
    virt_root.mkdir(parents=True)

    instance_id = GUID()
    instance_id.Data1 = 0xD137C01A
    instance_id.Data2 = 0xBAAD
    instance_id.Data3 = 0xCAFE

    hr = PrjMarkDirectoryAsPlaceholder(str(virt_root), None, None, ctypes.byref(instance_id))
    if hr != S_OK:
        print(f"ERROR: PrjMarkDirectoryAsPlaceholder failed: 0x{hr & 0xFFFFFFFF:08X}")
        sys.exit(1)

    callbacks = PRJ_CALLBACKS()
    callbacks.StartDirectoryEnumerationCallback = start_enum
    callbacks.EndDirectoryEnumerationCallback = end_enum
    callbacks.GetDirectoryEnumerationCallback = get_enum
    callbacks.GetPlaceholderInfoCallback = get_placeholder
    callbacks.GetFileDataCallback = get_file_data

    instance_handle = ctypes.c_void_p()
    hr = PrjStartVirtualizing(str(virt_root), ctypes.byref(callbacks), None, None, ctypes.byref(instance_handle))
    if hr != S_OK:
        print(f"ERROR: PrjStartVirtualizing failed: 0x{hr & 0xFFFFFFFF:08X}")
        sys.exit(1)

    print(f"\nProjFS mounted: {source_root} -> {virt_root}")
    print(f"Hidden: {deny_globs}")
    print(f"\nNOTE: ProjFS reads from source, but writes go to virt_root (not source).")
    print(f"      This is a ProjFS limitation - it is designed for read-projection.")
    print(f"\nCtrl+C to unmount.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nUnmounting...")
        PrjStopVirtualizing(instance_handle)
        print("Done.")


if __name__ == "__main__":
    main()
