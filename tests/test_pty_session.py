"""Tests for PTY session manager."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.pty_session import PtySessionConfig, PtySessionManager, pty_available


class FakePtyBackend:
    """Fake PTY backend for unit tests."""

    def __init__(self) -> None:
        self._alive = True
        self._output_queue: list[bytes] = []
        self._written: list[bytes] = []
        self._cols = 80
        self._rows = 24
        self._pid = 12345

    def spawn(self, argv: list[str], cols: int, rows: int, cwd: str, env: dict[str, str]) -> None:
        self._cols = cols
        self._rows = rows

    def read(self, size: int = 4096) -> bytes:
        if self._output_queue:
            return self._output_queue.pop(0)
        return b""

    def write(self, data: bytes) -> None:
        self._written.append(data)

    def resize(self, cols: int, rows: int) -> None:
        self._cols = cols
        self._rows = rows

    def close(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def pid(self) -> int | None:
        return self._pid


class TestPtySessionManagerLifecycle(unittest.TestCase):
    """Unit tests for PtySessionManager create/destroy lifecycle."""

    def _make_config(self, terminal_id: str = "term-abc123") -> PtySessionConfig:
        return PtySessionConfig(
            terminal_id=terminal_id,
            argv=["cmd.exe"],
            cols=80,
            rows=24,
            cwd=Path(tempfile.gettempdir()),
            env={},
        )

    @patch("backend.pty_session._create_pty_backend")
    def test_create_session(self, mock_create: MagicMock) -> None:
        backend = FakePtyBackend()
        mock_create.return_value = (backend, True)

        manager = PtySessionManager()
        config = self._make_config()
        session = manager.create(config)

        self.assertEqual(session.terminal_id, "term-abc123")
        self.assertEqual(session.status, "running")
        self.assertEqual(session.pid, 12345)
        self.assertTrue(manager.has_session("term-abc123"))
        self.assertTrue(manager.is_alive("term-abc123"))

        # Cleanup
        manager.destroy("term-abc123")

    @patch("backend.pty_session._create_pty_backend")
    def test_destroy_session(self, mock_create: MagicMock) -> None:
        backend = FakePtyBackend()
        mock_create.return_value = (backend, True)

        manager = PtySessionManager()
        manager.create(self._make_config())

        manager.destroy("term-abc123")
        self.assertFalse(manager.has_session("term-abc123"))
        self.assertFalse(backend._alive)

    @patch("backend.pty_session._create_pty_backend")
    def test_destroy_nonexistent_is_noop(self, mock_create: MagicMock) -> None:
        manager = PtySessionManager()
        manager.destroy("nonexistent")  # Should not raise

    @patch("backend.pty_session._create_pty_backend")
    def test_write_to_session(self, mock_create: MagicMock) -> None:
        backend = FakePtyBackend()
        mock_create.return_value = (backend, True)

        manager = PtySessionManager()
        manager.create(self._make_config())

        manager.write("term-abc123", "hello\n")
        self.assertEqual(backend._written, [b"hello\n"])

        manager.destroy("term-abc123")

    @patch("backend.pty_session._create_pty_backend")
    def test_resize_session(self, mock_create: MagicMock) -> None:
        backend = FakePtyBackend()
        mock_create.return_value = (backend, True)

        manager = PtySessionManager()
        session = manager.create(self._make_config())

        manager.resize("term-abc123", 120, 40)
        self.assertEqual(session.cols, 120)
        self.assertEqual(session.rows, 40)
        self.assertEqual(backend._cols, 120)
        self.assertEqual(backend._rows, 40)

        manager.destroy("term-abc123")

    @patch("backend.pty_session._create_pty_backend")
    def test_get_output_drains_buffer(self, mock_create: MagicMock) -> None:
        backend = FakePtyBackend()
        mock_create.return_value = (backend, True)

        manager = PtySessionManager()
        session = manager.create(self._make_config())

        # Simulate output being added by reader thread
        with session._output_lock:
            session.output_buffer.append(b"line1\r\n")
            session.output_buffer.append(b"line2\r\n")

        output = manager.get_output("term-abc123")
        self.assertEqual(output, b"line1\r\nline2\r\n")

        # Second drain should be empty
        output2 = manager.get_output("term-abc123")
        self.assertEqual(output2, b"")

        manager.destroy("term-abc123")

    @patch("backend.pty_session._create_pty_backend")
    def test_write_to_unknown_raises(self, mock_create: MagicMock) -> None:
        manager = PtySessionManager()
        with self.assertRaises(KeyError):
            manager.write("nonexistent", "data")

    @patch("backend.pty_session._create_pty_backend")
    def test_create_fails_when_unavailable(self, mock_create: MagicMock) -> None:
        mock_create.return_value = (None, False)
        manager = PtySessionManager()
        with self.assertRaises(RuntimeError):
            manager.create(self._make_config())

    @patch("backend.pty_session._create_pty_backend")
    def test_destroy_all(self, mock_create: MagicMock) -> None:
        backends = []
        def make_backend():
            b = FakePtyBackend()
            backends.append(b)
            return (b, True)

        mock_create.side_effect = [make_backend(), make_backend()]

        manager = PtySessionManager()
        manager.create(self._make_config("term-1"))
        manager.create(self._make_config("term-2"))

        self.assertEqual(len(manager.sessions), 2)
        manager.destroy_all()
        self.assertEqual(len(manager.sessions), 0)
        for b in backends:
            self.assertFalse(b._alive)

    @patch("backend.pty_session._create_pty_backend")
    def test_output_callback_invoked(self, mock_create: MagicMock) -> None:
        backend = FakePtyBackend()
        backend._output_queue.append(b"hello")
        mock_create.return_value = (backend, True)

        received: list[tuple[str, bytes]] = []
        def callback(terminal_id: str, data: bytes) -> None:
            received.append((terminal_id, data))

        manager = PtySessionManager(output_callback=callback)
        manager.create(self._make_config())

        # Give reader thread time to process
        time.sleep(0.1)
        manager.destroy("term-abc123")

        self.assertTrue(len(received) > 0)
        self.assertEqual(received[0][0], "term-abc123")
        self.assertEqual(received[0][1], b"hello")


class TestPtyAvailable(unittest.TestCase):
    """Test pty_available detection."""

    @patch("backend.pty_session._create_pty_backend")
    def test_pty_available_returns_true_when_backend_available(self, mock_create: MagicMock) -> None:
        mock_create.return_value = (MagicMock(), True)
        self.assertTrue(pty_available())

    @patch("backend.pty_session._create_pty_backend")
    def test_pty_available_returns_false_when_unavailable(self, mock_create: MagicMock) -> None:
        mock_create.return_value = (None, False)
        self.assertFalse(pty_available())


if __name__ == "__main__":
    unittest.main()
