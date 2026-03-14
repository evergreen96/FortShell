from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from core.workspace_access_service import WorkspaceAccessService
from core.workspace_models import WorkspaceCatalogEntry, WorkspaceSearchMatch

MAX_GREP_FILE_BYTES = 1_048_576
logger = logging.getLogger(__name__)


class WorkspaceCatalogService:
    def __init__(self, root: Path, workspace_access: WorkspaceAccessService) -> None:
        self.root = root.resolve()
        self.workspace_access = workspace_access

    def list_dir(self, target: str | Path = ".") -> list[WorkspaceCatalogEntry]:
        directory = self._resolve_directory(target)
        if self._is_under_root(directory):
            return [self._entry_from_path(entry) for entry in self.workspace_access.iter_visible_children(directory)]
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
        except OSError as exc:
            raise PermissionError(f"Directory not readable: {target}") from exc
        return [self._entry_from_path(entry) for entry in entries]

    def iter_tree(self, target: str | Path = ".") -> Iterable[WorkspaceCatalogEntry]:
        directory = self._resolve_directory(target)
        entries = (
            self.workspace_access.iter_visible_tree(directory)
            if self._is_under_root(directory)
            else self._iter_unmanaged_tree(directory, target)
        )
        for entry in entries:
            yield self._entry_from_path(entry)

    def grep(self, pattern: str, target_dir: str | Path = ".") -> list[WorkspaceSearchMatch]:
        directory = self._resolve_directory(target_dir)
        matches: list[WorkspaceSearchMatch] = []
        files = (
            self.workspace_access.iter_visible_files(directory)
            if self._is_under_root(directory)
            else self._iter_unmanaged_files(directory, target_dir)
        )
        for path in files:
            try:
                if path.stat().st_size > MAX_GREP_FILE_BYTES:
                    logger.warning("workspace.grep.skipped_oversized path=%s", path)
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                logger.debug("workspace.grep.skipped_unreadable path=%s", path)
                continue

            rel_path = self._display_path(path)
            for line_number, line in enumerate(content.splitlines(), start=1):
                if pattern in line:
                    matches.append(
                        WorkspaceSearchMatch(
                            path=rel_path,
                            line_number=line_number,
                            line_text=line.strip(),
                        )
                    )
        return matches

    def _resolve_directory(self, target: str | Path) -> Path:
        directory = self.workspace_access.resolve_readable_path(target)
        if not directory.exists() or not directory.is_dir():
            raise FileNotFoundError(f"Directory not found: {target}")
        return directory

    def _entry_from_path(self, path: Path) -> WorkspaceCatalogEntry:
        return WorkspaceCatalogEntry(
            path=self._display_path(path),
            is_dir=path.is_dir(),
        )

    def _iter_unmanaged_tree(self, directory: Path, target: str | Path) -> list[Path]:
        try:
            return sorted(directory.rglob("*"), key=lambda item: item.as_posix().lower())
        except OSError as exc:
            raise PermissionError(f"Directory not readable: {target}") from exc

    def _iter_unmanaged_files(self, directory: Path, target: str | Path) -> list[Path]:
        try:
            return sorted((path for path in directory.rglob("*") if path.is_file()), key=lambda item: item.as_posix().lower())
        except OSError as exc:
            raise PermissionError(f"Directory not readable: {target}") from exc

    def _display_path(self, path: Path) -> str:
        if self._is_under_root(path):
            return path.relative_to(self.root).as_posix()
        return path.as_posix()

    def _is_under_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False
