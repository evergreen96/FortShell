from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def advisory_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        _prepare_lock_file(handle)
        _lock_handle(handle)
        try:
            yield
        finally:
            _unlock_handle(handle)


def _prepare_lock_file(handle: object) -> None:
    if hasattr(handle, "seek") and hasattr(handle, "tell") and hasattr(handle, "write"):
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)


def _lock_handle(handle: object) -> None:
    if "msvcrt" in globals():
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_handle(handle: object) -> None:
    if "msvcrt" in globals():
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
