from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class WorkspaceCatalogEntry:
    path: str
    is_dir: bool

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def display_name(self) -> str:
        suffix = "/" if self.is_dir else ""
        return f"{self.name}{suffix}"

    @property
    def display_path(self) -> str:
        suffix = "/" if self.is_dir else ""
        return f"{self.path}{suffix}"

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": self.name,
            "is_dir": self.is_dir,
            "display_name": self.display_name,
            "display_path": self.display_path,
        }


@dataclass(frozen=True)
class WorkspaceSearchMatch:
    path: str
    line_number: int
    line_text: str

    def format_cli(self) -> str:
        return f"{self.path}:{self.line_number}:{self.line_text}"

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line_number": self.line_number,
            "line_text": self.line_text,
        }


@dataclass(frozen=True)
class WorkspaceIndexEntry:
    path: str
    is_dir: bool
    size: int
    modified_ns: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "is_dir": self.is_dir,
            "size": self.size,
            "modified_ns": self.modified_ns,
        }


@dataclass(frozen=True)
class WorkspaceIndexSnapshot:
    policy_version: int
    entries: list[WorkspaceIndexEntry]
    signature: str = ""

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def file_count(self) -> int:
        return sum(1 for entry in self.entries if not entry.is_dir)

    @property
    def directory_count(self) -> int:
        return sum(1 for entry in self.entries if entry.is_dir)

    def to_dict(self, *, stale: bool, stale_reasons: list[str] | None = None) -> dict[str, object]:
        stale_reasons = [] if stale_reasons is None else list(stale_reasons)
        return {
            "policy_version": self.policy_version,
            "signature": self.signature,
            "stale": stale,
            "stale_reasons": stale_reasons,
            "entry_count": self.entry_count,
            "file_count": self.file_count,
            "directory_count": self.directory_count,
            "entries": [entry.to_dict() for entry in self.entries],
        }
