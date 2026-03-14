from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from threading import RLock
from typing import Callable

from ai_ide.process_stop_service import ProcessStopService
from ai_ide.models import AgentRunRecord
from ai_ide.runner import RunnerProcessHandle

logger = logging.getLogger(__name__)


@dataclass
class ManagedAgentProcess:
    record: AgentRunRecord
    handle: RunnerProcessHandle
    stdout_offset: int = 0
    stderr_offset: int = 0


class AgentRunSupervisor:
    def __init__(self, process_stop_service: ProcessStopService | None = None) -> None:
        self._set_active_runs({})
        self._lock = RLock()
        self.process_stop_service = process_stop_service or ProcessStopService()

    def attach(self, record: AgentRunRecord, handle: RunnerProcessHandle) -> None:
        with self._lock:
            self._set_active_run(record.run_id, ManagedAgentProcess(record=record, handle=handle))
        logger.info(
            "agent_supervisor.attach run_id=%s backend=%s execution_session_id=%s",
            record.run_id,
            record.backend,
            record.execution_session_id,
        )

    def has(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self.active_runs

    def get(self, run_id: str) -> ManagedAgentProcess | None:
        with self._lock:
            return self.active_runs.get(run_id)

    def iter_active(self) -> list[tuple[str, ManagedAgentProcess]]:
        with self._lock:
            return list(self.active_runs.items())

    def poll(
        self,
        run_id: str,
        *,
        now: Callable[[], str],
        persist_state: Callable[[], None],
        publish_event: Callable[[str, AgentRunRecord, dict[str, object]], None],
        status_for_returncode: Callable[[int], str],
    ) -> AgentRunRecord | None:
        with self._lock:
            managed = self.active_runs.get(run_id)
            if managed is None:
                return None

            changed = self._consume_output(managed, publish_event)
            returncode = managed.handle.process.poll()
            if returncode is None:
                if changed:
                    persist_state()
                return managed.record

            record = managed.record
            record.finish(
                status=status_for_returncode(returncode),
                returncode=returncode,
                ended_at=now(),
            )
            self._consume_output(managed, publish_event)
            self._release_process(run_id)
            persist_state()
            logger.info(
                "agent_supervisor.complete run_id=%s status=%s returncode=%s",
                record.run_id,
                record.status,
                record.returncode,
            )
            publish_event(
                f"agent.run.{record.status}",
                record,
                {"returncode": record.returncode},
            )
            return record

    def stop(
        self,
        run_id: str,
        *,
        now: Callable[[], str],
        persist_state: Callable[[], None],
        publish_event: Callable[[str, AgentRunRecord, dict[str, object]], None],
        reason: str | None = None,
    ) -> AgentRunRecord | None:
        with self._lock:
            managed = self.active_runs.get(run_id)
            if managed is None:
                return None

            returncode = self.process_stop_service.stop(managed.handle)

            self._consume_output(managed, publish_event)
            record = managed.record
            record.mark_stopped(returncode=returncode, ended_at=now(), reason=reason)
            self._release_process(run_id)
            persist_state()
            logger.info(
                "agent_supervisor.stop run_id=%s returncode=%s reason=%s",
                record.run_id,
                record.returncode,
                reason or "stopped",
            )
            publish_event(
                "agent.run.stopped",
                record,
                {"returncode": record.returncode, "reason": reason or "stopped"},
            )
            return record

    def send_input(
        self,
        run_id: str,
        text: str,
        *,
        persist_state: Callable[[], None],
        publish_event: Callable[[str, AgentRunRecord, dict[str, object]], None],
        append_newline: bool = True,
    ) -> tuple[AgentRunRecord | None, str | None]:
        with self._lock:
            managed = self.active_runs.get(run_id)
            if managed is None:
                return None, None
            if managed.handle.process.poll() is not None:
                return managed.record, None
            stdin_file = managed.handle.stdin_file
            if stdin_file is None:
                raise RuntimeError(f"Agent run has no writable stdin: {run_id}")
            payload = text + ("\n" if append_newline else "")
            stdin_file.write(payload)
            stdin_file.flush()
            publish_event(
                "agent.run.stdin",
                managed.record,
                {"chunk": payload, "size": len(payload)},
            )
            persist_state()
            return managed.record, payload

    def _release_process(self, run_id: str) -> None:
        managed = self._drop_active_run(run_id)
        if managed.handle.stdin_file is not None and not managed.handle.stdin_file.closed:
            managed.handle.stdin_file.close()
        managed.handle.stdout_file.close()
        managed.handle.stderr_file.close()

    def _set_active_run(self, run_id: str, managed: ManagedAgentProcess) -> None:
        self.active_runs[run_id] = managed

    def _drop_active_run(self, run_id: str) -> ManagedAgentProcess:
        return self.active_runs.pop(run_id)

    def _set_active_runs(self, active_runs: dict[str, ManagedAgentProcess]) -> None:
        self.active_runs = active_runs

    def _consume_output(
        self,
        managed: ManagedAgentProcess,
        publish_event: Callable[[str, AgentRunRecord, dict[str, object]], None],
    ) -> bool:
        changed = False
        stdout_delta, managed.stdout_offset = self._read_from_offset(managed.handle.stdout_path, managed.stdout_offset)
        stderr_delta, managed.stderr_offset = self._read_from_offset(managed.handle.stderr_path, managed.stderr_offset)
        if stdout_delta:
            changed = True
            managed.record.append_stdout(stdout_delta)
            publish_event(
                "agent.run.stdout",
                managed.record,
                {"chunk": stdout_delta, "size": len(stdout_delta)},
            )
        if stderr_delta:
            changed = True
            managed.record.append_stderr(stderr_delta)
            publish_event(
                "agent.run.stderr",
                managed.record,
                {"chunk": stderr_delta, "size": len(stderr_delta)},
            )
        return changed

    @staticmethod
    def _read_from_offset(path: Path, offset: int) -> tuple[str, int]:
        if not path.exists():
            return "", offset
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            data = handle.read()
            return data, handle.tell()
