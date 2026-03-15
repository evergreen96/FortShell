from __future__ import annotations

from dataclasses import dataclass

from backend.windows.platforms import PlatformAdapter
from backend.strict_backend_validator import StrictBackendValidator


@dataclass(frozen=True)
class StrictBackendHealth:
    platform_name: str
    backend: str
    probe_ready: bool
    probe_status: str
    probe_detail: str
    contract_valid: bool
    contract_status: str
    contract_detail: str
    ready: bool


class StrictBackendHealthService:
    def __init__(
        self,
        platform_adapter: PlatformAdapter,
        fs_backend,
        session_manager,
        strict_backend_validator: StrictBackendValidator,
    ) -> None:
        self.platform_adapter = platform_adapter
        self.fs_backend = fs_backend
        self.session_manager = session_manager
        self.strict_backend_validator = strict_backend_validator

    def health(self) -> StrictBackendHealth:
        probe = self.platform_adapter.strict_probe()
        if not probe.ready:
            return StrictBackendHealth(
                platform_name=probe.platform_name,
                backend=probe.backend,
                probe_ready=probe.ready,
                probe_status=probe.status_code,
                probe_detail=probe.detail,
                contract_valid=False,
                contract_status="skipped",
                contract_detail="probe not ready",
                ready=False,
            )

        projected_root = self.fs_backend.mount_root
        if projected_root is None:
            projected_root = self.fs_backend.mount(self.session_manager.current_session_id).mount_root
        invocation = self.platform_adapter.strict_backend_invocation("true", projected_root)
        if invocation is None:
            return StrictBackendHealth(
                platform_name=probe.platform_name,
                backend=probe.backend,
                probe_ready=probe.ready,
                probe_status=probe.status_code,
                probe_detail=probe.detail,
                contract_valid=False,
                contract_status="missing_invocation",
                contract_detail="probe reported ready but no strict backend invocation was returned",
                ready=False,
            )

        validation = self.strict_backend_validator.validate(invocation, projected_root=projected_root)
        return StrictBackendHealth(
            platform_name=probe.platform_name,
            backend=probe.backend,
            probe_ready=probe.ready,
            probe_status=probe.status_code,
            probe_detail=probe.detail,
            contract_valid=validation.valid,
            contract_status="valid" if validation.valid else "invalid_contract",
            contract_detail="contract valid" if validation.valid else validation.reason,
            ready=probe.ready and validation.valid,
        )
