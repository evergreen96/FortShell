from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.models import (
    MAX_TERMINAL_COMMAND_HISTORY,
    MAX_TERMINAL_INBOX_ENTRIES,
    TerminalEventWatch,
    TerminalSession,
)
from ai_ide.terminal_inbox import TerminalInboxEntry
from ai_ide.terminal_state_store import TerminalStateStore


class TerminalStateStoreTests(unittest.TestCase):
    def test_load_returns_empty_state_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TerminalStateStore(Path(temp_dir) / "terminals" / "state.json")

            snapshot = store.load()

        self.assertEqual({}, snapshot.terminals)
        self.assertEqual({}, snapshot.event_watches)
        self.assertEqual({}, snapshot.bridge_watches)

    def test_save_and_load_round_trip_terminals_watches_and_bridges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "terminals" / "state.json"
            store = TerminalStateStore(state_path)
            terminals = {
                "term-1234": TerminalSession(
                    terminal_id="term-1234",
                    name="work",
                    created_at="2026-03-07T00:00:00Z",
                    transport="runner",
                    runner_mode="projected",
                    status="active",
                    stale_reason=None,
                    execution_session_id="sess-1234",
                    bound_agent_run_id="run-1234",
                    command_history=["echo hello"],
                    inbox=[
                        TerminalInboxEntry(
                            kind="runtime-event",
                            text="[evt-1] terminal.command.completed",
                            created_at="2026-03-07T00:00:03Z",
                            event_id="evt-1",
                            event_kind="terminal.command.completed",
                            source_type="terminal",
                            source_id="term-1234",
                            payload={"ok": True},
                        )
                    ],
                )
            }
            event_watches = {
                "term-1234": [
                    TerminalEventWatch(
                        watch_id="sub-000001",
                        consumer_id="terminal:term-1234:watch:sub-000001",
                        kind_prefix="agent.run",
                        source_type="agent-run",
                        source_id="run-1234",
                        created_at="2026-03-07T00:00:01Z",
                        updated_at="2026-03-07T00:00:02Z",
                    )
                ]
            }
            bridge_watches = {"term-1234": "sub-000001"}

            store.save(terminals, event_watches, bridge_watches)
            snapshot = store.load()

        self.assertEqual("work", snapshot.terminals["term-1234"].name)
        self.assertEqual("run-1234", snapshot.terminals["term-1234"].bound_agent_run_id)
        self.assertEqual("runtime-event", snapshot.terminals["term-1234"].inbox[0].kind)
        self.assertEqual("terminal.command.completed", snapshot.terminals["term-1234"].inbox[0].event_kind)
        self.assertEqual("sub-000001", snapshot.event_watches["term-1234"][0].watch_id)
        self.assertEqual("agent-run", snapshot.event_watches["term-1234"][0].source_type)
        self.assertEqual("sub-000001", snapshot.bridge_watches["term-1234"])

    def test_load_upgrades_legacy_string_inbox_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "terminals" / "state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                """
{
  "terminals": [
    {
      "terminal_id": "term-1234",
      "name": "work",
      "created_at": "2026-03-07T00:00:00Z",
      "transport": "runner",
      "runner_mode": "projected",
      "status": "active",
      "stale_reason": null,
      "execution_session_id": "sess-1234",
      "bound_agent_run_id": null,
      "command_history": [],
      "inbox": ["legacy text"]
    }
  ]
}
                """.strip(),
                encoding="utf-8",
            )
            store = TerminalStateStore(state_path)

            snapshot = store.load()

        self.assertEqual("legacy", snapshot.terminals["term-1234"].inbox[0].kind)
        self.assertEqual("legacy text", snapshot.terminals["term-1234"].inbox[0].text)

    def test_load_reuses_cached_snapshot_when_file_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "terminals" / "state.json"
            store = TerminalStateStore(state_path)
            terminals = {
                "term-1234": TerminalSession(
                    terminal_id="term-1234",
                    name="work",
                    created_at="2026-03-07T00:00:00Z",
                    transport="runner",
                    runner_mode="projected",
                    status="active",
                    stale_reason=None,
                    execution_session_id="sess-1234",
                    bound_agent_run_id=None,
                    command_history=["echo hello"],
                    inbox=[],
                )
            }

            store.save(terminals, {}, {})
            first_snapshot = store.load()

            with patch.object(Path, "read_text", side_effect=AssertionError("should reuse cached snapshot")):
                second_snapshot = store.load()

        self.assertEqual(first_snapshot.terminals["term-1234"].name, second_snapshot.terminals["term-1234"].name)
        self.assertEqual(
            first_snapshot.terminals["term-1234"].snapshot_command_history(),
            second_snapshot.terminals["term-1234"].snapshot_command_history(),
        )

    def test_load_trims_oversized_command_history_and_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "terminals" / "state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            command_history = [f"cmd-{index}" for index in range(MAX_TERMINAL_COMMAND_HISTORY + 5)]
            inbox = [
                {"kind": "legacy", "text": f"msg-{index}", "created_at": None}
                for index in range(MAX_TERMINAL_INBOX_ENTRIES + 5)
            ]
            state_path.write_text(
                json.dumps(
                    {
                        "terminals": [
                            {
                                "terminal_id": "term-1234",
                                "name": "work",
                                "created_at": "2026-03-07T00:00:00Z",
                                "transport": "runner",
                                "runner_mode": "projected",
                                "status": "active",
                                "stale_reason": None,
                                "execution_session_id": None,
                                "bound_agent_run_id": None,
                                "command_history": command_history,
                                "inbox": inbox,
                            }
                        ]
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            store = TerminalStateStore(state_path)

            snapshot = store.load()

        terminal = snapshot.terminals["term-1234"]
        self.assertEqual(MAX_TERMINAL_COMMAND_HISTORY, len(terminal.snapshot_command_history()))
        self.assertEqual("cmd-5", terminal.snapshot_command_history()[0])
        self.assertEqual(MAX_TERMINAL_INBOX_ENTRIES, len(terminal.snapshot_inbox()))
        self.assertEqual("msg-5", terminal.snapshot_inbox()[0].text)


if __name__ == "__main__":
    unittest.main()
