from __future__ import annotations

from pathlib import Path


INTERNAL_RUNTIME_DIR_NAME = ".ai_ide_runtime"
INTERNAL_PROJECT_METADATA_DIR_NAME = ".ai-ide"
INTERNAL_POLICY_STATE_FILENAME = "policy.json"
INTERNAL_ROOT_DIR_NAMES = {
    INTERNAL_RUNTIME_DIR_NAME,
    INTERNAL_PROJECT_METADATA_DIR_NAME,
}


def is_internal_path(project_root: Path, path: Path) -> bool:
    project_root = project_root.resolve()
    candidate = path.resolve(strict=False)
    try:
        relative = candidate.relative_to(project_root)
    except ValueError:
        return False
    parts = relative.parts
    return bool(parts) and parts[0] in INTERNAL_ROOT_DIR_NAMES
