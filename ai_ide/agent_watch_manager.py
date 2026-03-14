from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Callable

from ai_ide.events import EventBus, RuntimeEvent
from ai_ide.models import AgentRunWatch

logger = logging.getLogger(__name__)
MAX_AGENT_RUN_WATCHES = 200


class AgentRunWatchManager:
    def __init__(
        self,
        event_bus: EventBus | None,
        *,
        now: Callable[[], str],
        persist_state: Callable[[], None],
        refresh_active_runs: Callable[[], None],
        ensure_run_exists: Callable[[str], None],
    ) -> None:
        self.event_bus = event_bus
        self._now = now
        self._persist_state = persist_state
        self._refresh_active_runs = refresh_active_runs
        self._ensure_run_exists = ensure_run_exists
        self._set_watch_state({})

    def replace_state(self, run_watches: dict[str, AgentRunWatch]) -> bool:
        if len(run_watches) <= MAX_AGENT_RUN_WATCHES:
            self._set_watch_state(dict(run_watches))
            return False
        removed = len(run_watches) - MAX_AGENT_RUN_WATCHES
        self._set_watch_state(self._trim_watches(dict(run_watches)))
        logger.info("agent_watch.replace.trim removed=%s", removed)
        return True

    def watch_run(self, run_id: str, *, name: str | None = None, replay: bool = False) -> AgentRunWatch:
        self._ensure_run_exists(run_id)
        event_bus = self._require_event_bus()
        if len(self.run_watches) >= MAX_AGENT_RUN_WATCHES:
            raise ValueError(f"Too many agent watches: {len(self.run_watches)} >= {MAX_AGENT_RUN_WATCHES}")
        watch_id = f"agent-watch-{uuid.uuid4().hex[:8]}"
        consumer_id = f"agent-run:{run_id}:watch:{watch_id}"
        watch = AgentRunWatch(
            watch_id=watch_id,
            run_id=run_id,
            consumer_id=consumer_id,
            created_at=self._now(),
            name=name or watch_id,
            updated_at=self._now(),
        )
        self._set_watch(watch)
        if replay:
            event_bus.set_cursor(consumer_id, None)
        else:
            latest = event_bus.list_events(
                limit=1,
                kind_prefix="agent.run",
                source_type="agent-run",
                source_id=run_id,
            )
            last_seen = latest[0].event_id if latest else None
            event_bus.set_cursor(consumer_id, last_seen)
        self._persist_state()
        logger.info("agent_watch.created watch_id=%s run_id=%s replay=%s", watch_id, run_id, replay)
        return watch

    def list_watches(self, run_id: str | None = None) -> list[AgentRunWatch]:
        watches = list(self.run_watches.values())
        if run_id is None:
            return watches
        return [watch for watch in watches if watch.run_id == run_id]

    def pull_watch(self, watch_id: str, *, limit: int = 20) -> list[RuntimeEvent]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        event_bus = self._require_event_bus()
        watch = self.get_watch(watch_id)
        self._refresh_active_runs()
        watch.touch(self._now())
        events = event_bus.pull_events(
            watch.consumer_id,
            limit=limit,
            kind_prefix="agent.run",
            source_type="agent-run",
            source_id=watch.run_id,
            advance=True,
        )
        self._persist_state()
        logger.info("agent_watch.pull watch_id=%s run_id=%s event_count=%s", watch_id, watch.run_id, len(events))
        return events

    def unwatch_run(self, watch_id: str) -> None:
        event_bus = self._require_event_bus()
        watch = self.get_watch(watch_id)
        event_bus.set_cursor(watch.consumer_id, None)
        self._drop_watch(watch_id)
        self._persist_state()
        logger.info("agent_watch.removed watch_id=%s run_id=%s", watch_id, watch.run_id)

    def get_watch(self, watch_id: str) -> AgentRunWatch:
        if watch_id not in self.run_watches:
            raise KeyError(f"Unknown agent watch: {watch_id}")
        return self.run_watches[watch_id]

    def cleanup_stale_watches(self, max_age_seconds: int, *, now: str | None = None) -> int:
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be >= 0")

        reference_time = self._parse_timestamp(now or self._now())
        removed = 0
        for watch_id, watch in list(self.run_watches.items()):
            updated_at = watch.updated_at or watch.created_at
            age_seconds = (reference_time - self._parse_timestamp(updated_at)).total_seconds()
            if age_seconds <= max_age_seconds:
                continue
            if self.event_bus is not None:
                self.event_bus.set_cursor(watch.consumer_id, None)
            self._drop_watch(watch_id)
            removed += 1
        if removed:
            self._persist_state()
            logger.info("agent_watch.cleanup removed=%s", removed)
        return removed

    def _set_watch(self, watch: AgentRunWatch) -> None:
        self.run_watches[watch.watch_id] = watch

    def _drop_watch(self, watch_id: str) -> None:
        self.run_watches.pop(watch_id, None)

    def _set_watch_state(self, run_watches: dict[str, AgentRunWatch]) -> None:
        self.run_watches = run_watches

    @staticmethod
    def _trim_watches(run_watches: dict[str, AgentRunWatch]) -> dict[str, AgentRunWatch]:
        ordered = sorted(
            run_watches.values(),
            key=lambda watch: watch.updated_at or watch.created_at,
        )
        return {
            watch.watch_id: watch
            for watch in ordered[-MAX_AGENT_RUN_WATCHES:]
        }

    def _require_event_bus(self) -> EventBus:
        if self.event_bus is None:
            raise RuntimeError("Agent watches require an event bus")
        return self.event_bus

    @staticmethod
    def _parse_timestamp(timestamp: str) -> dt.datetime:
        return dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
