from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.agent_state_store import AgentRuntimeStateStore
from ai_ide.models import MAX_AGENT_RUN_STREAM_BYTES, AgentRunRecord, AgentRunWatch


class AgentRuntimeStateStoreTests(unittest.TestCase):
    def test_load_returns_empty_state_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentRuntimeStateStore(Path(temp_dir) / "agents" / "state.json")

            runs, watches = store.load()

        self.assertEqual([], runs)
        self.assertEqual({}, watches)

    def test_save_and_load_round_trip_runs_and_watches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "agents" / "state.json"
            store = AgentRuntimeStateStore(state_path)
            runs = [
                AgentRunRecord(
                    run_id="run-1234",
                    agent_session_id="agent-1234",
                    execution_session_id="exec-1234",
                    agent_kind="codex",
                    runner_mode="projected",
                    backend="projected",
                    io_mode="pipe",
                    transport_status="degraded",
                    argv=["codex", "--version"],
                    created_at="2026-03-07T00:00:00Z",
                    ended_at="2026-03-07T00:00:01Z",
                    pid=None,
                    returncode=0,
                    status="completed",
                    stdout="ok",
                    stderr="",
                )
            ]
            watches = {
                "watch-1234": AgentRunWatch(
                    watch_id="watch-1234",
                    run_id="run-1234",
                    consumer_id="agent-run:run-1234:watch:watch-1234",
                    created_at="2026-03-07T00:00:02Z",
                    name="observer",
                    updated_at="2026-03-07T00:00:03Z",
                )
            }

            store.save(runs, watches)
            loaded_runs, loaded_watches = store.load()

        self.assertEqual("run-1234", loaded_runs[0].run_id)
        self.assertEqual("degraded", loaded_runs[0].transport_status)
        self.assertEqual("watch-1234", loaded_watches["watch-1234"].watch_id)
        self.assertEqual("observer", loaded_watches["watch-1234"].name)

    def test_load_reuses_cached_state_when_file_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "agents" / "state.json"
            store = AgentRuntimeStateStore(state_path)
            runs = [
                AgentRunRecord(
                    run_id="run-1234",
                    agent_session_id="agent-1234",
                    execution_session_id="exec-1234",
                    agent_kind="codex",
                    runner_mode="projected",
                    backend="projected",
                    io_mode="pipe",
                    transport_status="native",
                    argv=["codex"],
                    created_at="2026-03-07T00:00:00Z",
                    ended_at=None,
                    pid=None,
                    returncode=-1,
                    status="running",
                    stdout="",
                    stderr="",
                )
            ]

            store.save(runs, {})
            first_runs, first_watches = store.load()

            with patch.object(Path, "read_text", side_effect=AssertionError("should reuse cached state")):
                second_runs, second_watches = store.load()

        self.assertEqual(first_runs[0].run_id, second_runs[0].run_id)
        self.assertEqual(first_watches, second_watches)

    def test_load_trims_oversized_streams(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "agents" / "state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            oversized = "x" * (MAX_AGENT_RUN_STREAM_BYTES + 10)
            state_path.write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "run-1234",
                                "agent_session_id": "agent-1234",
                                "execution_session_id": "exec-1234",
                                "agent_kind": "codex",
                                "runner_mode": "projected",
                                "backend": "python",
                                "io_mode": "pipe",
                                "transport_status": "native",
                                "argv": ["python", "-c", "print('hello')"],
                                "created_at": "2026-03-07T00:00:00Z",
                                "ended_at": None,
                                "pid": 1234,
                                "returncode": 0,
                                "status": "running",
                                "stdout": oversized,
                                "stderr": oversized,
                            }
                        ],
                        "run_watches": [],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            store = AgentRuntimeStateStore(state_path)

            runs, _ = store.load()

        self.assertEqual(MAX_AGENT_RUN_STREAM_BYTES, len(runs[0].stdout))
        self.assertEqual(MAX_AGENT_RUN_STREAM_BYTES, len(runs[0].stderr))


if __name__ == "__main__":
    unittest.main()
