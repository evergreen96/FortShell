"""PTY session manager — create, track, and destroy PTY sessions."""

from __future__ import annotations

import collections
import logging
import platform
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

PTY_OUTPUT_BUFFER_MAX = 100_000  # Max bytes retained in ring buffer per PTY


@dataclass
class PtySessionConfig:
    terminal_id: str
    shell: str
    cols: int
    rows: int
    cwd: Path
    env: dict[str, str]


@dataclass
class PtySession:
    terminal_id: str
    pty_backend: object  # WindowsPtyBackend or UnixPtyBackend
    pid: int | None
    cols: int
    rows: int
    status: str  # "running" | "exited"
    output_buffer: collections.deque = field(default_factory=lambda: collections.deque())
    _output_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _reader_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _output_callback: Callable[[str, bytes], None] | None = field(default=None, init=False, repr=False)
    _total_bytes: int = field(default=0, init=False, repr=False)


def _create_pty_backend(system: str | None = None):
    """Create platform-appropriate PTY backend. Returns (backend, available)."""
    system = system or platform.system()
    if system == "Windows":
        from backend.windows.pty_platform_windows import WindowsPtyBackend, winpty_available

        if not winpty_available():
            return None, False
        return WindowsPtyBackend(), True
    else:
        from backend.linux.pty_platform_unix import UnixPtyBackend

        return UnixPtyBackend(), True


def pty_available(system: str | None = None) -> bool:
    """Check if PTY support is available on this platform."""
    _, available = _create_pty_backend(system)
    return available


class PtySessionManager:
    """Manages PTY session lifecycle: create, write, resize, destroy."""

    def __init__(self, output_callback: Callable[[str, bytes], None] | None = None) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._lock = threading.Lock()
        self._output_callback = output_callback

    @property
    def sessions(self) -> dict[str, PtySession]:
        with self._lock:
            return dict(self._sessions)

    def create(self, config: PtySessionConfig) -> PtySession:
        backend, available = _create_pty_backend()
        if not available or backend is None:
            raise RuntimeError("PTY support is not available on this platform")

        backend.spawn(
            config.shell,
            config.cols,
            config.rows,
            str(config.cwd),
            config.env,
        )
        pid = backend.pid()

        session = PtySession(
            terminal_id=config.terminal_id,
            pty_backend=backend,
            pid=pid,
            cols=config.cols,
            rows=config.rows,
            status="running",
        )
        session._output_callback = self._output_callback

        with self._lock:
            self._sessions[config.terminal_id] = session

        # Start reader thread
        self._start_reader(session)

        logger.info(
            "pty_session.created terminal_id=%s pid=%s shell=%s cols=%s rows=%s",
            config.terminal_id,
            pid,
            config.shell,
            config.cols,
            config.rows,
        )
        return session

    def write(self, terminal_id: str, data: str) -> None:
        session = self._get_session(terminal_id)
        if session.status != "running":
            logger.warning("pty_session.write_to_exited terminal_id=%s", terminal_id)
            return
        try:
            session.pty_backend.write(data.encode("utf-8", errors="replace"))
        except (OSError, RuntimeError) as exc:
            logger.warning("pty_session.write_error terminal_id=%s error=%s", terminal_id, exc)

    def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        session = self._get_session(terminal_id)
        if session.status != "running":
            return
        try:
            session.pty_backend.resize(cols, rows)
            session.cols = cols
            session.rows = rows
        except (OSError, RuntimeError) as exc:
            logger.warning("pty_session.resize_error terminal_id=%s error=%s", terminal_id, exc)

    def destroy(self, terminal_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(terminal_id, None)
        if session is None:
            return

        session._stop_event.set()
        if session._reader_thread is not None:
            session._reader_thread.join(timeout=3)
        try:
            session.pty_backend.close()
        except (OSError, RuntimeError) as exc:
            logger.warning("pty_session.close_error terminal_id=%s error=%s", terminal_id, exc)
        session.status = "exited"
        logger.info("pty_session.destroyed terminal_id=%s", terminal_id)

    def get_output(self, terminal_id: str) -> bytes:
        """Drain all pending output from the session's buffer."""
        session = self._get_session(terminal_id)
        chunks = []
        with session._output_lock:
            while session.output_buffer:
                chunks.append(session.output_buffer.popleft())
        return b"".join(chunks)

    def has_session(self, terminal_id: str) -> bool:
        with self._lock:
            return terminal_id in self._sessions

    def is_alive(self, terminal_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(terminal_id)
        if session is None:
            return False
        return session.status == "running"

    def destroy_all(self) -> None:
        with self._lock:
            terminal_ids = list(self._sessions.keys())
        for terminal_id in terminal_ids:
            self.destroy(terminal_id)

    def _get_session(self, terminal_id: str) -> PtySession:
        with self._lock:
            session = self._sessions.get(terminal_id)
        if session is None:
            raise KeyError(f"No PTY session for terminal: {terminal_id}")
        return session

    def _start_reader(self, session: PtySession) -> None:
        def reader_loop():
            backend = session.pty_backend
            while not session._stop_event.is_set():
                if not backend.is_alive():
                    session.status = "exited"
                    logger.info("pty_session.process_exited terminal_id=%s", session.terminal_id)
                    break
                try:
                    chunk = backend.read(4096)
                except (OSError, RuntimeError):
                    session.status = "exited"
                    break
                if chunk:
                    with session._output_lock:
                        session.output_buffer.append(chunk)
                        session._total_bytes += len(chunk)
                        # Trim if buffer exceeds limit
                        while session._total_bytes > PTY_OUTPUT_BUFFER_MAX and session.output_buffer:
                            removed = session.output_buffer.popleft()
                            session._total_bytes -= len(removed)
                    if session._output_callback is not None:
                        try:
                            session._output_callback(session.terminal_id, chunk)
                        except Exception:
                            pass
                else:
                    time.sleep(0.02)  # Small sleep to avoid busy-wait when no data

        thread = threading.Thread(
            target=reader_loop,
            name=f"pty-reader-{session.terminal_id}",
            daemon=True,
        )
        session._reader_thread = thread
        thread.start()
