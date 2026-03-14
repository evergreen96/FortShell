from __future__ import annotations

import datetime as dt
import logging

from ai_ide.events import EventBus
from ai_ide.models import TerminalEventWatch, TerminalSession
from ai_ide.terminal_inbox import runtime_event_inbox_entry

logger = logging.getLogger(__name__)

MAX_TERMINAL_EVENT_WATCHES = 200


class TerminalWatchManager:
    def __init__(
        self,
        event_bus: EventBus | None,
        agent_runtime,
        *,
        now,
        persist_state,
        publish_event,
    ) -> None:
        self.event_bus = event_bus
        self.agent_runtime = agent_runtime
        self._now = now
        self._persist_state = persist_state
        self._publish_event = publish_event
        self._set_terminal_watch_state({})
        self._set_bridge_watch_state({})

    def replace_state(
        self,
        event_watches: dict[str, list[TerminalEventWatch]],
        bridge_watches: dict[str, str],
    ) -> None:
        trimmed = {
            terminal_id: self._trim_terminal_watches(list(watches))
            for terminal_id, watches in event_watches.items()
        }
        removed = sum(len(watches) - len(trimmed[terminal_id]) for terminal_id, watches in event_watches.items())
        self._set_terminal_watch_state(trimmed)
        self._set_bridge_watch_state(dict(bridge_watches))
        if removed:
            logger.info("terminal_watch.replace.trim removed=%s", removed)

    def attach_to_agent_run(self, terminal: TerminalSession, run_id: str) -> TerminalSession:
        if terminal.status != "active":
            raise ValueError(f"Terminal is not active: {terminal.terminal_id}")
        if self.agent_runtime is None:
            raise RuntimeError("Terminal-to-agent bridging requires an agent runtime")
        run = self.agent_runtime.get_run(run_id)
        if terminal.execution_session_id is not None and terminal.execution_session_id != run.execution_session_id:
            raise ValueError(
                f"Terminal execution session {terminal.execution_session_id} does not match agent run {run.execution_session_id}"
            )
        previous_watch = self._drop_bridge_watch(terminal.terminal_id)
        if previous_watch is not None:
            self._remove_watch(terminal.terminal_id, previous_watch)
        watch_id = self.subscribe_to_events(
            terminal,
            kind_prefix="agent.run",
            source_type="agent-run",
            source_id=run_id,
        )
        self._set_bridge_watch(terminal.terminal_id, watch_id)
        terminal.bind_agent_run(run_id)
        logger.info(
            "terminal_watch.attach terminal_id=%s run_id=%s watch_id=%s",
            terminal.terminal_id,
            run_id,
            watch_id,
        )
        self._publish_event(
            "terminal.agent_bridge.attached",
            terminal,
            {"run_id": run_id, "agent_kind": run.agent_kind},
        )
        self._persist_state()
        return terminal

    def send_input_to_agent(self, terminal: TerminalSession, text: str) -> str:
        if terminal.status != "active":
            raise ValueError(f"Terminal is not active: {terminal.terminal_id}")
        if terminal.bound_agent_run_id is None:
            raise ValueError(f"Terminal is not attached to an agent run: {terminal.terminal_id}")
        if self.agent_runtime is None:
            raise RuntimeError("Terminal-to-agent bridging requires an agent runtime")
        run = self.agent_runtime.send_input(terminal.bound_agent_run_id, text)
        self._publish_event(
            "terminal.agent_input.sent",
            terminal,
            {"run_id": run.run_id, "bytes": len(text) + 1},
        )
        self._persist_state()
        return run.run_id

    def subscribe_to_events(
        self,
        terminal: TerminalSession,
        *,
        kind_prefix: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> str:
        if self.event_bus is None:
            raise RuntimeError("Terminal event subscriptions require an event bus")
        existing = self.event_watches.get(terminal.terminal_id, [])
        if len(existing) >= MAX_TERMINAL_EVENT_WATCHES:
            raise ValueError(
                f"Terminal watch limit exceeded: {terminal.terminal_id} ({MAX_TERMINAL_EVENT_WATCHES})"
            )
        watch_index = len(existing) + 1
        watch_id = f"sub-{watch_index:06d}"
        consumer_id = f"terminal:{terminal.terminal_id}:watch:{watch_id}"
        watch = TerminalEventWatch(
            watch_id=watch_id,
            consumer_id=consumer_id,
            kind_prefix=kind_prefix,
            source_type=source_type,
            source_id=source_id,
            created_at=self._now(),
            updated_at=self._now(),
        )
        self._append_terminal_watch(terminal.terminal_id, existing, watch)
        latest = self.event_bus.list_events(limit=1)
        initial_cursor = latest[0].event_id if latest else None
        self.event_bus.set_cursor(consumer_id, initial_cursor)
        self._persist_state()
        return watch_id

    def sync_terminal_inbox(self, terminal: TerminalSession) -> None:
        if self.event_bus is None:
            return
        if self.agent_runtime is not None and hasattr(self.agent_runtime, "refresh_active_runs"):
            self.agent_runtime.refresh_active_runs(execution_session_id=terminal.execution_session_id)
        changed = False
        for watch in self.event_watches.get(terminal.terminal_id, []):
            watch.touch(self._now())
            changed = True
            events = self.event_bus.pull_events(
                watch.consumer_id,
                kind_prefix=watch.kind_prefix,
                source_type=watch.source_type,
                source_id=watch.source_id,
                advance=True,
            )
            for event in events:
                terminal.append_inbox(runtime_event_inbox_entry(event))
                changed = True
        if changed:
            logger.info(
                "terminal_watch.sync terminal_id=%s watch_count=%s inbox_size=%s",
                terminal.terminal_id,
                len(self.event_watches.get(terminal.terminal_id, [])),
                len(terminal.snapshot_inbox()),
            )
            self._persist_state()

    def cleanup_stale_watches(
        self,
        terminals: dict[str, TerminalSession],
        max_age_seconds: int,
        *,
        now: str | None = None,
    ) -> int:
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be >= 0")

        reference_time = self._parse_timestamp(now or self._now())
        removed = 0
        for terminal_id, watches in list(self.event_watches.items()):
            kept: list[TerminalEventWatch] = []
            for watch in watches:
                updated_at = watch.updated_at or watch.created_at or self._now()
                age_seconds = (reference_time - self._parse_timestamp(updated_at)).total_seconds()
                if age_seconds <= max_age_seconds:
                    kept.append(watch)
                    continue
                if self.event_bus is not None:
                    self.event_bus.set_cursor(watch.consumer_id, None)
                if self.bridge_watches.get(terminal_id) == watch.watch_id:
                    self._drop_bridge_watch(terminal_id)
                    terminal = terminals.get(terminal_id)
                    if terminal is not None:
                        terminal.unbind_agent_run()
                removed += 1
            if kept:
                self._set_terminal_watches(terminal_id, kept)
            else:
                self._drop_terminal_watches(terminal_id)
        if removed:
            logger.info("terminal_watch.cleanup removed=%s", removed)
            self._persist_state()
        return removed

    def _remove_watch(self, terminal_id: str, watch_id: str) -> None:
        if self.event_bus is None:
            return
        watches = self.event_watches.get(terminal_id, [])
        kept: list[TerminalEventWatch] = []
        for watch in watches:
            if watch.watch_id == watch_id:
                self.event_bus.set_cursor(watch.consumer_id, None)
                continue
            kept.append(watch)
        if kept:
            self._set_terminal_watches(terminal_id, kept)
        else:
            self._drop_terminal_watches(terminal_id)
        self._persist_state()

    def _set_terminal_watches(self, terminal_id: str, watches: list[TerminalEventWatch]) -> None:
        self.event_watches[terminal_id] = watches

    def _set_terminal_watch_state(self, event_watches: dict[str, list[TerminalEventWatch]]) -> None:
        self.event_watches = event_watches

    def _append_terminal_watch(
        self,
        terminal_id: str,
        watches: list[TerminalEventWatch],
        watch: TerminalEventWatch,
    ) -> None:
        self._set_terminal_watches(terminal_id, [*watches, watch])

    def _drop_terminal_watches(self, terminal_id: str) -> None:
        self.event_watches.pop(terminal_id, None)

    def _set_bridge_watch(self, terminal_id: str, watch_id: str) -> None:
        self.bridge_watches[terminal_id] = watch_id

    def _set_bridge_watch_state(self, bridge_watches: dict[str, str]) -> None:
        self.bridge_watches = bridge_watches

    def _drop_bridge_watch(self, terminal_id: str) -> str | None:
        return self.bridge_watches.pop(terminal_id, None)

    @staticmethod
    def _trim_terminal_watches(watches: list[TerminalEventWatch]) -> list[TerminalEventWatch]:
        if len(watches) <= MAX_TERMINAL_EVENT_WATCHES:
            return watches
        ordered = sorted(
            watches,
            key=lambda watch: watch.updated_at or watch.created_at or "",
        )
        return ordered[-MAX_TERMINAL_EVENT_WATCHES :]

    @staticmethod
    def _parse_timestamp(timestamp: str) -> dt.datetime:
        return dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
