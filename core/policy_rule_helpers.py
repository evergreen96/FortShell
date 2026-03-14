from __future__ import annotations

from pathlib import PurePosixPath


def deny_rule_for_target(target: str, *, is_dir: bool) -> str:
    normalized = normalize_relative_target(target)
    return f"{normalized}/**" if is_dir else normalized


def normalize_relative_target(target: str) -> str:
    text = target.replace("\\", "/").strip()
    if not text or text in {".", "./"}:
        raise ValueError("Cannot derive a deny rule for the project root")
    if text.startswith("/") or ":" in text.split("/", 1)[0]:
        raise ValueError("Deny rules must use project-relative paths")

    normalized = str(PurePosixPath(text))
    if normalized in {".", ""}:
        raise ValueError("Cannot derive a deny rule for the project root")
    if any(part == ".." for part in PurePosixPath(normalized).parts):
        raise ValueError("Deny rules must stay inside the project root")
    return normalized
