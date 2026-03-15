from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from core.atomic_files import atomic_replace
from core.file_lock import advisory_lock
from core.models import (
    MAX_TERMINAL_COMMAND_HISTORY,
    MAX_TERMINAL_INBOX_ENTRIES,
    TerminalEventWatch,
    TerminalSession,
)
from backend.terminal_inbox import TerminalInboxEntry, legacy_terminal_inbox_entry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TerminalStateSnapshot:
    terminals: dict[str, TerminalSession]
    event_watches: dict[str, list[TerminalEventWatch]]
    bridge_watches: dict[str, str]


class TerminalStateStore:
    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path.resolve() if state_path is not None else None
        self.state_lock_path = self._lock_path_for(self.state_path)
        self._set_state_signature(None)
        self._set_cached_snapshot(TerminalStateSnapshot(terminals={}, event_watches={}, bridge_watches={}))

    def load(self) -> TerminalStateSnapshot:
        if self.state_path is None:
            return TerminalStateSnapshot(terminals={}, event_watches={}, bridge_watches={})
        with self._state_lock():
            if not self.state_path.exists():
                self._set_state_signature(None)
                self._set_cached_snapshot(TerminalStateSnapshot(terminals={}, event_watches={}, bridge_watches={}))
                return TerminalStateSnapshot(terminals={}, event_watches={}, bridge_watches={})
            current_signature = self._signature_for(self.state_path)
            if self._state_signature == current_signature:
                logger.debug("terminal_state_store.cache.hit path=%s", self.state_path)
                return TerminalStateSnapshot(
                    terminals=dict(self._cached_snapshot.terminals),
                    event_watches=dict(self._cached_snapshot.event_watches),
                    bridge_watches=dict(self._cached_snapshot.bridge_watches),
                )
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            terminals = {
                item["terminal_id"]: self._load_terminal(item)
                for item in payload.get("terminals", [])
            }
            event_watches = {
                terminal_id: [
                    TerminalEventWatch(
                        watch_id=watch["watch_id"],
                        consumer_id=watch["consumer_id"],
                        kind_prefix=watch.get("kind_prefix"),
                        source_type=watch.get("source_type"),
                        source_id=watch.get("source_id"),
                        created_at=watch.get("created_at"),
                        updated_at=watch.get("updated_at") or watch.get("created_at"),
                    )
                    for watch in watch_items
                ]
                for terminal_id, watch_items in payload.get("event_watches", {}).items()
            }
            bridge_watches = {
                str(terminal_id): str(watch_id)
                for terminal_id, watch_id in payload.get("bridge_watches", {}).items()
            }
            snapshot = TerminalStateSnapshot(
                terminals=terminals,
                event_watches=event_watches,
                bridge_watches=bridge_watches,
            )
            self._set_state_signature(current_signature)
            self._set_cached_snapshot(snapshot)
            logger.debug("terminal_state_store.load path=%s terminals=%s event_watch_sets=%s", self.state_path, len(terminals), len(event_watches))
            return snapshot

    def save(
        self,
        terminals: dict[str, TerminalSession],
        event_watches: dict[str, list[TerminalEventWatch]],
        bridge_watches: dict[str, str],
    ) -> None:
        if self.state_path is None:
            return
        with self._state_lock():
            self._save_unlocked(terminals, event_watches, bridge_watches)

    def _save_unlocked(
        self,
        terminals: dict[str, TerminalSession],
        event_watches: dict[str, list[TerminalEventWatch]],
        bridge_watches: dict[str, str],
    ) -> None:
        assert self.state_path is not None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "terminals": [self._serialize_terminal(terminal) for terminal in terminals.values()],
            "event_watches": {
                terminal_id: [asdict(watch) for watch in watches]
                for terminal_id, watches in event_watches.items()
            },
            "bridge_watches": dict(bridge_watches),
        }
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        atomic_replace(temp_path, self.state_path)
        self._set_state_signature(self._signature_for(self.state_path))
        self._set_cached_snapshot(
            TerminalStateSnapshot(
                terminals=dict(terminals),
                event_watches=dict(event_watches),
                bridge_watches=dict(bridge_watches),
            )
        )
        logger.debug("terminal_state_store.save path=%s terminals=%s event_watch_sets=%s", self.state_path, len(terminals), len(event_watches))

    @staticmethod
    def _load_terminal(payload: dict[str, object]) -> TerminalSession:
        raw_inbox = payload.get("inbox") or []
        command_history = [str(item) for item in payload.get("command_history") or []]
        if len(command_history) > MAX_TERMINAL_COMMAND_HISTORY:
            removed = len(command_history) - MAX_TERMINAL_COMMAND_HISTORY
            command_history = command_history[-MAX_TERMINAL_COMMAND_HISTORY:]
            logger.info("terminal_state_store.command_history_trimmed removed=%s", removed)
        inbox = TerminalStateStore._load_inbox(raw_inbox)
        if len(inbox) > MAX_TERMINAL_INBOX_ENTRIES:
            removed = len(inbox) - MAX_TERMINAL_INBOX_ENTRIES
            inbox = inbox[-MAX_TERMINAL_INBOX_ENTRIES:]
            logger.info("terminal_state_store.inbox_trimmed removed=%s", removed)
        io_mode = str(payload.get("io_mode", "command"))
        return TerminalSession(
            terminal_id=str(payload["terminal_id"]),
            name=str(payload["name"]),
            created_at=str(payload["created_at"]),
            transport=str(payload["transport"]),
            runner_mode=payload.get("runner_mode"),
            profile_id=payload.get("profile_id"),
            profile_label=payload.get("profile_label"),
            status=str(payload["status"]),
            stale_reason=payload.get("stale_reason"),
            execution_session_id=payload.get("execution_session_id"),
            bound_agent_run_id=payload.get("bound_agent_run_id"),
            command_history=command_history,
            inbox=inbox,
            io_mode=io_mode,
            spawn_argv=[str(item) for item in payload.get("spawn_argv", [])],
            command_argv_prefix=[str(item) for item in payload.get("command_argv_prefix", [])],
            env_overrides={
                str(key): str(value)
                for key, value in (payload.get("env_overrides") or {}).items()
            } if isinstance(payload.get("env_overrides"), dict) else {},
            cwd_mode=str(payload.get("cwd_mode", "project")),
        )

    @staticmethod
    def _load_inbox(raw_items: object) -> list[TerminalInboxEntry]:
        if not isinstance(raw_items, list):
            return []
        entries: list[TerminalInboxEntry] = []
        for item in raw_items:
            if isinstance(item, str):
                entries.append(legacy_terminal_inbox_entry(item))
                continue
            if isinstance(item, dict):
                entries.append(TerminalInboxEntry.from_dict(item))
        return entries

    @staticmethod
    def _serialize_terminal(terminal: TerminalSession) -> dict[str, object]:
        payload: dict[str, object] = {}
        for terminal_field in fields(TerminalSession):
            if terminal_field.name in {"_state_lock", "_command_history_lock", "_inbox_lock"}:
                continue
            if terminal_field.name == "command_history":
                payload["command_history"] = terminal.snapshot_command_history()
                continue
            value = getattr(terminal, terminal_field.name)
            if terminal_field.name == "inbox":
                payload["inbox"] = [entry.to_dict() for entry in terminal.snapshot_inbox()]
                continue
            payload[terminal_field.name] = value
        return payload

    def state_lock(self):
        if self.state_lock_path is None:
            return _NullContext()
        return advisory_lock(self.state_lock_path)

    @staticmethod
    def _lock_path_for(path: Path | None) -> Path | None:
        if path is None:
            return None
        return path.with_name(path.name + ".lock")

    @staticmethod
    def _signature_for(path: Path) -> tuple[int, int]:
        stat_result = path.stat()
        return stat_result.st_mtime_ns, stat_result.st_size

    def _state_lock(self):
        return self.state_lock()

    def _set_cached_snapshot(self, snapshot: TerminalStateSnapshot) -> None:
        self._cached_snapshot = snapshot

    def _set_state_signature(self, signature: tuple[int, int] | None) -> None:
        self._state_signature = signature


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False
