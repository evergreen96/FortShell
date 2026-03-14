from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ai_ide.workspace_index_snapshot_builder import WorkspaceIndexSnapshotBuilder
from ai_ide.workspace_visibility_models import VisibleWorkspaceState


class WorkspaceVisibilitySource(Protocol):
    def current_state(self) -> VisibleWorkspaceState: ...


class SnapshotWorkspaceVisibilitySource:
    def __init__(
        self,
        snapshot_builder: WorkspaceIndexSnapshotBuilder,
        *,
        policy_version_provider: Callable[[], int],
    ) -> None:
        self.snapshot_builder = snapshot_builder
        self.policy_version_provider = policy_version_provider

    def current_state(self) -> VisibleWorkspaceState:
        snapshot = self.snapshot_builder.build(policy_version=self.policy_version_provider())
        return VisibleWorkspaceState(
            signature=snapshot.signature,
            entry_count=snapshot.entry_count,
            policy_version=snapshot.policy_version,
        )
