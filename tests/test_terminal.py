from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.agent_run_inspection_service import AgentProcessInspection, AgentRunInspection
from backend.events import EventBus
from core.models import AgentRunRecord, TerminalEventWatch, TerminalSession, UsageMetrics
from backend.runner import RunnerResult
from backend.terminal_inbox import TerminalInboxEntry
from backend.terminal import MAX_TERMINALS, TerminalManager
from backend.terminal_watch_manager import MAX_TERMINAL_EVENT_WATCHES


class FakeRunnerManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def run_in_mode(
        self,
        mode: str,
        command: str,
        execution_session_id: str | None = None,
    ) -> RunnerResult:
        self.calls.append((mode, command, execution_session_id))
        return RunnerResult(
            mode=mode,
            backend=f"fake-{mode}",
            returncode=0,
            stdout=f"runner:{command}",
            stderr="",
            working_directory="/projection",
        )


class FakeAgentRuntime:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.runs: dict[str, object] = {}
        self.inspections: dict[str, AgentRunInspection] = {}

    def register_run(
        self,
        run_id: str,
        *,
        execution_session_id: str = "sess-left",
        status: str = "running",
        backend: str = "restricted-host-helper",
        process_source: str = "helper-control",
        process_state: str = "running",
        process_pid: int = 4321,
        process_returncode: int | None = None,
    ) -> None:
        run = type(
            "Run",
            (),
            {
                "run_id": run_id,
                "execution_session_id": execution_session_id,
                "agent_kind": "codex",
            },
        )()
        self.runs[run_id] = run
        self.inspections[run_id] = AgentRunInspection(
            record=AgentRunRecord(
                run_id=run_id,
                agent_session_id="agent-1",
                execution_session_id=execution_session_id,
                agent_kind="codex",
                runner_mode="strict",
                backend=backend,
                io_mode="pipe",
                transport_status="ok",
                argv=["codex"],
                created_at="2026-03-08T00:00:00Z",
                ended_at=None,
                pid=process_pid,
                returncode=0,
                status=status,
                stdout="",
                stderr="",
            ),
            process=AgentProcessInspection(
                source=process_source,
                state=process_state,
                pid=process_pid,
                returncode=process_returncode,
                backend=backend,
            ),
        )

    def get_run(self, run_id: str):
        return self.runs[run_id]

    def send_input(self, run_id: str, text: str):
        self.sent.append((run_id, text))
        return self.runs[run_id]

    def inspect_run(self, run_id: str) -> AgentRunInspection:
        return self.inspections[run_id]

    def list_run_inspections(self) -> list[AgentRunInspection]:
        return list(self.inspections.values())


class TerminalManagerTests(unittest.TestCase):
    def test_runner_terminal_routes_through_runner_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_runner = FakeRunnerManager()
            event_bus = EventBus()
            manager = TerminalManager(Path(temp_dir), UsageMetrics(), fake_runner, event_bus=event_bus)
            left = manager.create_terminal(
                "left",
                execution_session_id="sess-left",
                transport="runner",
                runner_mode="projected",
            )
            right = manager.create_terminal(
                "right",
                execution_session_id="sess-right",
                transport="runner",
                runner_mode="strict",
            )

            manager.send_message(left.terminal_id, right.terminal_id, "hello")
            output = manager.run_command(left.terminal_id, "echo hello")

            self.assertEqual([f"from {left.terminal_id}: hello"], [entry.text for entry in right.inbox])
            self.assertIn("[transport=runner mode=projected", output)
            self.assertIn("runner:echo hello", output)
            self.assertEqual(1, manager.metrics.terminal_runs)
            self.assertEqual([("projected", "echo hello", "sess-left")], fake_runner.calls)
            self.assertEqual("sess-left", left.execution_session_id)
            self.assertEqual("sess-right", right.execution_session_id)
            self.assertEqual("runner", left.transport)
            self.assertEqual("projected", left.runner_mode)
            self.assertEqual("active", left.status)
            self.assertEqual(
                ["terminal.message.sent", "terminal.command.completed"],
                [event.kind for event in event_bus.list_events()],
            )

    def test_host_terminal_runs_unsafe_shell_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TerminalManager(Path(temp_dir), UsageMetrics())
            terminal = manager.create_terminal("host", transport="host")

            output = manager.run_command(terminal.terminal_id, "echo hello")

            self.assertIn("[transport=host mode=host", output)
            self.assertIn("unsafe=true", output)
            self.assertIn("hello", output.lower())
            self.assertIsNone(terminal.execution_session_id)
            self.assertIsNone(terminal.runner_mode)
            self.assertEqual("active", terminal.status)

    def test_runner_terminal_becomes_stale_after_execution_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_runner = FakeRunnerManager()
            event_bus = EventBus()
            manager = TerminalManager(Path(temp_dir), UsageMetrics(), fake_runner, event_bus=event_bus)
            terminal = manager.create_terminal(
                "work",
                execution_session_id="sess-old",
                transport="runner",
                runner_mode="projected",
            )

            manager.mark_execution_session_stale("sess-old", "terminal bound to stale execution session sess-old")
            output = manager.run_command(terminal.terminal_id, "echo hello")

            self.assertEqual("stale", terminal.status)
            self.assertIn("blocked=true", output)
            self.assertIn("stale execution session", output)
            self.assertEqual([], fake_runner.calls)
            self.assertEqual(
                ["terminal.command.blocked"],
                [event.kind for event in event_bus.list_events()],
            )

    def test_terminal_can_subscribe_to_event_bus_and_read_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event_bus = EventBus()
            manager = TerminalManager(Path(temp_dir), UsageMetrics(), event_bus=event_bus)
            terminal = manager.create_terminal("watcher", transport="host")

            subscription_id = manager.subscribe_to_events(
                terminal.terminal_id,
                kind_prefix="agent.run",
                source_type="agent-run",
                source_id="run-1",
            )
            event_bus.publish("agent.run.started", source_type="agent-run", source_id="run-1", payload={"x": 1})
            event_bus.publish("agent.run.started", source_type="agent-run", source_id="run-2", payload={"x": 2})
            inbox = manager.read_inbox(terminal.terminal_id)

            self.assertTrue(subscription_id.startswith("sub-"))
            self.assertEqual(1, len(inbox))
            self.assertIn("agent.run.started", inbox[0])
            self.assertIn("run-1", inbox[0])
            self.assertEqual("runtime-event", terminal.inbox[0].kind)
            self.assertEqual("agent.run.started", terminal.inbox[0].event_kind)

    def test_terminal_watch_uses_persisted_consumer_cursor_without_replaying_old_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event_bus = EventBus()
            manager = TerminalManager(Path(temp_dir), UsageMetrics(), event_bus=event_bus)
            terminal = manager.create_terminal("watcher", transport="host")
            event_bus.publish("agent.run.started", source_type="agent-run", source_id="run-1", payload={"old": True})

            watch_id = manager.subscribe_to_events(
                terminal.terminal_id,
                kind_prefix="agent.run",
                source_type="agent-run",
                source_id="run-1",
            )
            event_bus.publish("agent.run.stdout", source_type="agent-run", source_id="run-1", payload={"new": True})
            inbox = manager.read_inbox(terminal.terminal_id)

            self.assertEqual(1, len(inbox))
            self.assertIn("agent.run.stdout", inbox[0])
            self.assertNotIn("agent.run.started", inbox[0])
            self.assertEqual(
                "evt-000002",
                event_bus.get_cursor(f"terminal:{terminal.terminal_id}:watch:{watch_id}"),
            )

    def test_terminal_state_and_watch_restore_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            events_root = base / "runtime" / "events"
            state_path = base / "runtime" / "terminals" / "state.json"
            first_bus = EventBus(
                log_path=events_root / "events.jsonl",
                cursor_path=events_root / "cursors.json",
            )
            first_manager = TerminalManager(
                base,
                UsageMetrics(),
                event_bus=first_bus,
                state_path=state_path,
            )
            terminal = first_manager.create_terminal("watcher", transport="host")
            first_manager.subscribe_to_events(
                terminal.terminal_id,
                kind_prefix="agent.run",
                source_type="agent-run",
                source_id="run-1",
            )
            first_bus.publish("agent.run.started", source_type="agent-run", source_id="run-1", payload={"step": 1})
            self.assertIn("agent.run.started", first_manager.read_inbox(terminal.terminal_id)[0])

            second_bus = EventBus(
                log_path=events_root / "events.jsonl",
                cursor_path=events_root / "cursors.json",
            )
            second_manager = TerminalManager(
                base,
                UsageMetrics(),
                event_bus=second_bus,
                state_path=state_path,
            )
            second_bus.publish("agent.run.completed", source_type="agent-run", source_id="run-1", payload={"step": 2})
            inbox = second_manager.read_inbox(terminal.terminal_id)

            self.assertEqual(1, len(second_manager.list_terminals()))
            self.assertIn("agent.run.started", inbox[0])
            self.assertIn("agent.run.completed", inbox[-1])

    def test_terminal_can_attach_to_agent_run_and_send_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event_bus = EventBus()
            fake_agent_runtime = FakeAgentRuntime()
            fake_agent_runtime.register_run("run-1")
            manager = TerminalManager(
                Path(temp_dir),
                UsageMetrics(),
                agent_runtime=fake_agent_runtime,
                event_bus=event_bus,
            )
            terminal = manager.create_terminal(
                "bridge",
                execution_session_id="sess-left",
                transport="runner",
                runner_mode="projected",
            )

            manager.attach_to_agent_run(terminal.terminal_id, "run-1")
            run_id = manager.send_input_to_agent(terminal.terminal_id, "hello-run")
            event_bus.publish("agent.run.stdout", source_type="agent-run", source_id="run-1", payload={"chunk": "ok"})
            inbox = manager.read_inbox(terminal.terminal_id)

            self.assertEqual("run-1", terminal.bound_agent_run_id)
            self.assertEqual("run-1", run_id)
            self.assertEqual([("run-1", "hello-run")], fake_agent_runtime.sent)
            self.assertIn("agent.run.stdout", inbox[-1])
            self.assertEqual("runtime-event", terminal.inbox[-1].kind)
            self.assertEqual(
                [
                    "terminal.agent_bridge.attached",
                    "terminal.agent_input.sent",
                    "agent.run.stdout",
                ],
                [event.kind for event in event_bus.list_events()],
            )

    def test_terminal_inspection_enriches_bound_run_listing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_agent_runtime = FakeAgentRuntime()
            fake_agent_runtime.register_run("run-1", process_pid=9001)
            manager = TerminalManager(
                Path(temp_dir),
                UsageMetrics(),
                agent_runtime=fake_agent_runtime,
                event_bus=EventBus(),
            )
            terminal = manager.create_terminal(
                "bridge",
                execution_session_id="sess-left",
                transport="runner",
                runner_mode="strict",
            )

            manager.attach_to_agent_run(terminal.terminal_id, "run-1")
            inspection = manager.inspect_terminal(terminal.terminal_id)
            listing = manager.list_terminal_inspections()

            self.assertEqual("run-1", inspection.bound_run.run_id if inspection.bound_run else None)
            self.assertEqual("helper-control", inspection.bound_run.process_source if inspection.bound_run else None)
            self.assertEqual("running", inspection.bound_run.process_state if inspection.bound_run else None)
            self.assertEqual(9001, inspection.bound_run.process_pid if inspection.bound_run else None)
            self.assertEqual("run-1", listing[0].bound_run.run_id if listing[0].bound_run else None)

    def test_read_inbox_snapshot_and_watch_snapshots_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event_bus = EventBus()
            manager = TerminalManager(Path(temp_dir), UsageMetrics(), event_bus=event_bus)
            terminal = manager.create_terminal("watcher", transport="host")

            watch = manager.watch_events(
                terminal.terminal_id,
                kind_prefix="agent.run",
                source_type="agent-run",
                source_id="run-1",
            )
            event_bus.publish("agent.run.started", source_type="agent-run", source_id="run-1", payload={"step": 1})
            inbox = manager.read_inbox_snapshot(terminal.terminal_id)
            watches = manager.list_watch_snapshots(terminal.terminal_id)

            self.assertEqual(terminal.terminal_id, watch.to_dict()["terminal_id"])
            self.assertEqual("agent.run", watch.to_dict()["kind_prefix"])
            self.assertEqual(terminal.terminal_id, inbox.to_dict()["terminal_id"])
            self.assertEqual([watch.watch_id], inbox.to_dict()["watch_ids"])
            self.assertIn("agent.run.started", inbox.to_dict()["messages"][0])
            self.assertEqual("runtime-event", inbox.to_dict()["entries"][0]["kind"])
            self.assertEqual("agent.run.started", inbox.to_dict()["entries"][0]["event_kind"])
            self.assertEqual(watch.watch_id, watches[0].watch_id)
            self.assertFalse(watches[0].bridge)

    def test_cleanup_stale_watches_removes_bridge_and_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event_bus = EventBus()
            fake_agent_runtime = FakeAgentRuntime()
            fake_agent_runtime.register_run("run-1")
            manager = TerminalManager(
                Path(temp_dir),
                UsageMetrics(),
                agent_runtime=fake_agent_runtime,
                event_bus=event_bus,
            )
            terminal = manager.create_terminal(
                "bridge",
                execution_session_id="sess-left",
                transport="runner",
                runner_mode="projected",
            )
            event_bus.publish("agent.run.started", source_type="agent-run", source_id="run-1")
            manager.attach_to_agent_run(terminal.terminal_id, "run-1")
            watch_id = manager.bridge_watches[terminal.terminal_id]
            watch = manager.event_watches[terminal.terminal_id][0]
            watch.updated_at = "2020-01-01T00:00:00Z"

            removed = manager.cleanup_stale_watches(60, now="2026-03-07T00:05:00Z")

            self.assertEqual(1, removed)
            self.assertNotIn(terminal.terminal_id, manager.bridge_watches)
            self.assertIsNone(terminal.bound_agent_run_id)
            self.assertNotIn(terminal.terminal_id, manager.event_watches)
            self.assertIsNone(event_bus.get_cursor(f"terminal:{terminal.terminal_id}:watch:{watch_id}"))

    def test_create_terminal_rejects_when_terminal_limit_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TerminalManager(Path(temp_dir), UsageMetrics())

            for index in range(MAX_TERMINALS):
                manager.create_terminal(f"term-{index}", transport="host")

            with self.assertRaisesRegex(ValueError, "Too many terminals"):
                manager.create_terminal("overflow", transport="host")

    def test_load_state_trims_terminals_to_limit_and_drops_orphaned_watches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "runtime" / "terminals" / "state.json"
            first = TerminalManager(Path(temp_dir), UsageMetrics(), state_path=state_path)
            terminals = {}
            created_ids: list[str] = []
            for index in range(MAX_TERMINALS + 2):
                terminal = first.create_terminal(f"term-{index}", transport="host") if index < MAX_TERMINALS else TerminalSession(
                    terminal_id=f"term-extra-{index}",
                    name=f"term-{index}",
                    created_at=f"2026-03-13T00:{index:02d}:00Z",
                    transport="host",
                    runner_mode=None,
                    status="active",
                    stale_reason=None,
                    execution_session_id=None,
                    bound_agent_run_id=None,
                    command_history=[],
                    inbox=[],
                )
                terminals[terminal.terminal_id] = terminal
                created_ids.append(terminal.terminal_id)
            keep_ids = set(created_ids[2:])
            drop_id = created_ids[0]
            first.state_store.save(
                terminals,
                {drop_id: []},
                {drop_id: "watch-1"},
            )

            reloaded = TerminalManager(Path(temp_dir), UsageMetrics(), state_path=state_path)

            self.assertEqual(MAX_TERMINALS, len(reloaded.terminals))
            self.assertEqual(keep_ids, set(reloaded.terminals))
            self.assertNotIn(drop_id, reloaded.event_watches)
            self.assertNotIn(drop_id, reloaded.bridge_watches)

    def test_load_state_trims_watches_to_limit_and_clears_missing_bridge_watch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "runtime" / "terminals" / "state.json"
            manager = TerminalManager(Path(temp_dir), UsageMetrics(), state_path=state_path)
            terminal = manager.create_terminal("watcher", transport="host")
            terminal.bind_agent_run("run-1")
            watches = [
                TerminalEventWatch(
                    watch_id=f"sub-{index:06d}",
                    consumer_id=f"terminal:{terminal.terminal_id}:watch:{index}",
                    kind_prefix=None,
                    source_type=None,
                    source_id=None,
                    created_at=f"2026-03-13T00:{index:02d}:00Z",
                    updated_at=f"2026-03-13T00:{index:02d}:00Z",
                )
                for index in range(MAX_TERMINAL_EVENT_WATCHES + 2)
            ]
            dropped_bridge_id = watches[0].watch_id
            manager.state_store.save(
                {terminal.terminal_id: terminal},
                {terminal.terminal_id: watches},
                {terminal.terminal_id: dropped_bridge_id},
            )

            reloaded = TerminalManager(Path(temp_dir), UsageMetrics(), state_path=state_path)

            self.assertEqual(MAX_TERMINAL_EVENT_WATCHES, len(reloaded.event_watches[terminal.terminal_id]))
            self.assertNotIn(terminal.terminal_id, reloaded.bridge_watches)
            self.assertIsNone(reloaded.terminals[terminal.terminal_id].bound_agent_run_id)

    def test_load_state_clears_orphaned_bridge_watches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "runtime" / "terminals" / "state.json"
            manager = TerminalManager(Path(temp_dir), UsageMetrics(), state_path=state_path)
            terminal = manager.create_terminal("watcher", transport="host")
            manager.state_store.save(
                {terminal.terminal_id: terminal},
                {},
                {
                    terminal.terminal_id: "sub-valid",
                    "term-orphan": "sub-orphan",
                },
            )

            reloaded = TerminalManager(Path(temp_dir), UsageMetrics(), state_path=state_path)

            self.assertEqual({"sub-valid"}, set(reloaded.bridge_watches.values()))
            self.assertNotIn("term-orphan", reloaded.bridge_watches)

    def test_load_state_clears_orphaned_event_watches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "runtime" / "terminals" / "state.json"
            manager = TerminalManager(Path(temp_dir), UsageMetrics(), state_path=state_path)
            terminal = manager.create_terminal("watcher", transport="host")
            manager.state_store.save(
                {terminal.terminal_id: terminal},
                {
                    terminal.terminal_id: [],
                    "term-orphan": [
                        TerminalEventWatch(
                            watch_id="sub-orphan",
                            consumer_id="terminal:term-orphan",
                            kind_prefix=None,
                            source_type=None,
                            source_id=None,
                            created_at="2026-03-13T00:00:00Z",
                            updated_at="2026-03-13T00:00:00Z",
                        )
                    ],
                },
                {"term-orphan": "sub-orphan"},
            )

            reloaded = TerminalManager(Path(temp_dir), UsageMetrics(), state_path=state_path)

            self.assertIn(terminal.terminal_id, reloaded.event_watches)
            self.assertNotIn("term-orphan", reloaded.event_watches)
            self.assertNotIn("term-orphan", reloaded.bridge_watches)


if __name__ == "__main__":
    unittest.main()
