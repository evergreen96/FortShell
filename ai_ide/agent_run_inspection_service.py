from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ai_ide.models import AgentRunRecord
from ai_ide.process_control_service import ProcessControlService
from ai_ide.runner_models import RunnerProcessHandle


@dataclass(frozen=True)
class AgentProcessInspection:
    source: str
    state: str
    pid: int | None
    returncode: int | None
    backend: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "state": self.state,
            "pid": self.pid,
            "returncode": self.returncode,
            "backend": self.backend,
        }


@dataclass(frozen=True)
class AgentRunInspection:
    record: AgentRunRecord
    process: AgentProcessInspection

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "process": self.process.to_dict(),
        }


class AgentRunInspectionService:
    def __init__(self, process_control_service: ProcessControlService | None = None) -> None:
        self.process_control_service = process_control_service or ProcessControlService()

    def inspect(
        self,
        record: AgentRunRecord,
        handle: RunnerProcessHandle | None = None,
    ) -> AgentRunInspection:
        record_snapshot = replace(record)
        if handle is None:
            return AgentRunInspection(
                record=record_snapshot,
                process=AgentProcessInspection(
                    source="recorded",
                    state="running" if record_snapshot.status == "running" else "exited",
                    pid=record_snapshot.pid,
                    returncode=None if record_snapshot.status == "running" else record_snapshot.returncode,
                    backend=record_snapshot.backend,
                ),
            )

        returncode = handle.process.poll()
        if returncode is not None:
            return AgentRunInspection(
                record=record_snapshot,
                process=AgentProcessInspection(
                    source="local",
                    state="exited",
                    pid=handle.process.pid,
                    returncode=returncode,
                    backend=handle.backend,
                ),
            )

        status = self.process_control_service.request_status(handle)
        if status is not None:
            return AgentRunInspection(
                record=record_snapshot,
                process=AgentProcessInspection(
                    source="helper-control",
                    state=status.state,
                    pid=status.pid,
                    returncode=status.returncode,
                    backend=status.backend or handle.backend,
                ),
            )

        return AgentRunInspection(
            record=record_snapshot,
            process=AgentProcessInspection(
                source="local",
                state="running",
                pid=handle.process.pid,
                returncode=None,
                backend=handle.backend,
            ),
        )
