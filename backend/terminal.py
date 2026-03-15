from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from backend.events import EventBus
from core.models import TerminalEventWatch, TerminalSession, UsageMetrics
from backend.pty_session import PtySessionConfig, PtySessionManager, pty_available
from backend.terminal_command_executor import TerminalCommandExecutor
from backend.terminal_inbox import render_terminal_inbox_entries, terminal_message_inbox_entry
from backend.terminal_inspection_service import TerminalInspectionService, TerminalSessionInspection
from backend.terminal_profiles import TerminalProfileCatalog
from backend.terminal_snapshots import TerminalInboxSnapshot, TerminalWatchSnapshot
from backend.terminal_state_store import TerminalStateStore
from backend.terminal_watch_manager import MAX_TERMINAL_EVENT_WATCHES, TerminalWatchManager

if TYPE_CHECKING:
    from backend.agent_runtime import AgentRuntimeManager
    from core.filtered_fs_backend import FilteredFSBackend
    from backend.runner import RunnerManager

logger = logging.getLogger(__name__)

MAX_TERMINALS = 100


def _default_shell() -> str:
    """Return the default shell for the current platform."""
    if os.name == "nt":
        return os.environ.get("ComSpec", "cmd.exe")
    return os.environ.get("SHELL", "/bin/bash")


class TerminalManager:
    def __init__(
        self,
        project_root: Path,
        metrics: UsageMetrics,
        runner_manager: Optional["RunnerManager"] = None,
        agent_runtime: Optional["AgentRuntimeManager"] = None,
        event_bus: EventBus | None = None,
        state_path: Path | None = None,
        filtered_fs_backend: "FilteredFSBackend" | None = None,
        profile_catalog: TerminalProfileCatalog | None = None,
    ) -> None:
        self.project_root = project_root
        self.metrics = metrics
        self.runner_manager = runner_manager
        self.agent_runtime = agent_runtime
        self.event_bus = event_bus
        self.filtered_fs_backend = filtered_fs_backend
        self.profile_catalog = profile_catalog
        self._lock = threading.RLock()
        self._set_terminal_state({})
        self.state_store = TerminalStateStore(state_path)
        self.inspection_service = TerminalInspectionService(self.agent_runtime)
        self.command_executor = TerminalCommandExecutor(
            self.project_root,
            self.metrics,
            self.runner_manager,
            persist_state=self._persist_state,
            publish_event=self._publish_event,
        )
        self.watch_manager = TerminalWatchManager(
            self.event_bus,
            self.agent_runtime,
            now=self._now,
            persist_state=self._persist_state,
            publish_event=self._publish_event,
        )
        self.pty_manager = PtySessionManager()
        self._load_state()

    @property
    def event_watches(self) -> Dict[str, list[TerminalEventWatch]]:
        return self.watch_manager.event_watches

    @property
    def bridge_watches(self) -> Dict[str, str]:
        return self.watch_manager.bridge_watches

    def create_terminal(
        self,
        name: Optional[str] = None,
        execution_session_id: Optional[str] = None,
        transport: str = "runner",
        runner_mode: Optional[str] = "projected",
        io_mode: str = "command",
        profile_id: str | None = None,
    ) -> TerminalSession:
        if len(self.terminals) >= MAX_TERMINALS:
            raise ValueError(f"Too many terminals: {len(self.terminals)} >= {MAX_TERMINALS}")
        profile = self.profile_catalog.get(profile_id) if profile_id and self.profile_catalog is not None else None
        if profile is not None:
            transport = profile.transport
            runner_mode = profile.runner_mode
            io_mode = profile.io_mode
        if transport not in {"runner", "host"}:
            raise ValueError("Terminal transport must be 'runner' or 'host'")
        if transport == "runner" and runner_mode not in {"projected", "strict"}:
            raise ValueError("Runner terminal mode must be 'projected' or 'strict'")
        if io_mode not in {"command", "pty"}:
            raise ValueError("Terminal io_mode must be 'command' or 'pty'")
        if transport == "host":
            runner_mode = None
            execution_session_id = None

        # Fallback: if PTY requested but not available, degrade to command mode
        if io_mode == "pty" and not pty_available():
            logger.warning("terminal.pty_unavailable fallback=command")
            io_mode = "command"

        terminal_id = f"term-{uuid.uuid4().hex[:6]}"
        session = TerminalSession(
            terminal_id=terminal_id,
            name=name or (profile.label if profile is not None else terminal_id),
            created_at=dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            transport=transport,
            runner_mode=runner_mode,
            profile_id=profile.profile_id if profile is not None else None,
            profile_label=profile.label if profile is not None else None,
            status="active",
            stale_reason=None,
            execution_session_id=execution_session_id,
            bound_agent_run_id=None,
            command_history=[],
            inbox=[],
            io_mode=io_mode,
            spawn_argv=list(profile.spawn_argv) if profile is not None else [],
            command_argv_prefix=list(profile.command_argv_prefix) if profile is not None else [],
            env_overrides=dict(profile.env) if profile is not None else {},
            cwd_mode=profile.cwd_mode if profile is not None else ("runner_mount" if transport == "runner" else "project"),
        )
        self._set_terminal(session)

        # Spawn PTY if io_mode is "pty"
        if io_mode == "pty":
            self._spawn_pty(session)

        self._persist_state()
        logger.info(
            "terminal.created terminal_id=%s transport=%s runner_mode=%s io_mode=%s execution_session_id=%s profile_id=%s",
            session.terminal_id,
            session.transport,
            session.runner_mode,
            session.io_mode,
            session.execution_session_id,
            session.profile_id,
        )
        return session

    def _spawn_pty(self, session: TerminalSession) -> None:
        """Spawn a PTY process for the given terminal session."""
        argv = list(session.spawn_argv) or [_default_shell()]
        env = dict(session.env_overrides)
        if os.name == "nt":
            env.setdefault("PYTHONUTF8", "1")
        cwd = self._terminal_cwd(session)
        config = PtySessionConfig(
            terminal_id=session.terminal_id,
            argv=argv,
            cols=80,
            rows=24,
            cwd=cwd,
            env=env,
        )
        try:
            self.pty_manager.create(config)
        except RuntimeError as exc:
            logger.error("terminal.pty_spawn_failed terminal_id=%s error=%s", session.terminal_id, exc)
            session.io_mode = "command"

    def _terminal_cwd(self, session: TerminalSession) -> Path:
        if session.cwd_mode != "runner_mount":
            return self.project_root
        if session.execution_session_id is None:
            return self.project_root
        if self.filtered_fs_backend is None:
            return self.project_root
        mount = self.filtered_fs_backend.mount_root
        if mount is not None:
            return mount
        result = self.filtered_fs_backend.mount(session.execution_session_id)
        return result.mount_root

    def write_to_pty(self, terminal_id: str, data: str) -> None:
        """Write data to a PTY terminal."""
        session = self._get_terminal(terminal_id)
        if session.io_mode != "pty":
            raise ValueError(f"Terminal {terminal_id} is not in PTY mode")
        if session.status != "active":
            raise ValueError(f"Terminal {terminal_id} is stale")
        self.pty_manager.write(terminal_id, data)

    def resize_pty(self, terminal_id: str, cols: int, rows: int) -> None:
        """Resize a PTY terminal."""
        session = self._get_terminal(terminal_id)
        if session.io_mode != "pty":
            raise ValueError(f"Terminal {terminal_id} is not in PTY mode")
        self.pty_manager.resize(terminal_id, cols, rows)

    def get_pty_output(self, terminal_id: str) -> bytes:
        """Drain pending PTY output."""
        session = self._get_terminal(terminal_id)
        if session.io_mode != "pty":
            raise ValueError(f"Terminal {terminal_id} is not in PTY mode")
        return self.pty_manager.get_output(terminal_id)

    def destroy_terminal(self, terminal_id: str) -> None:
        """Destroy a terminal and its PTY if applicable."""
        with self._lock:
            session = self.terminals.get(terminal_id)
        if session is None:
            return
        if session.io_mode == "pty" and self.pty_manager.has_session(terminal_id):
            self.pty_manager.destroy(terminal_id)
        session.mark_stale("terminal destroyed")
        self._persist_state()
        logger.info("terminal.destroyed terminal_id=%s", terminal_id)

    def list_terminals(self) -> List[TerminalSession]:
        with self._lock:
            return list(self.terminals.values())

    def inspect_terminal(self, terminal_id: str) -> TerminalSessionInspection:
        return self.inspection_service.inspect(self._get_terminal(terminal_id))

    def list_terminal_inspections(self) -> list[TerminalSessionInspection]:
        return self.inspection_service.inspect_many(self.list_terminals())

    def read_inbox(self, terminal_id: str) -> list[str]:
        terminal = self._get_terminal(terminal_id)
        self.watch_manager.sync_terminal_inbox(terminal)
        return render_terminal_inbox_entries(terminal.snapshot_inbox())

    def read_inbox_snapshot(self, terminal_id: str) -> TerminalInboxSnapshot:
        terminal = self._get_terminal(terminal_id)
        self.watch_manager.sync_terminal_inbox(terminal)
        return TerminalInboxSnapshot(
            terminal_id=terminal.terminal_id,
            bound_agent_run_id=terminal.bound_agent_run_id,
            entries=terminal.snapshot_inbox(),
            watch_ids=[watch.watch_id for watch in self.event_watches.get(terminal.terminal_id, [])],
        )

    def attach_to_agent_run(self, terminal_id: str, run_id: str) -> TerminalSession:
        terminal = self._get_terminal(terminal_id)
        return self.watch_manager.attach_to_agent_run(terminal, run_id)

    def send_input_to_agent(self, terminal_id: str, text: str) -> str:
        terminal = self._get_terminal(terminal_id)
        return self.watch_manager.send_input_to_agent(terminal, text)

    def subscribe_to_events(
        self,
        terminal_id: str,
        *,
        kind_prefix: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> str:
        terminal = self._get_terminal(terminal_id)
        return self.watch_manager.subscribe_to_events(
            terminal,
            kind_prefix=kind_prefix,
            source_type=source_type,
            source_id=source_id,
        )

    def watch_events(
        self,
        terminal_id: str,
        *,
        kind_prefix: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> TerminalWatchSnapshot:
        watch_id = self.subscribe_to_events(
            terminal_id,
            kind_prefix=kind_prefix,
            source_type=source_type,
            source_id=source_id,
        )
        watches = self.list_watch_snapshots(terminal_id)
        return next(watch for watch in watches if watch.watch_id == watch_id)

    def list_watch_snapshots(self, terminal_id: str) -> list[TerminalWatchSnapshot]:
        terminal = self._get_terminal(terminal_id)
        bridge_watch_id = self.bridge_watches.get(terminal.terminal_id)
        return [
            TerminalWatchSnapshot(
                terminal_id=terminal.terminal_id,
                watch_id=watch.watch_id,
                consumer_id=watch.consumer_id,
                kind_prefix=watch.kind_prefix,
                source_type=watch.source_type,
                source_id=watch.source_id,
                created_at=watch.created_at,
                updated_at=watch.updated_at,
                bridge=watch.watch_id == bridge_watch_id,
            )
            for watch in self.event_watches.get(terminal.terminal_id, [])
        ]

    def run_command(self, terminal_id: str, command: str) -> str:
        session = self._get_terminal(terminal_id)
        return self.command_executor.execute(session, command)

    def mark_execution_session_stale(self, execution_session_id: str, reason: str) -> None:
        with self._lock:
            for session in self.terminals.values():
                if session.transport != "runner":
                    continue
                if session.execution_session_id != execution_session_id:
                    continue
                if session.status != "active":
                    continue
                session.mark_stale(reason)
                logger.info(
                    "terminal.mark_stale terminal_id=%s execution_session_id=%s reason=%s",
                    session.terminal_id,
                    execution_session_id,
                    reason,
                )
            self._persist_state()

    def mark_noncurrent_runner_terminals_stale(self, current_execution_session_id: str, reason_prefix: str) -> None:
        with self._lock:
            changed = False
            for session in self.terminals.values():
                if session.transport != "runner":
                    continue
                if session.execution_session_id == current_execution_session_id:
                    continue
                if session.status != "active":
                    continue
                session.mark_stale(f"{reason_prefix} {session.execution_session_id}")
                logger.info(
                    "terminal.mark_stale terminal_id=%s execution_session_id=%s reason=%s",
                    session.terminal_id,
                    session.execution_session_id,
                    f"{reason_prefix} {session.execution_session_id}",
                )
                changed = True
            if changed:
                self._persist_state()

    def cleanup_stale_watches(self, max_age_seconds: int, *, now: str | None = None) -> int:
        return self.watch_manager.cleanup_stale_watches(self.terminals, max_age_seconds, now=now)

    def send_message(self, src_terminal_id: str, dst_terminal_id: str, message: str) -> None:
        source = self._get_terminal(src_terminal_id)
        destination = self._get_terminal(dst_terminal_id)
        destination.append_inbox(
            terminal_message_inbox_entry(
                src_terminal_id,
                message,
                created_at=self._now(),
            )
        )
        self._publish_event(
            "terminal.message.sent",
            source,
            {"to_terminal_id": dst_terminal_id, "message": message},
        )
        self._persist_state()
        logger.info(
            "terminal.message_sent from_terminal_id=%s to_terminal_id=%s",
            src_terminal_id,
            dst_terminal_id,
        )

    def _get_terminal(self, terminal_id: str) -> TerminalSession:
        with self._lock:
            if terminal_id not in self.terminals:
                raise KeyError(f"Unknown terminal: {terminal_id}")
            return self.terminals[terminal_id]

    def _publish_event(self, kind: str, session: TerminalSession, payload: dict[str, object]) -> None:
        if self.event_bus is None:
            return
        self.event_bus.publish(
            kind,
            source_type="terminal",
            source_id=session.terminal_id,
            execution_session_id=session.execution_session_id,
            payload=payload,
        )

    def _load_state(self) -> None:
        snapshot = self.state_store.load()
        terminals = dict(snapshot.terminals)
        event_watches = dict(snapshot.event_watches)
        bridge_watches = dict(snapshot.bridge_watches)
        changed = self._trim_loaded_state(terminals, event_watches, bridge_watches)
        # Mark PTY terminals as stale on restart (PTY handles don't survive restart)
        for terminal in terminals.values():
            if terminal.io_mode == "pty" and terminal.status == "active":
                terminal.mark_stale("app restarted — PTY session lost")
                changed = True
                logger.info(
                    "terminal.pty_stale_on_restart terminal_id=%s",
                    terminal.terminal_id,
                )
        self._set_terminal_state(terminals)
        self.watch_manager.replace_state(event_watches, bridge_watches)
        if changed:
            self._persist_state()

    def _persist_state(self) -> None:
        self.state_store.save(self.terminals, self.event_watches, self.bridge_watches)

    def _set_terminal(self, terminal: TerminalSession) -> None:
        with self._lock:
            self.terminals[terminal.terminal_id] = terminal

    def _set_terminal_state(self, terminals: dict[str, TerminalSession]) -> None:
        with self._lock:
            self.terminals = terminals

    @staticmethod
    def _drop_terminal(terminals: dict[str, TerminalSession], terminal_id: str) -> None:
        terminals.pop(terminal_id, None)

    @staticmethod
    def _set_loaded_event_watches(
        event_watches: dict[str, list[TerminalEventWatch]],
        terminal_id: str,
        watches: list[TerminalEventWatch],
    ) -> None:
        event_watches[terminal_id] = watches

    @staticmethod
    def _drop_loaded_terminal_watch_state(
        event_watches: dict[str, list[TerminalEventWatch]],
        bridge_watches: dict[str, str],
        terminal_id: str,
    ) -> None:
        event_watches.pop(terminal_id, None)
        TerminalManager._drop_loaded_bridge_watch(bridge_watches, terminal_id)

    def _trim_loaded_state(
        self,
        terminals: dict[str, TerminalSession],
        event_watches: dict[str, list[TerminalEventWatch]],
        bridge_watches: dict[str, str],
    ) -> bool:
        changed = False
        if len(terminals) > MAX_TERMINALS:
            ordered = list(terminals.values())
            keep_ids = {terminal.terminal_id for terminal in ordered[-MAX_TERMINALS:]}
            terminals_to_drop = [terminal_id for terminal_id in terminals if terminal_id not in keep_ids]
            removed = len(terminals_to_drop)
            for terminal_id in terminals_to_drop:
                self._drop_terminal(terminals, terminal_id)
                self._drop_loaded_terminal_watch_state(event_watches, bridge_watches, terminal_id)
            changed = bool(terminals_to_drop)
            if removed:
                logger.info("terminal.load.trim removed=%s limit=%s", removed, MAX_TERMINALS)
        for terminal_id, watches in list(event_watches.items()):
            if terminal_id not in terminals:
                self._drop_loaded_terminal_watch_state(event_watches, bridge_watches, terminal_id)
                changed = True
                continue
            if len(watches) <= MAX_TERMINAL_EVENT_WATCHES:
                continue
            ordered_watches = sorted(
                watches,
                key=lambda watch: watch.updated_at or watch.created_at or "",
            )
            kept_watches = ordered_watches[-MAX_TERMINAL_EVENT_WATCHES:]
            kept_watch_ids = {watch.watch_id for watch in kept_watches}
            self._set_loaded_event_watches(event_watches, terminal_id, kept_watches)
            if bridge_watches.get(terminal_id) not in kept_watch_ids:
                self._drop_loaded_bridge_watch(bridge_watches, terminal_id)
                terminal = terminals.get(terminal_id)
                if terminal is not None:
                    terminal.unbind_agent_run()
            changed = True
            logger.info(
                "terminal.load.watch_trim terminal_id=%s removed=%s limit=%s",
                terminal_id,
                len(watches) - len(kept_watches),
                MAX_TERMINAL_EVENT_WATCHES,
            )
        orphaned_bridge_terminal_ids = [
            terminal_id for terminal_id in bridge_watches if terminal_id not in terminals
        ]
        for terminal_id in orphaned_bridge_terminal_ids:
            self._drop_loaded_terminal_watch_state(event_watches, bridge_watches, terminal_id)
        if orphaned_bridge_terminal_ids:
            changed = True
            logger.info("terminal.load.orphaned_bridges removed=%s", len(orphaned_bridge_terminal_ids))
        return changed

    @staticmethod
    def _drop_loaded_bridge_watch(bridge_watches: dict[str, str], terminal_id: str) -> None:
        bridge_watches.pop(terminal_id, None)

    @staticmethod
    def _now() -> str:
        return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
