"""Unix PTY backend using stdlib pty module."""

from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
from typing import Protocol


class PtyBackend(Protocol):
    """Common protocol for PTY backends across platforms."""

    def spawn(self, argv: list[str], cols: int, rows: int, cwd: str, env: dict[str, str]) -> None: ...
    def read(self, size: int = 4096) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def close(self) -> None: ...
    def is_alive(self) -> bool: ...
    def pid(self) -> int | None: ...


class UnixPtyBackend:
    """Unix PTY backend using stdlib pty + subprocess.Popen."""

    def __init__(self) -> None:
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._process: subprocess.Popen | None = None

    def spawn(self, argv: list[str], cols: int, rows: int, cwd: str, env: dict[str, str]) -> None:
        if not argv:
            raise ValueError("PTY spawn argv must not be empty")
        master_fd, slave_fd = pty.openpty()
        # Set initial terminal size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        merged_env = dict(os.environ)
        merged_env.update(env)
        merged_env["TERM"] = merged_env.get("TERM", "xterm-256color")

        self._process = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=merged_env,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        self._master_fd = master_fd
        self._slave_fd = None

        # Set master fd to non-blocking for reads
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    def read(self, size: int = 4096) -> bytes:
        if self._master_fd is None:
            return b""
        try:
            return os.read(self._master_fd, size)
        except (OSError, BlockingIOError):
            return b""

    def write(self, data: bytes) -> None:
        if self._master_fd is None:
            return
        os.write(self._master_fd, data)

    def resize(self, cols: int, rows: int) -> None:
        if self._master_fd is None:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    def close(self) -> None:
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self._process.kill()
                    self._process.wait(timeout=2)
                except OSError:
                    pass
            self._process = None

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None

    def pid(self) -> int | None:
        if self._process is None:
            return None
        return self._process.pid
