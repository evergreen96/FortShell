from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from backend.agents import AgentLaunchPlan, AgentRegistry
from backend.agent_transport import AgentTransportPlanner, AgentTransportResolution
from core.models import AgentSession
from backend.runner import RunnerResult


@dataclass(frozen=True)
class AgentLaunchBlock:
    backend: str
    returncode: int
    stderr: str

    def to_runner_result(self, mode: str) -> RunnerResult:
        return RunnerResult(
            mode=mode,
            backend=self.backend,
            returncode=self.returncode,
            stdout="",
            stderr=self.stderr,
            working_directory="",
        )


@dataclass(frozen=True)
class PreparedAgentLaunch:
    session: AgentSession
    runner_mode: str
    launch_plan: AgentLaunchPlan
    transport: AgentTransportResolution
    argv: list[str]
    env: dict[str, str]
    block: AgentLaunchBlock | None = None

    @property
    def launchable(self) -> bool:
        return self.block is None


class AgentLaunchCoordinator:
    def __init__(
        self,
        registry: AgentRegistry,
        transport_planner: AgentTransportPlanner,
        default_mode: Callable[[], str],
    ) -> None:
        self.registry = registry
        self.transport_planner = transport_planner
        self._default_mode = default_mode

    def prepare(
        self,
        session: AgentSession,
        *,
        extra_args: list[str] | None = None,
        mode: str | None = None,
    ) -> PreparedAgentLaunch:
        runner_mode = mode or self._default_mode()
        if runner_mode not in {"projected", "strict"}:
            raise ValueError("Agent execution supports only projected or strict mode")

        launch_plan = self.registry.launch_plan(session.agent_kind)
        transport = self.transport_planner.resolve_launch_plan(launch_plan)
        argv = self._build_argv(launch_plan, extra_args)
        env = self._build_agent_env(
            session.agent_session_id,
            session.execution_session_id,
            session.agent_kind,
            launch_plan,
            io_mode=transport.resolved_io_mode,
            transport_status=transport.transport_status,
        )

        block = None
        if not launch_plan.available or launch_plan.launcher is None:
            block = AgentLaunchBlock(
                backend="agent-adapter",
                returncode=127,
                stderr=f"unavailable: {launch_plan.detail}",
            )
        elif transport.transport_status == "unavailable":
            block = AgentLaunchBlock(
                backend="agent-transport",
                returncode=125,
                stderr=f"unavailable transport: {transport.detail}",
            )

        return PreparedAgentLaunch(
            session=session,
            runner_mode=runner_mode,
            launch_plan=launch_plan,
            transport=transport,
            argv=argv,
            env=env,
            block=block,
        )

    @staticmethod
    def _build_argv(launch_plan: AgentLaunchPlan, extra_args: list[str] | None) -> list[str]:
        default_args = list(launch_plan.argv[1:] or ["--version"])
        return [launch_plan.launcher, *(extra_args or default_args)] if launch_plan.launcher else []

    @staticmethod
    def _build_agent_env(
        agent_session_id: str,
        execution_session_id: str,
        agent_kind: str,
        launch_plan: AgentLaunchPlan,
        *,
        io_mode: str,
        transport_status: str,
    ) -> dict[str, str]:
        return {
            "AI_IDE_AGENT_SESSION_ID": agent_session_id,
            "AI_IDE_EXECUTION_SESSION_ID": execution_session_id,
            "AI_IDE_AGENT_KIND": agent_kind,
            "AI_IDE_AGENT_TRANSPORT": launch_plan.transport,
            "AI_IDE_AGENT_IO_MODE": io_mode,
            "AI_IDE_AGENT_TRANSPORT_STATUS": transport_status,
        }
