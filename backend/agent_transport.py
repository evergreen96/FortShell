from __future__ import annotations

from dataclasses import dataclass

from backend.agents import AgentLaunchPlan, AgentRegistry
from backend.agent_transport_provider import PipeOnlyTransportProvider


@dataclass(frozen=True)
class AgentTransportResolution:
    requested_io_mode: str
    resolved_io_mode: str
    transport_status: str
    detail: str
    provider_name: str
    provider_ready: bool
    provider_detail: str
    supports_pty: bool


@dataclass(frozen=True)
class AgentTransportPlan:
    agent_kind: str
    runner_mode: str
    adapter_available: bool
    adapter_status: str
    launcher: str | None
    transport: str
    requires_tty: bool
    requested_io_mode: str
    resolved_io_mode: str
    transport_status: str
    launchable: bool
    detail: str
    provider_name: str
    provider_ready: bool
    provider_detail: str
    supports_pty: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_kind": self.agent_kind,
            "runner_mode": self.runner_mode,
            "adapter_available": self.adapter_available,
            "adapter_status": self.adapter_status,
            "launcher": self.launcher,
            "transport": self.transport,
            "requires_tty": self.requires_tty,
            "requested_io_mode": self.requested_io_mode,
            "resolved_io_mode": self.resolved_io_mode,
            "transport_status": self.transport_status,
            "launchable": self.launchable,
            "detail": self.detail,
            "provider_name": self.provider_name,
            "provider_ready": self.provider_ready,
            "provider_detail": self.provider_detail,
            "supports_pty": self.supports_pty,
        }


class AgentTransportPlanner:
    def __init__(self, registry: AgentRegistry, provider: PipeOnlyTransportProvider | None = None) -> None:
        self.registry = registry
        self.provider = provider or PipeOnlyTransportProvider()

    def resolve_kind(self, agent_kind: str) -> AgentTransportResolution:
        launch_plan = self.registry.launch_plan(agent_kind)
        return self.resolve_launch_plan(launch_plan)

    def describe(self, agent_kind: str, runner_mode: str) -> AgentTransportPlan:
        probe = self.registry.probe(agent_kind)
        resolution = self.resolve_kind(agent_kind)
        launcher = probe.launcher or probe.launcher_hint
        return AgentTransportPlan(
            agent_kind=agent_kind,
            runner_mode=runner_mode,
            adapter_available=probe.available,
            adapter_status=probe.status_code,
            launcher=launcher,
            transport=probe.transport,
            requires_tty=probe.requires_tty,
            requested_io_mode=resolution.requested_io_mode,
            resolved_io_mode=resolution.resolved_io_mode,
            transport_status=resolution.transport_status,
            launchable=probe.available and resolution.transport_status != "unavailable",
            detail=f"adapter: {probe.detail}; runtime: {resolution.detail}",
            provider_name=resolution.provider_name,
            provider_ready=resolution.provider_ready,
            provider_detail=resolution.provider_detail,
            supports_pty=resolution.supports_pty,
        )

    def resolve_launch_plan(self, launch_plan: AgentLaunchPlan) -> AgentTransportResolution:
        decision = self.provider.resolve(launch_plan.io_mode_preference)
        return AgentTransportResolution(
            requested_io_mode=decision.requested_io_mode,
            resolved_io_mode=decision.resolved_io_mode,
            transport_status=decision.transport_status,
            detail=decision.detail,
            provider_name=decision.provider_name,
            provider_ready=decision.provider_ready,
            provider_detail=decision.provider_detail,
            supports_pty=decision.supports_pty,
        )
