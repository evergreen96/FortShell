"""Tests for PTY integration in TerminalManager."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_ide.models import TerminalSession, UsageMetrics
from ai_ide.terminal import TerminalManager


class FakeRunnerManager:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def run_in_mode(self, mode, command, execution_session_id=None):
        from ai_ide.runner import RunnerResult

        self.calls.append((mode, command, execution_session_id))
        return RunnerResult(
            mode=mode,
            backend=f"fake-{mode}",
            returncode=0,
            stdout=f"runner:{command}",
            stderr="",
            working_directory="/projection",
        )


class TestTerminalManagerIoMode(unittest.TestCase):
    """Test io_mode field handling in TerminalManager."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = Path(self.tmpdir) / "terminals" / "state.json"
        self.project_root = Path(self.tmpdir) / "project"
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.metrics = UsageMetrics()

    def _make_manager(self) -> TerminalManager:
        return TerminalManager(
            self.project_root,
            self.metrics,
            runner_manager=FakeRunnerManager(),
            state_path=self.state_path,
        )

    def test_create_terminal_default_command_mode(self) -> None:
        mgr = self._make_manager()
        session = mgr.create_terminal(name="test", transport="host")
        self.assertEqual(session.io_mode, "command")

    def test_create_terminal_invalid_io_mode_raises(self) -> None:
        mgr = self._make_manager()
        with self.assertRaises(ValueError):
            mgr.create_terminal(name="test", transport="host", io_mode="invalid")

    @patch("ai_ide.terminal.pty_available", return_value=False)
    def test_create_pty_terminal_fallback_when_unavailable(self, mock_avail: MagicMock) -> None:
        mgr = self._make_manager()
        session = mgr.create_terminal(name="test", transport="host", io_mode="pty")
        # Should fall back to command mode
        self.assertEqual(session.io_mode, "command")

    def test_io_mode_persisted_and_restored(self) -> None:
        mgr = self._make_manager()
        session = mgr.create_terminal(name="test", transport="host")
        session.io_mode = "command"  # explicitly set
        mgr._persist_state()

        # Load in a new manager and verify
        mgr2 = self._make_manager()
        restored = mgr2.terminals.get(session.terminal_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.io_mode, "command")

    def test_io_mode_in_inspection(self) -> None:
        mgr = self._make_manager()
        session = mgr.create_terminal(name="test", transport="host")
        inspection = mgr.inspect_terminal(session.terminal_id)
        d = inspection.to_dict()
        self.assertEqual(d["io_mode"], "command")

    def test_pty_terminals_stale_on_restart(self) -> None:
        """PTY terminals should be marked stale when state is loaded (simulating restart)."""
        mgr = self._make_manager()
        session = mgr.create_terminal(name="test", transport="host")

        # Manually set io_mode to pty in persisted state (simulating a PTY terminal that was running)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state_data = json.loads(self.state_path.read_text(encoding="utf-8"))
        for term in state_data["terminals"]:
            if term["terminal_id"] == session.terminal_id:
                term["io_mode"] = "pty"
                term["status"] = "active"
        self.state_path.write_text(json.dumps(state_data), encoding="utf-8")

        # Create new manager (simulates restart)
        mgr2 = self._make_manager()
        restored = mgr2.terminals.get(session.terminal_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.status, "stale")
        self.assertIn("app restarted", restored.stale_reason)

    def test_write_to_pty_rejects_command_mode(self) -> None:
        mgr = self._make_manager()
        session = mgr.create_terminal(name="test", transport="host")
        with self.assertRaises(ValueError):
            mgr.write_to_pty(session.terminal_id, "hello")

    def test_resize_pty_rejects_command_mode(self) -> None:
        mgr = self._make_manager()
        session = mgr.create_terminal(name="test", transport="host")
        with self.assertRaises(ValueError):
            mgr.resize_pty(session.terminal_id, 120, 40)

    def test_get_pty_output_rejects_command_mode(self) -> None:
        mgr = self._make_manager()
        session = mgr.create_terminal(name="test", transport="host")
        with self.assertRaises(ValueError):
            mgr.get_pty_output(session.terminal_id)

    def test_destroy_terminal(self) -> None:
        mgr = self._make_manager()
        session = mgr.create_terminal(name="test", transport="host")
        mgr.destroy_terminal(session.terminal_id)
        self.assertEqual(session.status, "stale")
        self.assertEqual(session.stale_reason, "terminal destroyed")


class TestTerminalStateStoreIoMode(unittest.TestCase):
    """Test io_mode backward compatibility in state store."""

    def test_missing_io_mode_defaults_to_command(self) -> None:
        """Legacy state files without io_mode should default to 'command'."""
        from ai_ide.terminal_state_store import TerminalStateStore

        tmpdir = tempfile.mkdtemp()
        state_path = Path(tmpdir) / "state.json"
        # Write state without io_mode field
        state_data = {
            "terminals": [
                {
                    "terminal_id": "term-legacy",
                    "name": "legacy",
                    "created_at": "2024-01-01T00:00:00Z",
                    "transport": "host",
                    "runner_mode": None,
                    "status": "active",
                    "stale_reason": None,
                    "execution_session_id": None,
                    "bound_agent_run_id": None,
                    "command_history": [],
                    "inbox": [],
                    # No io_mode field!
                }
            ],
            "event_watches": {},
            "bridge_watches": {},
        }
        state_path.write_text(json.dumps(state_data), encoding="utf-8")

        store = TerminalStateStore(state_path)
        snapshot = store.load()
        terminal = snapshot.terminals["term-legacy"]
        self.assertEqual(terminal.io_mode, "command")


if __name__ == "__main__":
    unittest.main()
