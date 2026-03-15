from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from typing import TYPE_CHECKING

from backend.agent_run_inspection_service import AgentRunInspection
from core.models import TerminalSession
from backend.terminal_inbox import render_terminal_inbox_entries

if TYPE_CHECKING:
    from backend.agent_runtime import AgentRuntimeManager


@dataclass(frozen=True)
class TerminalBoundRunInspection:
    run_id: str
    status: str
    backend: str
    process_source: str
    process_state: str
    process_pid: int | None
    process_returncode: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "backend": self.backend,
            "process_source": self.process_source,
            "process_state": self.process_state,
            "process_pid": self.process_pid,
            "process_returncode": self.process_returncode,
        }


@dataclass(frozen=True)
class TerminalSessionInspection:
    session: TerminalSession
    bound_run: TerminalBoundRunInspection | None

    def to_dict(self) -> dict[str, Any]:
        inbox_entries = self.session.snapshot_inbox()
        return {
            "terminal_id": self.session.terminal_id,
            "name": self.session.name,
            "created_at": self.session.created_at,
            "transport": self.session.transport,
            "runner_mode": self.session.runner_mode,
            "profile_id": self.session.profile_id,
            "profile_label": self.session.profile_label,
            "status": self.session.status,
            "stale_reason": self.session.stale_reason,
            "execution_session_id": self.session.execution_session_id,
            "bound_agent_run_id": self.session.bound_agent_run_id,
            "io_mode": self.session.io_mode,
            "command_history": self.session.snapshot_command_history(),
            "inbox": render_terminal_inbox_entries(inbox_entries),
            "inbox_entries": [entry.to_dict() for entry in inbox_entries],
            "bound_run": self.bound_run.to_dict() if self.bound_run is not None else None,
        }


class TerminalInspectionService:
    def __init__(self, agent_runtime: "AgentRuntimeManager" | None = None) -> None:
        self.agent_runtime = agent_runtime

    def inspect(self, session: TerminalSession) -> TerminalSessionInspection:
        session_snapshot = replace(session)
        if session_snapshot.bound_agent_run_id is None:
            return TerminalSessionInspection(session=session_snapshot, bound_run=None)
        if self.agent_runtime is None:
            return TerminalSessionInspection(
                session=session_snapshot,
                bound_run=self._missing_bound_run(session_snapshot.bound_agent_run_id),
            )
        try:
            inspection = self.agent_runtime.inspect_run(session_snapshot.bound_agent_run_id)
        except KeyError:
            inspection = None
        return TerminalSessionInspection(
            session=session_snapshot,
            bound_run=self._bound_run_from_agent_inspection(session_snapshot.bound_agent_run_id, inspection),
        )

    def inspect_many(self, sessions: list[TerminalSession]) -> list[TerminalSessionInspection]:
        session_snapshots = [replace(session) for session in sessions]
        if not any(session.bound_agent_run_id is not None for session in session_snapshots):
            return [TerminalSessionInspection(session=session, bound_run=None) for session in session_snapshots]
        if self.agent_runtime is None:
            return [
                TerminalSessionInspection(
                    session=session,
                    bound_run=(
                        self._missing_bound_run(session.bound_agent_run_id)
                        if session.bound_agent_run_id is not None
                        else None
                    ),
                )
                for session in session_snapshots
            ]
        inspections_by_run_id = {
            inspection.record.run_id: inspection for inspection in self.agent_runtime.list_run_inspections()
        }
        return [
            TerminalSessionInspection(
                session=session,
                bound_run=self._bound_run_from_agent_inspection(
                    session.bound_agent_run_id,
                    inspections_by_run_id.get(session.bound_agent_run_id or ""),
                ),
            )
            for session in session_snapshots
        ]

    @staticmethod
    def _bound_run_from_agent_inspection(
        run_id: str | None,
        inspection: AgentRunInspection | None,
    ) -> TerminalBoundRunInspection | None:
        if run_id is None:
            return None
        if inspection is None:
            return TerminalInspectionService._missing_bound_run(run_id)
        return TerminalBoundRunInspection(
            run_id=run_id,
            status=inspection.record.status,
            backend=inspection.process.backend,
            process_source=inspection.process.source,
            process_state=inspection.process.state,
            process_pid=inspection.process.pid,
            process_returncode=inspection.process.returncode,
        )

    @staticmethod
    def _missing_bound_run(run_id: str) -> TerminalBoundRunInspection:
        return TerminalBoundRunInspection(
            run_id=run_id,
            status="missing",
            backend="(unknown)",
            process_source="missing",
            process_state="missing",
            process_pid=None,
            process_returncode=None,
        )
