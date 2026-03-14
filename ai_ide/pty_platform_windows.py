"""Windows ConPTY backend using pywinpty."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_WINPTY_AVAILABLE = False
try:
    from winpty import PtyProcess  # type: ignore[import-untyped]

    _WINPTY_AVAILABLE = True
except ImportError:
    logger.info("pty_platform_windows: pywinpty not available, PTY mode will fall back to command mode")


def winpty_available() -> bool:
    return _WINPTY_AVAILABLE


class WindowsPtyBackend:
    """Windows PTY backend using pywinpty ConPTY."""

    def __init__(self) -> None:
        self._process: PtyProcess | None = None
        self._pid: int | None = None

    def spawn(self, shell: str, cols: int, rows: int, cwd: str, env: dict[str, str]) -> None:
        if not _WINPTY_AVAILABLE:
            raise RuntimeError("pywinpty is not installed")

        merged_env = dict(os.environ)
        merged_env.update(env)
        merged_env.setdefault("TERM", "xterm-256color")
        # Ensure UTF-8 output on Windows
        merged_env.setdefault("PYTHONUTF8", "1")

        self._process = PtyProcess.spawn(
            shell,
            dimensions=(rows, cols),
            cwd=cwd,
            env=merged_env,
        )
        self._pid = self._process.pid

    def read(self, size: int = 4096) -> bytes:
        if self._process is None:
            return b""
        try:
            data = self._process.read(size)
            if isinstance(data, str):
                return data.encode("utf-8", errors="replace")
            return data
        except (EOFError, OSError):
            return b""

    def write(self, data: bytes) -> None:
        if self._process is None:
            return
        text = data.decode("utf-8", errors="replace")
        self._process.write(text)

    def resize(self, cols: int, rows: int) -> None:
        if self._process is None:
            return
        try:
            self._process.setwinsize(rows, cols)
        except (OSError, RuntimeError):
            pass

    def close(self) -> None:
        if self._process is not None:
            try:
                if self._process.isalive():
                    self._process.terminate(force=True)
            except (OSError, RuntimeError):
                pass
            self._process = None

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        try:
            return self._process.isalive()
        except (OSError, RuntimeError):
            return False

    def pid(self) -> int | None:
        return self._pid
