from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class StrictBackendValidationSnapshot:
    status: str
    backend: str
    ready: bool
    reason: str
    checked_at: str
    session_id: str
    workspace_signature: str = ""
    restricted_token_status: str = ""
    write_boundary_status: str = ""
    read_boundary_status: str = ""

    @classmethod
    def not_run(cls) -> "StrictBackendValidationSnapshot":
        return cls(
            status="not_run",
            backend="",
            ready=False,
            reason="strict backend validation has not been run",
            checked_at="",
            session_id="",
        )

    def as_stale(self, reason: str) -> "StrictBackendValidationSnapshot":
        return replace(self, status="stale", reason=reason)


class StrictBackendValidationCache:
    def __init__(self) -> None:
        self._set_snapshot(StrictBackendValidationSnapshot.not_run())

    def snapshot(self) -> StrictBackendValidationSnapshot:
        return self._snapshot

    def record(
        self,
        *,
        session_id: str,
        backend: str,
        ready: bool,
        status: str,
        reason: str,
        workspace_signature: str = "",
        restricted_token_status: str = "",
        write_boundary_status: str = "",
        read_boundary_status: str = "",
    ) -> StrictBackendValidationSnapshot:
        self._set_snapshot(
            StrictBackendValidationSnapshot(
                status=status,
                backend=backend,
                ready=ready,
                reason=reason,
                checked_at=self._utc_now_iso(),
                session_id=session_id,
                workspace_signature=workspace_signature,
                restricted_token_status=restricted_token_status,
                write_boundary_status=write_boundary_status,
                read_boundary_status=read_boundary_status,
            )
        )
        return self._snapshot

    def _set_snapshot(self, snapshot: StrictBackendValidationSnapshot) -> None:
        self._snapshot = snapshot

    def _utc_now_iso(self) -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
