from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ai_ide.app import AIIdeApp
from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME, INTERNAL_POLICY_STATE_FILENAME
from ai_ide.platforms import get_platform_adapter
from ai_ide.rust_host_client import RustHostClient


RUST_HOST_ENABLE_ENV = "AI_IDE_USE_RUST_HOST"
RUST_HOST_BIN_ENV = "AI_IDE_RUST_HOST_BIN"
RUST_HOST_DEFAULT_AGENT_KIND_ENV = "AI_IDE_RUST_HOST_DEFAULT_AGENT_KIND"
RUST_HOST_POLICY_STORE_ENV = "AI_IDE_RUST_HOST_POLICY_STORE"
RUST_HOST_REVIEW_STORE_ENV = "AI_IDE_RUST_HOST_REVIEW_STORE"
RUST_HOST_WORKSPACE_INDEX_STORE_ENV = "AI_IDE_RUST_HOST_WORKSPACE_INDEX_STORE"
RUST_HOST_BROKER_STORE_ENV = "AI_IDE_RUST_HOST_BROKER_STORE"

TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RustHostSettings:
    default_agent_kind: str
    policy_store_path: Path
    review_store_path: Path
    workspace_index_store_path: Path
    broker_store_path: Path
    base_command: tuple[str, ...] | None = None


def create_app(
    project_root: Path,
    *,
    runtime_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AIIdeApp:
    runtime_root = resolve_runtime_root(project_root, runtime_root)
    rust_host_client = build_optional_rust_host_client(
        project_root,
        runtime_root=runtime_root,
        env=env,
    )
    return AIIdeApp(
        project_root,
        runtime_root=runtime_root,
        rust_host_client=rust_host_client,
    )


def resolve_runtime_root(project_root: Path, runtime_root: Path | None = None) -> Path:
    return (runtime_root or get_platform_adapter().runtime_root(project_root)).resolve()


def resolve_rust_host_settings(
    project_root: Path,
    *,
    runtime_root: Path,
    env: Mapping[str, str] | None = None,
) -> RustHostSettings | None:
    env = env or os.environ
    raw_enabled = env.get(RUST_HOST_ENABLE_ENV, "").strip().lower()
    if raw_enabled not in TRUE_VALUES:
        return None

    policy_store_path = Path(
        env.get(
            RUST_HOST_POLICY_STORE_ENV,
            str(project_root / INTERNAL_PROJECT_METADATA_DIR_NAME / INTERNAL_POLICY_STATE_FILENAME),
        )
    ).resolve()
    review_store_path = Path(
        env.get(
            RUST_HOST_REVIEW_STORE_ENV,
            str(runtime_root / "reviews" / "state.json"),
        )
    ).resolve()
    workspace_index_store_path = Path(
        env.get(
            RUST_HOST_WORKSPACE_INDEX_STORE_ENV,
            str(runtime_root / "workspace" / "index.json"),
        )
    ).resolve()
    broker_store_path = Path(
        env.get(
            RUST_HOST_BROKER_STORE_ENV,
            str(runtime_root / "broker" / "state.json"),
        )
    ).resolve()
    default_agent_kind = env.get(RUST_HOST_DEFAULT_AGENT_KIND_ENV, "default").strip() or "default"
    rust_host_bin = env.get(RUST_HOST_BIN_ENV, "").strip()
    base_command = (rust_host_bin,) if rust_host_bin else None

    return RustHostSettings(
        default_agent_kind=default_agent_kind,
        policy_store_path=policy_store_path,
        review_store_path=review_store_path,
        workspace_index_store_path=workspace_index_store_path,
        broker_store_path=broker_store_path,
        base_command=base_command,
    )


def build_optional_rust_host_client(
    project_root: Path,
    *,
    runtime_root: Path,
    env: Mapping[str, str] | None = None,
) -> RustHostClient | None:
    settings = resolve_rust_host_settings(
        project_root,
        runtime_root=runtime_root,
        env=env,
    )
    if settings is None:
        return None

    return RustHostClient(
        project_root,
        default_agent_kind=settings.default_agent_kind,
        policy_store_path=settings.policy_store_path,
        review_store_path=settings.review_store_path,
        workspace_index_store_path=settings.workspace_index_store_path,
        broker_store_path=settings.broker_store_path,
        base_command=settings.base_command,
    )
