from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentTransportProviderDecision:
    requested_io_mode: str
    resolved_io_mode: str
    transport_status: str
    detail: str
    provider_name: str
    provider_ready: bool
    provider_detail: str
    supports_pty: bool


class PipeOnlyTransportProvider:
    name = "pipe-only"

    def resolve(self, requested_io_mode: str) -> AgentTransportProviderDecision:
        provider_detail = "runtime exposes pipe transport only; PTY transport is not implemented yet"
        if requested_io_mode in {"pipe", "session-placeholder"}:
            return AgentTransportProviderDecision(
                requested_io_mode=requested_io_mode,
                resolved_io_mode="pipe",
                transport_status="native",
                detail="runtime pipe transport is native for this adapter",
                provider_name=self.name,
                provider_ready=True,
                provider_detail=provider_detail,
                supports_pty=False,
            )
        if requested_io_mode == "pty_preferred":
            return AgentTransportProviderDecision(
                requested_io_mode=requested_io_mode,
                resolved_io_mode="pipe",
                transport_status="degraded",
                detail="runtime has no PTY transport yet; using pipe fallback",
                provider_name=self.name,
                provider_ready=True,
                provider_detail=provider_detail,
                supports_pty=False,
            )
        if requested_io_mode == "pty_required":
            return AgentTransportProviderDecision(
                requested_io_mode=requested_io_mode,
                resolved_io_mode="none",
                transport_status="unavailable",
                detail="runtime has no PTY transport yet",
                provider_name=self.name,
                provider_ready=True,
                provider_detail=provider_detail,
                supports_pty=False,
            )
        return AgentTransportProviderDecision(
            requested_io_mode=requested_io_mode,
            resolved_io_mode="pipe",
            transport_status="native",
            detail=f"unknown io preference {requested_io_mode}; using pipe",
            provider_name=self.name,
            provider_ready=True,
            provider_detail=provider_detail,
            supports_pty=False,
        )
