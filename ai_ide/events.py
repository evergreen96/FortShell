from __future__ import annotations

import datetime as dt
import json
import logging
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ai_ide.atomic_files import atomic_replace
from ai_ide.file_lock import advisory_lock


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    timestamp: str
    kind: str
    source_type: str
    source_id: str
    execution_session_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "kind": self.kind,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "execution_session_id": self.execution_session_id,
            "payload": dict(self.payload),
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> RuntimeEvent:
        return RuntimeEvent(
            event_id=payload["event_id"],
            timestamp=payload["timestamp"],
            kind=payload["kind"],
            source_type=payload["source_type"],
            source_id=payload["source_id"],
            execution_session_id=payload.get("execution_session_id"),
            payload=dict(payload.get("payload") or {}),
        )


@dataclass
class EventSubscription:
    subscription_id: str
    callback: Callable[[RuntimeEvent], None]
    kind_prefix: str | None = None
    source_type: str | None = None
    source_id: str | None = None

    def matches(self, event: RuntimeEvent) -> bool:
        if self.kind_prefix is not None and not event.kind.startswith(self.kind_prefix):
            return False
        if self.source_type is not None and event.source_type != self.source_type:
            return False
        if self.source_id is not None and event.source_id != self.source_id:
            return False
        return True


class EventBus:
    DEFAULT_MAX_EVENTS = 1000
    AUTO_COMPACT_RETAIN = 500
    MAX_SUBSCRIPTIONS = 200
    MAX_CURSORS = 500

    def __init__(
        self,
        log_path: Path | None = None,
        cursor_path: Path | None = None,
        *,
        max_events: int | None = None,
    ) -> None:
        self._set_events_state([])
        self._set_subscriptions_state({})
        self._set_counter(0)
        self._set_cursor_state({}, {})
        self._max_events = max_events if max_events is not None else self.DEFAULT_MAX_EVENTS
        self._set_log_signature(None)
        self._set_cursor_signature(None)
        self._log_path = log_path.resolve() if log_path is not None else None
        self._cursor_path = cursor_path.resolve() if cursor_path is not None else None
        self._log_lock_path = self._lock_path_for(self._log_path)
        self._cursor_lock_path = self._lock_path_for(self._cursor_path)
        self._load_from_disk()
        self._load_cursors()
        self._maybe_auto_compact()

    def publish(
        self,
        kind: str,
        *,
        source_type: str,
        source_id: str,
        execution_session_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        timestamp = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        event_payload = payload or {}
        if self._log_lock_path is None:
            event = self._create_event(
                kind,
                source_type=source_type,
                source_id=source_id,
                execution_session_id=execution_session_id,
                payload=event_payload,
                timestamp=timestamp,
            )
            self._append_event_local(event)
        else:
            with advisory_lock(self._log_lock_path):
                self._load_from_disk_unlocked()
                event = self._create_event(
                    kind,
                    source_type=source_type,
                    source_id=source_id,
                    execution_session_id=execution_session_id,
                    payload=event_payload,
                    timestamp=timestamp,
                )
                self._append_event_local(event)
        for subscription in list(self._subscriptions.values()):
            if not subscription.matches(event):
                continue
            subscription.callback(event)
        self._maybe_auto_compact()
        return event

    def list_events(
        self,
        *,
        limit: int | None = None,
        after_event_id: str | None = None,
        kind: str | None = None,
        kind_prefix: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> list[RuntimeEvent]:
        self._load_from_disk()
        events = self._events
        if after_event_id is not None:
            seen = False
            filtered: list[RuntimeEvent] = []
            for event in events:
                if seen:
                    filtered.append(event)
                elif event.event_id == after_event_id:
                    seen = True
            events = filtered
        if kind is not None:
            events = [event for event in events if event.kind == kind]
        if kind_prefix is not None:
            events = [event for event in events if event.kind.startswith(kind_prefix)]
        if source_type is not None:
            events = [event for event in events if event.source_type == source_type]
        if source_id is not None:
            events = [event for event in events if event.source_id == source_id]
        if limit is None:
            return list(events)
        return list(events[-limit:])

    def subscribe(
        self,
        callback: Callable[[RuntimeEvent], None],
        *,
        kind_prefix: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> str:
        if len(self._subscriptions) >= self.MAX_SUBSCRIPTIONS:
            raise ValueError(f"too many subscriptions: {len(self._subscriptions)} >= {self.MAX_SUBSCRIPTIONS}")
        subscription_id = f"sub-{len(self._subscriptions) + 1:06d}"
        self._set_subscription(
            EventSubscription(
                subscription_id=subscription_id,
                callback=callback,
                kind_prefix=kind_prefix,
                source_type=source_type,
                source_id=source_id,
            )
        )
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        self._drop_subscription(subscription_id)

    def size(self) -> int:
        self._load_from_disk()
        return len(self._events)

    def get_cursor(self, consumer_id: str) -> str | None:
        self._load_from_disk()
        self._load_cursors()
        return self._cursors.get(consumer_id)

    def get_cursor_updated_at(self, consumer_id: str) -> str | None:
        self._load_cursors()
        return self._cursor_updated_at.get(consumer_id)

    def set_cursor(self, consumer_id: str, event_id: str | None, *, updated_at: str | None = None) -> None:
        self._load_from_disk()
        if event_id is not None and not any(event.event_id == event_id for event in self._events):
            raise ValueError(f"unknown event id: {event_id}")
        if self._cursor_lock_path is None:
            self._set_cursor_local(consumer_id, event_id, updated_at=updated_at)
            return
        with advisory_lock(self._cursor_lock_path):
            self._load_cursors_unlocked()
            self._set_cursor_local(consumer_id, event_id, updated_at=updated_at)
            self._persist_cursors_unlocked()

    def pull_events(
        self,
        consumer_id: str,
        *,
        limit: int | None = None,
        kind: str | None = None,
        kind_prefix: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        advance: bool = True,
    ) -> list[RuntimeEvent]:
        cursor = self.get_cursor(consumer_id)
        events = self.list_events(after_event_id=cursor)
        matched: list[RuntimeEvent] = []
        last_seen_id: str | None = None
        for event in events:
            last_seen_id = event.event_id
            if kind is not None and event.kind != kind:
                continue
            if kind_prefix is not None and not event.kind.startswith(kind_prefix):
                continue
            if source_type is not None and event.source_type != source_type:
                continue
            if source_id is not None and event.source_id != source_id:
                continue
            matched.append(event)
            if limit is not None and len(matched) >= limit:
                if advance:
                    self.set_cursor(consumer_id, event.event_id)
                return matched
        if advance:
            if matched:
                self.set_cursor(consumer_id, matched[-1].event_id)
            elif last_seen_id is not None:
                self.set_cursor(consumer_id, last_seen_id)
        return matched

    def compact(self, retain_last: int) -> tuple[int, int]:
        if retain_last < 1:
            raise ValueError("retain_last must be >= 1")

        log_lock = advisory_lock(self._log_lock_path) if self._log_lock_path is not None else nullcontext()
        cursor_lock = (
            advisory_lock(self._cursor_lock_path) if self._cursor_lock_path is not None else nullcontext()
        )
        with log_lock:
            self._load_from_disk_unlocked()
            with cursor_lock:
                self._load_cursors_unlocked()
                pinned_event_ids = set(self._cursors.values())
                keep_event_ids = pinned_event_ids | {
                    event.event_id for event in self._events[-retain_last:]
                }
                compacted_events = [
                    event for event in self._events if event.event_id in keep_event_ids
                ]
                removed_count = len(self._events) - len(compacted_events)
                if removed_count == 0:
                    return len(compacted_events), 0
                self._rewrite_log_unlocked(compacted_events)
                if self._cursor_path is not None:
                    self._persist_cursors_unlocked()
                logger.info(
                    "event.compact retained=%s removed=%s path=%s",
                    len(compacted_events),
                    removed_count,
                    self._log_path,
                )
                return len(compacted_events), removed_count

    def cleanup_stale_cursors(self, max_age_seconds: int, *, now: str | None = None) -> int:
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be >= 0")

        reference_time = self._parse_timestamp(now or self._utc_now())
        if self._cursor_lock_path is None:
            self._load_cursors_unlocked()
            removed = self._cleanup_stale_cursors_unlocked(reference_time, max_age_seconds)
            if removed:
                self._persist_cursors_unlocked()
                logger.info("event.cursor_cleanup removed=%s path=%s", removed, self._cursor_path)
            return removed

        with advisory_lock(self._cursor_lock_path):
            self._load_cursors_unlocked()
            removed = self._cleanup_stale_cursors_unlocked(reference_time, max_age_seconds)
            if removed:
                self._persist_cursors_unlocked()
                logger.info("event.cursor_cleanup removed=%s path=%s", removed, self._cursor_path)
            return removed

    def _maybe_auto_compact(self) -> None:
        if self._max_events <= 0:
            return
        if len(self._events) <= self._max_events:
            return
        retain = min(self.AUTO_COMPACT_RETAIN, self._max_events)
        self.compact(max(retain, 1))

    def _load_from_disk(self) -> None:
        if self._log_lock_path is None:
            self._load_from_disk_unlocked()
            return
        with advisory_lock(self._log_lock_path):
            self._load_from_disk_unlocked()

    def _load_cursors(self) -> None:
        if self._cursor_lock_path is None:
            self._load_cursors_unlocked()
            return
        with advisory_lock(self._cursor_lock_path):
            self._load_cursors_unlocked()

    def _append_to_disk(self, event: RuntimeEvent) -> None:
        if self._log_path is None:
            return

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=True) + "\n")
        stat_result = self._log_path.stat()
        self._set_log_signature((stat_result.st_mtime_ns, stat_result.st_size))

    def _append_event_local(self, event: RuntimeEvent) -> None:
        self._append_to_disk(event)
        self._append_event_state(event)

    def _create_event(
        self,
        kind: str,
        *,
        source_type: str,
        source_id: str,
        execution_session_id: str | None,
        payload: dict[str, Any],
        timestamp: str,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_id=self._next_event_id(),
            timestamp=timestamp,
            kind=kind,
            source_type=source_type,
            source_id=source_id,
            execution_session_id=execution_session_id,
            payload=payload,
        )

    def _set_subscription(self, subscription: EventSubscription) -> None:
        self._subscriptions[subscription.subscription_id] = subscription

    def _drop_subscription(self, subscription_id: str) -> None:
        self._subscriptions.pop(subscription_id, None)

    def _persist_cursors(self) -> None:
        if self._cursor_path is None:
            return

        if self._cursor_lock_path is None:
            self._persist_cursors_unlocked()
            return
        with advisory_lock(self._cursor_lock_path):
            self._persist_cursors_unlocked()

    def _load_from_disk_unlocked(self) -> None:
        if self._log_path is None:
            return
        if not self._log_path.exists():
            self._set_log_state([], counter=0, signature=None)
            return

        stat_result = self._log_path.stat()
        signature = (stat_result.st_mtime_ns, stat_result.st_size)
        if self._log_signature == signature:
            return

        loaded: list[RuntimeEvent] = []
        with self._log_path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid event log line {line_number} in {self._log_path}"
                    ) from exc
                loaded.append(RuntimeEvent.from_dict(payload))

        self._set_log_state(
            loaded,
            counter=max((self._event_sequence(event.event_id) for event in loaded), default=0),
            signature=signature,
        )

    def _load_cursors_unlocked(self) -> None:
        if self._cursor_path is None:
            return
        if not self._cursor_path.exists():
            self._set_cursor_state({}, {})
            self._set_cursor_signature(None)
            return

        stat_result = self._cursor_path.stat()
        signature = (stat_result.st_mtime_ns, stat_result.st_size)
        if self._cursor_signature == signature:
            return

        try:
            payload = json.loads(self._cursor_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid cursor store: {self._cursor_path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"invalid cursor store: {self._cursor_path}")

        event_timestamps = {event.event_id: event.timestamp for event in self._events}
        known_event_ids = {event.event_id for event in self._events}
        loaded: dict[str, str] = {}
        loaded_updated_at: dict[str, str] = {}
        for consumer_id, event_id in payload.items():
            if not isinstance(consumer_id, str):
                continue
            cursor_event_id: str | None = None
            updated_at: str | None = None
            if isinstance(event_id, str):
                cursor_event_id = event_id
                updated_at = event_timestamps.get(event_id)
            elif isinstance(event_id, dict):
                cursor_event_id = event_id.get("event_id")
                updated_at = event_id.get("updated_at")
            if not isinstance(cursor_event_id, str):
                continue
            if cursor_event_id not in known_event_ids:
                continue
            loaded[consumer_id] = cursor_event_id
            if isinstance(updated_at, str):
                loaded_updated_at[consumer_id] = updated_at
        if len(loaded) > self.MAX_CURSORS:
            ordered = sorted(
                loaded,
                key=lambda consumer_id: loaded_updated_at.get(consumer_id, event_timestamps.get(loaded[consumer_id], "")),
            )
            keep_ids = set(ordered[-self.MAX_CURSORS:])
            loaded = {consumer_id: event_id for consumer_id, event_id in loaded.items() if consumer_id in keep_ids}
            loaded_updated_at = {
                consumer_id: updated_at
                for consumer_id, updated_at in loaded_updated_at.items()
                if consumer_id in keep_ids
            }
        self._set_cursor_state(loaded, loaded_updated_at)
        self._set_cursor_signature(signature)

    def _persist_cursors_unlocked(self) -> None:
        self._cursor_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._cursor_path.with_suffix(self._cursor_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(
                {
                    consumer_id: {
                        "event_id": event_id,
                        "updated_at": self._cursor_updated_at.get(consumer_id),
                    }
                    for consumer_id, event_id in self._cursors.items()
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        atomic_replace(temp_path, self._cursor_path)
        stat_result = self._cursor_path.stat()
        self._set_cursor_signature((stat_result.st_mtime_ns, stat_result.st_size))

    def _rewrite_log_unlocked(self, events: list[RuntimeEvent]) -> None:
        if self._log_path is None:
            self._set_events_state(list(events))
            return

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._log_path.with_suffix(self._log_path.suffix + ".tmp")
        payload = "\n".join(
            json.dumps(event.to_dict(), ensure_ascii=True) for event in events
        )
        if payload:
            payload += "\n"
        temp_path.write_text(payload, encoding="utf-8")
        atomic_replace(temp_path, self._log_path)
        stat_result = self._log_path.stat()
        self._set_log_state(
            list(events),
            counter=self._counter,
            signature=(stat_result.st_mtime_ns, stat_result.st_size),
        )

    def _set_cursor_local(self, consumer_id: str, event_id: str | None, *, updated_at: str | None = None) -> None:
        if event_id is None:
            self._drop_cursor_local(consumer_id)
        else:
            if consumer_id not in self._cursors and len(self._cursors) >= self.MAX_CURSORS:
                raise ValueError(f"too many cursors: {len(self._cursors)} >= {self.MAX_CURSORS}")
            self._set_cursor_entry(consumer_id, event_id, updated_at or self._utc_now())

    def _cleanup_stale_cursors_unlocked(self, reference_time: dt.datetime, max_age_seconds: int) -> int:
        removed_ids: list[str] = []
        for consumer_id, updated_at in list(self._cursor_updated_at.items()):
            age_seconds = (reference_time - self._parse_timestamp(updated_at)).total_seconds()
            if age_seconds > max_age_seconds:
                removed_ids.append(consumer_id)

        for consumer_id in removed_ids:
            self._drop_cursor_local(consumer_id)
        return len(removed_ids)

    def _drop_cursor_local(self, consumer_id: str) -> None:
        self._cursors.pop(consumer_id, None)
        self._cursor_updated_at.pop(consumer_id, None)

    def _set_cursor_entry(self, consumer_id: str, event_id: str, updated_at: str) -> None:
        self._cursors[consumer_id] = event_id
        self._cursor_updated_at[consumer_id] = updated_at

    def _set_log_state(
        self,
        events: list[RuntimeEvent],
        *,
        counter: int,
        signature: tuple[int, int] | None,
    ) -> None:
        self._events = events
        self._set_counter(counter)
        self._set_log_signature(signature)

    def _set_events_state(self, events: list[RuntimeEvent]) -> None:
        self._events = events

    def _set_cursor_state(self, cursors: dict[str, str], cursor_updated_at: dict[str, str]) -> None:
        self._cursors = cursors
        self._cursor_updated_at = cursor_updated_at

    def _set_subscriptions_state(self, subscriptions: dict[str, EventSubscription]) -> None:
        self._subscriptions = subscriptions

    def _next_event_id(self) -> str:
        self._increment_counter()
        return f"evt-{self._counter:06d}"

    def _append_event_state(self, event: RuntimeEvent) -> None:
        self._events.append(event)

    def _increment_counter(self) -> None:
        self._counter += 1

    def _set_counter(self, counter: int) -> None:
        self._counter = counter

    def _set_log_signature(self, signature: tuple[int, int] | None) -> None:
        self._log_signature = signature

    def _set_cursor_signature(self, signature: tuple[int, int] | None) -> None:
        self._cursor_signature = signature

    @staticmethod
    def _event_sequence(event_id: str) -> int:
        prefix, _, sequence = event_id.partition("-")
        if prefix != "evt" or not sequence.isdigit():
            raise ValueError(f"invalid event id: {event_id}")
        return int(sequence)

    @staticmethod
    def _lock_path_for(path: Path | None) -> Path | None:
        if path is None:
            return None
        return path.with_name(path.name + ".lock")

    @staticmethod
    def _utc_now() -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _parse_timestamp(timestamp: str) -> dt.datetime:
        normalized = timestamp.replace("Z", "+00:00")
        return dt.datetime.fromisoformat(normalized)
