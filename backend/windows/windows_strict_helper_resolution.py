from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

WINDOWS_STRICT_HELPER_ENV = "AI_IDE_WINDOWS_STRICT_HELPER"
WINDOWS_STRICT_HELPER_RUST_DEV = "rust-dev"


def resolve_windows_strict_helper_command() -> list[str] | None:
    configured = os.environ.get(WINDOWS_STRICT_HELPER_ENV)
    if configured:
        if configured.strip().lower() == WINDOWS_STRICT_HELPER_RUST_DEV:
            return _resolve_rust_dev_helper_command()
        return shlex.split(configured, posix=False)
    discovered = _resolve_windows_strict_helper_path()
    if not discovered:
        return None
    return [discovered]


def _resolve_rust_dev_helper_command() -> list[str] | None:
    cargo = shutil.which("cargo")
    if not cargo:
        return None
    manifest = _repo_root() / "rust" / "Cargo.toml"
    if not manifest.exists():
        return None
    return [
        cargo,
        "run",
        "--quiet",
        "--manifest-path",
        str(manifest),
        "-p",
        "ai-ide-windows-helper",
        "--",
    ]


def _resolve_windows_strict_helper_path() -> str | None:
    return shutil.which("ai-ide-restricted-host-helper.exe") or shutil.which("ai-ide-restricted-host-helper")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
