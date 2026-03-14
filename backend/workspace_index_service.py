from __future__ import annotations

import logging
from pathlib import Path

from core.policy import PolicyEngine
from core.workspace_access_service import WorkspaceAccessService
from backend.workspace_index_freshness_service import WorkspaceIndexFreshnessService
from backend.workspace_index_snapshot_builder import WorkspaceIndexSnapshotBuilder
from backend.workspace_index_state_store import WorkspaceIndexStateStore
from core.workspace_models import WorkspaceIndexSnapshot

logger = logging.getLogger(__name__)


class WorkspaceIndexService:
    def __init__(
        self,
        root: Path,
        policy_engine: PolicyEngine,
        workspace_access: WorkspaceAccessService,
        *,
        state_store: WorkspaceIndexStateStore,
    ) -> None:
        self.root = root.resolve()
        self.policy_engine = policy_engine
        self.workspace_access = workspace_access
        self.state_store = state_store
        self.snapshot_builder = WorkspaceIndexSnapshotBuilder(self.root, self.workspace_access)
        self.freshness = WorkspaceIndexFreshnessService(self.snapshot_builder)

    def load(self) -> WorkspaceIndexSnapshot:
        return self.state_store.load()

    def is_stale(self, snapshot: WorkspaceIndexSnapshot | None = None) -> bool:
        snapshot = snapshot or self.load()
        stale = self.freshness.is_stale(snapshot, policy_version=self.policy_engine.state.version)
        logger.info(
            "workspace_index.stale stale=%s policy_version=%s signature=%s",
            stale,
            self.policy_engine.state.version,
            snapshot.signature,
        )
        return stale

    def refresh(self) -> WorkspaceIndexSnapshot:
        snapshot = self.snapshot_builder.build(policy_version=self.policy_engine.state.version)
        self.state_store.save(snapshot)
        logger.info(
            "workspace_index.refresh entries=%s policy_version=%s signature=%s",
            len(snapshot.entries),
            snapshot.policy_version,
            snapshot.signature,
        )
        return snapshot
