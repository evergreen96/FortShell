from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from core.internal import is_internal_path
from core.policy import PolicyEngine

logger = logging.getLogger(__name__)
NOT_UNDER_ROOT_ERROR = "Path is not under workspace root"


@dataclass(frozen=True)
class WorkspaceAccessDecision:
    path: Path
    allowed: bool
    access_reason: str
    matched_rule: str | None = None


class WorkspaceAccessService:
    def __init__(self, root: Path, policy_engine: PolicyEngine) -> None:
        self.root = root.resolve()
        self.policy_engine = policy_engine

    def resolve_under_root(self, target: str | Path) -> Path:
        return self._ensure_under_root(self._candidate_path(target))

    def resolve_allowed_path(self, target: str | Path) -> Path:
        candidate = self._candidate_path(target)
        self._ensure_no_symlink_components(candidate)
        self._ensure_no_hardlink_alias(candidate)
        try:
            return self._ensure_allowed(candidate)
        except PermissionError as exc:
            if str(exc) != NOT_UNDER_ROOT_ERROR:
                raise
            return candidate.resolve(strict=False)

    def resolve_readable_path(self, target: str | Path) -> Path:
        """Alias for resolve_allowed_path (reads and writes use the same resolution)."""
        return self.resolve_allowed_path(target)

    def assert_allowed(self, path: Path) -> None:
        self._ensure_allowed(path)

    def inspect_path(self, target: str | Path) -> WorkspaceAccessDecision:
        candidate = self._candidate_path(target)
        try:
            self._ensure_no_symlink_components(candidate)
        except PermissionError:
            return WorkspaceAccessDecision(
                path=candidate.resolve(strict=False),
                allowed=False,
                access_reason="symlink",
            )
        try:
            self._ensure_no_hardlink_alias(candidate)
        except PermissionError:
            return WorkspaceAccessDecision(
                path=candidate.resolve(strict=False),
                allowed=False,
                access_reason="hardlink",
            )
        try:
            resolved = self._ensure_under_root(candidate)
        except PermissionError:
            return WorkspaceAccessDecision(
                path=candidate.resolve(strict=False),
                allowed=True,
                access_reason="unmanaged",
            )
        if is_internal_path(self.root, resolved):
            return WorkspaceAccessDecision(
                path=resolved,
                allowed=False,
                access_reason="internal",
            )
        decision = self.policy_engine.evaluate(resolved)
        if not decision.allowed:
            return WorkspaceAccessDecision(
                path=resolved,
                allowed=False,
                access_reason="policy",
                matched_rule=decision.matched_rule,
            )
        return WorkspaceAccessDecision(
            path=resolved,
            allowed=True,
            access_reason="allowed",
        )

    def iter_visible_children(self, directory: Path) -> Iterable[Path]:
        directory = self._ensure_allowed(directory)
        yield from self._iter_visible_children(directory)

    def iter_visible_files(self, directory: Path) -> Iterable[Path]:
        directory = self._ensure_allowed(directory)
        yield from self._iter_visible_descendants(directory, files_only=True)

    def iter_visible_tree(self, directory: Path | None = None) -> Iterable[Path]:
        directory = self.root if directory is None else self._ensure_allowed(directory)
        yield from self._iter_visible_descendants(directory, files_only=False)

    def is_visible(self, path: Path) -> bool:
        return self.inspect_path(path).allowed

    def _candidate_path(self, target: str | Path) -> Path:
        if isinstance(target, Path):
            return target if target.is_absolute() else self.root / target
        return self.root / target

    def _ensure_under_root(self, path: Path) -> Path:
        candidate = path.resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            logger.debug("path_not_under_root path=%s", path)
            raise PermissionError(NOT_UNDER_ROOT_ERROR) from exc
        return candidate

    def _ensure_allowed(self, path: Path) -> Path:
        self._ensure_no_symlink_components(path)
        self._ensure_no_hardlink_alias(path)
        candidate = self._ensure_under_root(path)
        if is_internal_path(self.root, candidate):
            logger.warning("internal_path blocked=%s", candidate.relative_to(self.root))
            raise PermissionError(f"Blocked internal path: {candidate.relative_to(self.root)}")
        if not self.policy_engine.is_allowed(candidate):
            logger.warning("policy_deny blocked=%s", candidate.relative_to(self.root))
            raise PermissionError(f"Blocked by policy: {candidate.relative_to(self.root)}")
        return candidate

    def _ensure_no_symlink_components(self, path: Path) -> None:
        candidate = path if path.is_absolute() else self.root / path
        try:
            relative = candidate.relative_to(self.root)
        except ValueError:
            return

        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise PermissionError(f"Blocked symlink path: {current.relative_to(self.root)}")

    def _ensure_no_hardlink_alias(self, path: Path) -> None:
        candidate = path if path.is_absolute() else self.root / path
        try:
            relative = candidate.relative_to(self.root)
        except ValueError:
            return
        if not relative.parts or not candidate.exists() or candidate.is_dir():
            return

        try:
            stat_result = candidate.stat()
        except OSError:
            return

        if getattr(stat_result, "st_nlink", 1) > 1:
            raise PermissionError(f"Blocked hardlink path: {relative}")

    def _iter_visible_children(self, directory: Path) -> Iterable[Path]:
        for entry in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            if self.is_visible(entry):
                yield entry

    def _iter_visible_descendants(self, directory: Path, *, files_only: bool) -> Iterable[Path]:
        for entry in self._iter_visible_children(directory):
            if entry.is_dir():
                if not files_only:
                    yield entry
                yield from self._iter_visible_descendants(entry, files_only=files_only)
                continue
            yield entry
