from __future__ import annotations

import json
from collections.abc import Callable

from ai_ide.platforms import PlatformAdapter
from ai_ide.strict_backend_health_service import StrictBackendHealth
from ai_ide.strict_backend_validation_cache import StrictBackendValidationSnapshot


class RunnerStatusService:
    def __init__(
        self,
        platform_adapter: PlatformAdapter,
        *,
        strict_backend_health_provider: Callable[[], StrictBackendHealth] | None = None,
        strict_backend_validation_provider: Callable[[], StrictBackendValidationSnapshot] | None = None,
        strict_boundary_scope_provider: Callable[[], str] | None = None,
        execution_session_id_provider: Callable[[], str] | None = None,
        workspace_signature_provider: Callable[[], str] | None = None,
    ) -> None:
        self.platform_adapter = platform_adapter
        self.strict_backend_health_provider = strict_backend_health_provider
        self.strict_backend_validation_provider = strict_backend_validation_provider
        self.strict_boundary_scope_provider = strict_boundary_scope_provider
        self.execution_session_id_provider = execution_session_id_provider
        self.workspace_signature_provider = workspace_signature_provider

    def status_payload(self, mode: str) -> dict[str, str | bool]:
        capabilities = self.platform_adapter.capabilities()
        health = self._strict_backend_health()
        validation = self._strict_backend_validation(health)
        use_contract = self.strict_backend_health_provider is not None and health.probe_ready
        strict_status = health.contract_status if use_contract else health.probe_status
        strict_detail = health.contract_detail if use_contract else health.probe_detail
        return {
            "mode": mode,
            "platform": capabilities.platform_name,
            "projection_supported": capabilities.projection_supported,
            "strict_sandbox_available": capabilities.strict_sandbox_available,
            "strict_strategy": capabilities.strict_sandbox_strategy,
            "strict_backend_ready": health.ready,
            "strict_backend": health.backend,
            "strict_backend_status": strict_status,
            "strict_backend_detail": strict_detail,
            "strict_backend_probe_ready": health.probe_ready,
            "strict_backend_probe_status": health.probe_status,
            "strict_backend_probe_detail": health.probe_detail,
            "strict_backend_contract_valid": health.contract_valid,
            "strict_backend_contract_status": health.contract_status,
            "strict_backend_contract_detail": health.contract_detail,
            "strict_backend_validation_status": validation.status,
            "strict_backend_validation_reason": validation.reason,
            "strict_backend_validation_backend": validation.backend,
            "strict_backend_validation_ready": validation.ready,
            "strict_backend_validation_checked_at": validation.checked_at,
            "strict_backend_validation_restricted_token": validation.restricted_token_status,
            "strict_backend_validation_write_boundary": validation.write_boundary_status,
            "strict_backend_validation_read_boundary": validation.read_boundary_status,
            "strict_boundary_scope": self._strict_boundary_scope(),
            "strict_preview_guarded": True,
        }

    def status_text(self, mode: str) -> str:
        payload = self.status_payload(mode)
        return (
            f"mode={payload['mode']} platform={payload['platform']} "
            f"projection_supported={payload['projection_supported']} "
            f"strict_sandbox_available={payload['strict_sandbox_available']} "
            f"strict_strategy={payload['strict_strategy']} "
            f"strict_backend_ready={payload['strict_backend_ready']} "
            f"strict_backend={payload['strict_backend']} "
            f"strict_backend_status={payload['strict_backend_status']} "
            f"strict_boundary_scope={payload['strict_boundary_scope']} "
            f"strict_backend_validation_status={payload['strict_backend_validation_status']} "
            "strict_preview_guarded=true"
        )

    def probe_text(self) -> str:
        health = self._strict_backend_health()
        if self.strict_backend_health_provider is None:
            return (
                f"platform={health.platform_name} backend={health.backend} "
                f"ready={health.ready} status={health.probe_status} detail={health.probe_detail}"
            )
        return (
            f"platform={health.platform_name} backend={health.backend} "
            f"ready={health.ready} status={health.contract_status if health.probe_ready else health.probe_status} "
            f"detail={health.contract_detail if health.probe_ready else health.probe_detail} "
            f"probe_ready={health.probe_ready} probe_status={health.probe_status}"
        )

    def status_json(self, mode: str) -> str:
        return json.dumps(self.status_payload(mode), sort_keys=True)

    def _strict_backend_health(self) -> StrictBackendHealth:
        if self.strict_backend_health_provider is not None:
            return self.strict_backend_health_provider()
        probe = self.platform_adapter.strict_probe()
        return StrictBackendHealth(
            platform_name=probe.platform_name,
            backend=probe.backend,
            probe_ready=probe.ready,
            probe_status=probe.status_code,
            probe_detail=probe.detail,
            contract_valid=False,
            contract_status="skipped",
            contract_detail="probe-only status",
            ready=probe.ready,
        )

    def _strict_backend_validation(
        self,
        health: StrictBackendHealth,
    ) -> StrictBackendValidationSnapshot:
        if self.strict_backend_validation_provider is None:
            return StrictBackendValidationSnapshot.not_run()

        snapshot = self.strict_backend_validation_provider()
        if snapshot.status == "not_run":
            return snapshot

        if (
            self.execution_session_id_provider is not None
            and snapshot.session_id
            and snapshot.session_id != self.execution_session_id_provider()
        ):
            return snapshot.as_stale("execution session changed after last validation")

        if snapshot.backend and snapshot.backend != health.backend:
            return snapshot.as_stale(
                f"strict backend changed after last validation: {snapshot.backend} -> {health.backend}"
            )

        if (
            self.workspace_signature_provider is not None
            and snapshot.workspace_signature
            and snapshot.workspace_signature != self.workspace_signature_provider()
        ):
            return snapshot.as_stale("visible workspace changed after last validation")

        return snapshot

    def _strict_boundary_scope(self) -> str:
        if self.strict_boundary_scope_provider is not None:
            return self.strict_boundary_scope_provider()
        return "unknown"
