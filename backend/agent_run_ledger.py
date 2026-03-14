from __future__ import annotations

import logging
import uuid
from typing import Callable

from backend.agents import AgentLaunchPlan
from core.models import AgentRunRecord
from backend.runner import RunnerResult

logger = logging.getLogger(__name__)
MAX_AGENT_RUN_HISTORY = 500


class AgentRunLedger:
    def __init__(self, now: Callable[[], str]) -> None:
        self._now = now
        self._set_runs([])

    def replace_state(self, runs: list[AgentRunRecord]) -> None:
        self._set_runs(self._trim_runs(list(runs)))

    def list_runs(self, execution_session_id: str | None = None) -> list[AgentRunRecord]:
        if execution_session_id is None:
            return list(self.runs)
        return [run for run in self.runs if run.execution_session_id == execution_session_id]

    def get_run(self, run_id: str) -> AgentRunRecord:
        for record in self.runs:
            if record.run_id == run_id:
                return record
        raise KeyError(f"Unknown agent run: {run_id}")

    def create_started_run(
        self,
        *,
        agent_session_id: str,
        execution_session_id: str,
        agent_kind: str,
        runner_mode: str,
        backend: str,
        io_mode: str,
        transport_status: str,
        argv: list[str],
        pid: int | None,
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            run_id=f"run-{uuid.uuid4().hex[:8]}",
            agent_session_id=agent_session_id,
            execution_session_id=execution_session_id,
            agent_kind=agent_kind,
            runner_mode=runner_mode,
            backend=backend,
            io_mode=io_mode,
            transport_status=transport_status,
            argv=list(argv),
            created_at=self._now(),
            ended_at=None,
            pid=pid,
            returncode=-1,
            status="running",
            stdout="",
            stderr="",
        )
        self._append_run(record)
        logger.info(
            "agent_run.created run_id=%s execution_session_id=%s backend=%s mode=%s",
            record.run_id,
            record.execution_session_id,
            record.backend,
            record.runner_mode,
        )
        return record

    def create_completed_run(
        self,
        *,
        agent_session_id: str,
        execution_session_id: str,
        agent_kind: str,
        runner_mode: str,
        io_mode: str,
        transport_status: str,
        argv: list[str],
        launch_plan: AgentLaunchPlan,
        result: RunnerResult,
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            run_id=f"run-{uuid.uuid4().hex[:8]}",
            agent_session_id=agent_session_id,
            execution_session_id=execution_session_id,
            agent_kind=agent_kind,
            runner_mode=runner_mode,
            backend=result.backend,
            io_mode=io_mode,
            transport_status=transport_status,
            argv=list(argv),
            created_at=self._now(),
            ended_at=self._now(),
            pid=None,
            returncode=result.returncode,
            status=self.status_for_result(launch_plan, result, transport_status=transport_status),
            stdout=result.stdout,
            stderr=result.stderr,
        )
        self._append_run(record)
        logger.info(
            "agent_run.completed run_id=%s execution_session_id=%s backend=%s status=%s returncode=%s",
            record.run_id,
            record.execution_session_id,
            record.backend,
            record.status,
            record.returncode,
        )
        return record

    def reconcile_restored_runs(self) -> bool:
        changed = False
        for record in self.runs:
            if record.status != "running":
                continue
            record.mark_interrupted(
                ended_at=record.ended_at or self._now(),
                reason="restored runtime lost live process handle",
            )
            changed = True
            logger.warning(
                "agent_run.reconciled_interrupted run_id=%s execution_session_id=%s",
                record.run_id,
                record.execution_session_id,
            )
        return changed

    def _append_run(self, record: AgentRunRecord) -> None:
        self._set_runs(self._trim_runs([*self.runs, record]))

    def _set_runs(self, runs: list[AgentRunRecord]) -> None:
        self.runs = runs

    @staticmethod
    def _trim_runs(runs: list[AgentRunRecord]) -> list[AgentRunRecord]:
        if len(runs) <= MAX_AGENT_RUN_HISTORY:
            return runs
        removed = len(runs) - MAX_AGENT_RUN_HISTORY
        logger.info("agent_run.trim removed=%s", removed)
        return list(runs[-MAX_AGENT_RUN_HISTORY:])

    @staticmethod
    def status_for_result(
        launch_plan: AgentLaunchPlan,
        result: RunnerResult,
        *,
        transport_status: str = "native",
    ) -> str:
        if not launch_plan.available:
            return "unavailable"
        if transport_status == "unavailable":
            return "unavailable"
        if result.returncode == 126:
            return "blocked"
        return AgentRunLedger.status_for_returncode(result.returncode)

    @staticmethod
    def status_for_returncode(returncode: int) -> str:
        if returncode == 0:
            return "completed"
        return "failed"
