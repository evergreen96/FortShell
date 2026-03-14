from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path

from ai_ide.agents import AgentRegistry
from ai_ide.agent_event_publisher import AgentRuntimeEventPublisher
from ai_ide.agent_launch_coordinator import AgentLaunchCoordinator, PreparedAgentLaunch
from ai_ide.agent_run_inspection_service import AgentRunInspection, AgentRunInspectionService
from ai_ide.events import EventBus, RuntimeEvent
from ai_ide.agent_run_ledger import AgentRunLedger
from ai_ide.agent_state_store import AgentRuntimeStateStore
from ai_ide.agent_supervisor import AgentRunSupervisor
from ai_ide.agent_transport import AgentTransportPlan, AgentTransportPlanner, AgentTransportResolution
from ai_ide.agent_watch_manager import AgentRunWatchManager
from ai_ide.models import AgentRunRecord, AgentRunWatch
from ai_ide.process_control_service import ProcessControlService
from ai_ide.process_stop_service import ProcessStopService
from ai_ide.runner import RunnerManager, RunnerResult
from ai_ide.session import SessionManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentExecution:
    record: AgentRunRecord
    result: RunnerResult


class AgentRuntimeManager:
    def __init__(
        self,
        registry: AgentRegistry,
        runner_manager: RunnerManager,
        session_manager: SessionManager,
        event_bus: EventBus | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.registry = registry
        self.runner_manager = runner_manager
        self.session_manager = session_manager
        self.event_bus = event_bus
        self.event_publisher = AgentRuntimeEventPublisher(self.event_bus)
        self.transport_planner = AgentTransportPlanner(self.registry)
        self.launch_coordinator = AgentLaunchCoordinator(
            self.registry,
            self.transport_planner,
            self._default_agent_mode,
        )
        self.process_control_service = ProcessControlService()
        self.run_ledger = AgentRunLedger(self._now)
        self.state_store = AgentRuntimeStateStore(state_path)
        self.supervisor = AgentRunSupervisor(ProcessStopService(self.process_control_service))
        self.inspection_service = AgentRunInspectionService(self.process_control_service)
        self.watch_manager = AgentRunWatchManager(
            self.event_bus,
            now=self._now,
            persist_state=self._persist_state,
            refresh_active_runs=self.refresh_active_runs,
            ensure_run_exists=self._get_run,
        )
        self._load_state()

    def execute_current(
        self,
        extra_args: list[str] | None = None,
        *,
        mode: str | None = None,
    ) -> AgentExecution:
        launch = self.launch_coordinator.prepare(
            self.session_manager.current_agent_session,
            extra_args=extra_args,
            mode=mode,
        )
        if not launch.launchable:
            assert launch.block is not None
            result = launch.block.to_runner_result(launch.runner_mode)
            record = self._record_completed_run(launch, result)
            logger.info(
                "agent_runtime.execute_current blocked run_id=%s backend=%s status=%s",
                record.run_id,
                record.backend,
                record.status,
            )
            return AgentExecution(record=record, result=result)

        result = self.runner_manager.run_process_in_mode(
            launch.runner_mode,
            launch.argv,
            execution_session_id=launch.session.execution_session_id,
            env=launch.env,
        )
        record = self._record_completed_run(
            launch,
            result,
        )
        logger.info(
            "agent_runtime.execute_current completed run_id=%s backend=%s status=%s returncode=%s",
            record.run_id,
            record.backend,
            record.status,
            record.returncode,
        )
        return AgentExecution(record=record, result=result)

    def start_current(
        self,
        extra_args: list[str] | None = None,
        *,
        mode: str | None = None,
    ) -> AgentRunRecord:
        prepared = self.launch_coordinator.prepare(
            self.session_manager.current_agent_session,
            extra_args=extra_args,
            mode=mode,
        )
        if not prepared.launchable:
            assert prepared.block is not None
            result = prepared.block.to_runner_result(prepared.runner_mode)
            record = self._record_completed_run(prepared, result)
            logger.info(
                "agent_runtime.start_current blocked run_id=%s backend=%s status=%s",
                record.run_id,
                record.backend,
                record.status,
            )
            return record

        started = self.runner_manager.start_process_in_mode(
            prepared.runner_mode,
            prepared.argv,
            execution_session_id=prepared.session.execution_session_id,
            env=prepared.env,
        )
        if not started.started:
            assert started.result is not None
            record = self._record_completed_run(prepared, started.result)
            logger.info(
                "agent_runtime.start_current completed_immediately run_id=%s backend=%s status=%s returncode=%s",
                record.run_id,
                record.backend,
                record.status,
                record.returncode,
            )
            return record
        assert started.handle is not None
        record = self.run_ledger.create_started_run(
            agent_session_id=prepared.session.agent_session_id,
            execution_session_id=prepared.session.execution_session_id,
            agent_kind=prepared.session.agent_kind,
            runner_mode=prepared.runner_mode,
            backend=started.handle.backend,
            io_mode=prepared.transport.resolved_io_mode,
            transport_status=prepared.transport.transport_status,
            argv=prepared.argv,
            pid=started.handle.process.pid,
        )
        self.supervisor.attach(record, started.handle)
        self._persist_state()
        self.event_publisher.publish_run_event(
            "agent.run.started",
            record,
            {"pid": record.pid, "argv": list(record.argv)},
        )
        logger.info(
            "agent_runtime.start_current started run_id=%s backend=%s pid=%s mode=%s",
            record.run_id,
            record.backend,
            record.pid,
            record.runner_mode,
        )
        return record

    def poll_run(self, run_id: str) -> AgentRunRecord:
        record = self._get_run(run_id)
        if not self.supervisor.has(run_id):
            return record

        updated = self.supervisor.poll(
            run_id,
            now=self._now,
            persist_state=self._persist_state,
            publish_event=self.event_publisher.publish_run_event,
            status_for_returncode=self.run_ledger.status_for_returncode,
        )
        return updated or record

    def stop_run(self, run_id: str, reason: str | None = None) -> AgentRunRecord:
        record = self._get_run(run_id)
        if not self.supervisor.has(run_id):
            return record

        updated = self.supervisor.stop(
            run_id,
            now=self._now,
            persist_state=self._persist_state,
            publish_event=self.event_publisher.publish_run_event,
            reason=reason,
        )
        if updated is not None:
            logger.info(
                "agent_runtime.stop_run run_id=%s status=%s returncode=%s reason=%s",
                updated.run_id,
                updated.status,
                updated.returncode,
                reason or "stopped",
            )
        return updated or record

    def send_input(self, run_id: str, text: str, *, append_newline: bool = True) -> AgentRunRecord:
        record = self._get_run(run_id)
        if not self.supervisor.has(run_id):
            raise ValueError(f"Agent run is not active: {run_id}")
        updated, payload = self.supervisor.send_input(
            run_id,
            text,
            persist_state=self._persist_state,
            publish_event=self.event_publisher.publish_run_event,
            append_newline=append_newline,
        )
        if updated is None:
            raise ValueError(f"Agent run is not active: {run_id}")
        if payload is None:
            self.poll_run(run_id)
            raise ValueError(f"Agent run is not active: {run_id}")
        return updated

    def mark_execution_session_stale(self, execution_session_id: str) -> None:
        for run_id, managed in self.supervisor.iter_active():
            if managed.record.execution_session_id != execution_session_id:
                continue
            self.stop_run(run_id, reason=f"execution session {execution_session_id} became stale")

    def list_runs(self, execution_session_id: str | None = None) -> list[AgentRunRecord]:
        self.refresh_active_runs(execution_session_id=execution_session_id)
        return self.run_ledger.list_runs(execution_session_id)

    def list_run_inspections(self, execution_session_id: str | None = None) -> list[AgentRunInspection]:
        self.refresh_active_runs(execution_session_id=execution_session_id)
        records = self.run_ledger.list_runs(execution_session_id)
        return [
            self.inspection_service.inspect(
                record,
                self.supervisor.get(record.run_id).handle if self.supervisor.has(record.run_id) else None,
            )
            for record in records
        ]

    def get_run(self, run_id: str) -> AgentRunRecord:
        if self.supervisor.has(run_id):
            self.poll_run(run_id)
        return self._get_run(run_id)

    def inspect_run(self, run_id: str) -> AgentRunInspection:
        record = self._get_run(run_id)
        if self.supervisor.has(run_id):
            record = self.poll_run(run_id)
        managed = self.supervisor.get(run_id)
        return self.inspection_service.inspect(record, managed.handle if managed is not None else None)

    def resolve_transport(self, agent_kind: str | None = None) -> AgentTransportResolution:
        kind = agent_kind or self.session_manager.current_agent_session.agent_kind
        return self.transport_planner.resolve_kind(kind)

    def describe_transport(
        self,
        agent_kind: str | None = None,
        *,
        mode: str | None = None,
    ) -> AgentTransportPlan:
        kind = agent_kind or self.session_manager.current_agent_session.agent_kind
        selected_mode = mode or self._default_agent_mode()
        if selected_mode not in {"projected", "strict"}:
            raise ValueError("Agent execution supports only projected or strict mode")
        return self.transport_planner.describe(kind, selected_mode)

    def refresh_active_runs(self, execution_session_id: str | None = None) -> list[AgentRunRecord]:
        refreshed: list[AgentRunRecord] = []
        for run_id, managed in self.supervisor.iter_active():
            if execution_session_id is not None and managed.record.execution_session_id != execution_session_id:
                continue
            refreshed.append(self.poll_run(run_id))
        return refreshed

    def watch_run(self, run_id: str, *, name: str | None = None, replay: bool = False) -> AgentRunWatch:
        return self.watch_manager.watch_run(run_id, name=name, replay=replay)

    def list_watches(self, run_id: str | None = None) -> list[AgentRunWatch]:
        return self.watch_manager.list_watches(run_id)

    def pull_watch(self, watch_id: str, *, limit: int = 20) -> list[RuntimeEvent]:
        return self.watch_manager.pull_watch(watch_id, limit=limit)

    def unwatch_run(self, watch_id: str) -> None:
        self.watch_manager.unwatch_run(watch_id)

    def get_watch(self, watch_id: str) -> AgentRunWatch:
        return self.watch_manager.get_watch(watch_id)

    def cleanup_stale_watches(self, max_age_seconds: int, *, now: str | None = None) -> int:
        return self.watch_manager.cleanup_stale_watches(max_age_seconds, now=now)

    def _record_completed_run(
        self,
        launch: PreparedAgentLaunch,
        result: RunnerResult,
    ) -> AgentRunRecord:
        record = self.run_ledger.create_completed_run(
            agent_session_id=launch.session.agent_session_id,
            execution_session_id=launch.session.execution_session_id,
            agent_kind=launch.session.agent_kind,
            runner_mode=launch.runner_mode,
            io_mode=launch.transport.resolved_io_mode,
            transport_status=launch.transport.transport_status,
            argv=launch.argv,
            launch_plan=launch.launch_plan,
            result=result,
        )
        self._persist_state()
        self.event_publisher.publish_run_event(
            f"agent.run.{record.status}",
            record,
            {"returncode": record.returncode, "argv": list(record.argv)},
        )
        return record

    def _get_run(self, run_id: str) -> AgentRunRecord:
        return self.run_ledger.get_run(run_id)

    def _default_agent_mode(self) -> str:
        return self.runner_manager.mode if self.runner_manager.mode in {"projected", "strict"} else "projected"

    @staticmethod
    def _now() -> str:
        return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    def _load_state(self) -> None:
        runs, run_watches = self.state_store.load()
        self.run_ledger.replace_state(runs)
        watches_trimmed = self.watch_manager.replace_state(run_watches)
        valid_run_ids = {record.run_id for record in self.run_ledger.runs}
        orphaned_watch_ids = [
            watch_id
            for watch_id, watch in list(self.watch_manager.run_watches.items())
            if watch.run_id not in valid_run_ids
        ]
        for watch_id in orphaned_watch_ids:
            self.watch_manager._drop_watch(watch_id)
        if watches_trimmed:
            logger.info("agent_runtime.load.watch_trim")
        if orphaned_watch_ids:
            logger.info("agent_runtime.load.orphaned_watches removed=%s", len(orphaned_watch_ids))
        if self.run_ledger.reconcile_restored_runs() or watches_trimmed or orphaned_watch_ids:
            self.state_store.save(self.run_ledger.runs, self.watch_manager.run_watches)

    def _persist_state(self) -> None:
        self.state_store.save(self.run_ledger.runs, self.watch_manager.run_watches)
