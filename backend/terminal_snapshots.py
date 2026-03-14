from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.terminal_inbox import TerminalInboxEntry, render_terminal_inbox_entries


@dataclass(frozen=True)
class TerminalWatchSnapshot:
    terminal_id: str
    watch_id: str
    consumer_id: str
    kind_prefix: str | None
    source_type: str | None
    source_id: str | None
    created_at: str | None
    updated_at: str | None
    bridge: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminal_id": self.terminal_id,
            "watch_id": self.watch_id,
            "consumer_id": self.consumer_id,
            "kind_prefix": self.kind_prefix,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "bridge": self.bridge,
        }


@dataclass(frozen=True)
class TerminalInboxSnapshot:
    terminal_id: str
    bound_agent_run_id: str | None
    entries: list[TerminalInboxEntry]
    watch_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminal_id": self.terminal_id,
            "bound_agent_run_id": self.bound_agent_run_id,
            "messages": render_terminal_inbox_entries(self.entries),
            "entries": [entry.to_dict() for entry in self.entries],
            "watch_ids": list(self.watch_ids),
        }
