from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from ai_ide.policy import PolicyEngine
from ai_ide.workspace_access_service import WorkspaceAccessService


@dataclass
class ProjectionManifest:
    session_id: str
    root: Path
    file_count: int
    directory_count: int
    policy_version: int


class ProjectedWorkspaceManager:
    def __init__(
        self,
        project_root: Path,
        policy_engine: PolicyEngine,
        runtime_root: Path,
        workspace_access: WorkspaceAccessService | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.policy_engine = policy_engine
        self.runtime_root = runtime_root.resolve() / "projections"
        self.workspace_access = workspace_access or WorkspaceAccessService(self.project_root, self.policy_engine)

    def materialize(self, session_id: str) -> ProjectionManifest:
        target_root = self.runtime_root / session_id
        if target_root.exists():
            shutil.rmtree(target_root)
        target_root.mkdir(parents=True, exist_ok=True)

        file_count = 0
        directory_count = 0
        for source_path in self.workspace_access.iter_visible_tree():
            relative = source_path.relative_to(self.project_root)
            destination = target_root / relative
            if source_path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                directory_count += 1
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            file_count += 1

        manifest = ProjectionManifest(
            session_id=session_id,
            root=target_root,
            file_count=file_count,
            directory_count=directory_count,
            policy_version=self.policy_engine.state.version,
        )
        self._write_manifest(manifest)
        self.cleanup_stale(session_id)
        return manifest

    def projection_root(self, session_id: str) -> Path:
        return self.runtime_root / session_id

    def cleanup(self, session_id: str) -> None:
        target_root = self.runtime_root / session_id
        if target_root.exists():
            shutil.rmtree(target_root)

    def cleanup_stale(self, current_session_id: str) -> None:
        if not self.runtime_root.exists():
            return
        for path in self.runtime_root.iterdir():
            if path.name == current_session_id:
                continue
            if path.is_dir():
                shutil.rmtree(path)

    def read_manifest(self, session_id: str) -> ProjectionManifest:
        manifest_path = self.projection_root(session_id) / ".ai_ide_projection.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return ProjectionManifest(
            session_id=payload["session_id"],
            root=Path(payload["root"]),
            file_count=payload["file_count"],
            directory_count=payload["directory_count"],
            policy_version=payload["policy_version"],
        )

    def _write_manifest(self, manifest: ProjectionManifest) -> None:
        payload = {
            "session_id": manifest.session_id,
            "root": str(manifest.root),
            "file_count": manifest.file_count,
            "directory_count": manifest.directory_count,
            "policy_version": manifest.policy_version,
        }
        manifest_path = manifest.root / ".ai_ide_projection.json"
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @property
    def internal_runtime_dir(self) -> Path:
        return self.runtime_root.parent
