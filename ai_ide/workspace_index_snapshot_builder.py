from __future__ import annotations

import hashlib
from pathlib import Path

from ai_ide.workspace_access_service import WorkspaceAccessService
from ai_ide.workspace_models import WorkspaceIndexEntry, WorkspaceIndexSnapshot


class WorkspaceIndexSnapshotBuilder:
    def __init__(self, root: Path, workspace_access: WorkspaceAccessService) -> None:
        self.root = root.resolve()
        self.workspace_access = workspace_access

    def build(self, *, policy_version: int) -> WorkspaceIndexSnapshot:
        entries = self.build_entries()
        return WorkspaceIndexSnapshot(
            policy_version=policy_version,
            signature=self.signature_for_entries(entries),
            entries=entries,
        )

    def build_signature(self) -> str:
        return self.signature_for_entries(self.build_entries())

    @staticmethod
    def signature_for_entries(entries: list[WorkspaceIndexEntry]) -> str:
        digest = hashlib.sha256()
        for entry in entries:
            digest.update(
                (
                    f"{entry.path}\t{int(entry.is_dir)}\t{entry.size}\t{entry.modified_ns}\n"
                ).encode("utf-8")
            )
        return digest.hexdigest()

    def build_entries(self) -> list[WorkspaceIndexEntry]:
        return [self._entry_from_path(path) for path in self.workspace_access.iter_visible_tree()]

    def _entry_from_path(self, path: Path) -> WorkspaceIndexEntry:
        stat = path.stat()
        return WorkspaceIndexEntry(
            path=path.relative_to(self.root).as_posix(),
            is_dir=path.is_dir(),
            size=0 if path.is_dir() else stat.st_size,
            modified_ns=stat.st_mtime_ns,
        )
