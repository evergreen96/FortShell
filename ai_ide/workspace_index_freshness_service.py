from __future__ import annotations

from collections.abc import Callable

from ai_ide.workspace_index_snapshot_builder import WorkspaceIndexSnapshotBuilder
from ai_ide.workspace_models import WorkspaceIndexSnapshot


class WorkspaceIndexFreshnessService:
    def __init__(
        self,
        snapshot_builder: WorkspaceIndexSnapshotBuilder,
        *,
        current_signature_provider: Callable[[], str] | None = None,
    ) -> None:
        self.snapshot_builder = snapshot_builder
        self.current_signature_provider = current_signature_provider

    def stale_reasons(self, snapshot: WorkspaceIndexSnapshot, *, policy_version: int) -> list[str]:
        if snapshot.policy_version != policy_version:
            return ["policy"]
        if self._workspace_changed(snapshot):
            return ["workspace"]
        return []

    def is_stale(self, snapshot: WorkspaceIndexSnapshot, *, policy_version: int) -> bool:
        return bool(self.stale_reasons(snapshot, policy_version=policy_version))

    def _workspace_changed(self, snapshot: WorkspaceIndexSnapshot) -> bool:
        current_signature = (
            self.current_signature_provider()
            if self.current_signature_provider is not None
            else self.snapshot_builder.build_signature()
        )
        if snapshot.signature:
            return snapshot.signature != current_signature
        return snapshot.entries != self.snapshot_builder.build_entries()
