from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from ai_ide.atomic_files import atomic_replace
from ai_ide.file_lock import advisory_lock
from ai_ide.models import MAX_AGENT_RUN_STREAM_BYTES, AgentRunRecord, AgentRunWatch

logger = logging.getLogger(__name__)


class AgentRuntimeStateStore:
    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path.resolve() if state_path is not None else None
        self.state_lock_path = self._lock_path_for(self.state_path)
        self._set_state_signature(None)
        self._set_cached_state([], {})

    def load(self) -> tuple[list[AgentRunRecord], dict[str, AgentRunWatch]]:
        if self.state_path is None:
            return [], {}
        with self._state_lock():
            if not self.state_path.exists():
                self._set_state_signature(None)
                self._set_cached_state([], {})
                return [], {}
            current_signature = self._signature_for(self.state_path)
            if self._state_signature == current_signature:
                logger.debug("agent_state_store.cache.hit path=%s", self.state_path)
                return list(self._cached_runs), dict(self._cached_watches)
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            runs = [
                AgentRunRecord(
                    run_id=item["run_id"],
                    agent_session_id=item["agent_session_id"],
                    execution_session_id=item["execution_session_id"],
                    agent_kind=item["agent_kind"],
                    runner_mode=item["runner_mode"],
                    backend=item["backend"],
                    io_mode=item.get("io_mode", "pipe"),
                    transport_status=item.get("transport_status", "native"),
                    argv=list(item.get("argv", [])),
                    created_at=item["created_at"],
                    ended_at=item.get("ended_at"),
                    pid=item.get("pid"),
                    returncode=item["returncode"],
                    status=item["status"],
                    stdout=self._trim_stream(item.get("stdout", "")),
                    stderr=self._trim_stream(item.get("stderr", "")),
                )
                for item in payload.get("runs", [])
            ]
            trimmed_streams = sum(
                int(len(item.get("stdout", "")) > MAX_AGENT_RUN_STREAM_BYTES)
                + int(len(item.get("stderr", "")) > MAX_AGENT_RUN_STREAM_BYTES)
                for item in payload.get("runs", [])
            )
            if trimmed_streams:
                logger.info("agent_state_store.stream_trimmed count=%s", trimmed_streams)
            watches = {
                item["watch_id"]: AgentRunWatch(
                    watch_id=item["watch_id"],
                    run_id=item["run_id"],
                    consumer_id=item["consumer_id"],
                    created_at=item["created_at"],
                    name=item["name"],
                    updated_at=item.get("updated_at") or item["created_at"],
                )
                for item in payload.get("run_watches", [])
            }
            self._set_state_signature(current_signature)
            self._set_cached_state(runs, watches)
            logger.debug("agent_state_store.load path=%s runs=%s watches=%s", self.state_path, len(runs), len(watches))
            return runs, watches

    def save(self, runs: list[AgentRunRecord], run_watches: dict[str, AgentRunWatch]) -> None:
        if self.state_path is None:
            return
        with self._state_lock():
            self._save_unlocked(runs, run_watches)

    def save_unlocked(self, runs: list[AgentRunRecord], run_watches: dict[str, AgentRunWatch]) -> None:
        if self.state_path is None:
            return
        self._save_unlocked(runs, run_watches)

    def _save_unlocked(self, runs: list[AgentRunRecord], run_watches: dict[str, AgentRunWatch]) -> None:
        assert self.state_path is not None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "runs": [asdict(record) for record in runs],
            "run_watches": [asdict(watch) for watch in run_watches.values()],
        }
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        atomic_replace(temp_path, self.state_path)
        self._set_state_signature(self._signature_for(self.state_path))
        self._set_cached_state(runs, run_watches)
        logger.debug("agent_state_store.save path=%s runs=%s watches=%s", self.state_path, len(runs), len(run_watches))

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

    @staticmethod
    def _trim_stream(text: str) -> str:
        if len(text) <= MAX_AGENT_RUN_STREAM_BYTES:
            return text
        return text[-MAX_AGENT_RUN_STREAM_BYTES:]

    def _set_cached_state(
        self,
        runs: list[AgentRunRecord],
        watches: dict[str, AgentRunWatch],
    ) -> None:
        self._cached_runs = list(runs)
        self._cached_watches = dict(watches)

    def _set_state_signature(self, signature: tuple[int, int] | None) -> None:
        self._state_signature = signature

    def _state_lock(self):
        return self.state_lock()


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False
