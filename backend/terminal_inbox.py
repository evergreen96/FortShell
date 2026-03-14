from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.events import RuntimeEvent


@dataclass(frozen=True)
class TerminalInboxEntry:
    kind: str
    text: str
    created_at: str | None
    source_terminal_id: str | None = None
    event_id: str | None = None
    event_kind: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "created_at": self.created_at,
            "source_terminal_id": self.source_terminal_id,
            "event_id": self.event_id,
            "event_kind": self.event_kind,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "payload": dict(self.payload or {}),
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> TerminalInboxEntry:
        return TerminalInboxEntry(
            kind=str(payload.get("kind") or "legacy"),
            text=str(payload.get("text") or ""),
            created_at=payload.get("created_at"),
            source_terminal_id=payload.get("source_terminal_id"),
            event_id=payload.get("event_id"),
            event_kind=payload.get("event_kind"),
            source_type=payload.get("source_type"),
            source_id=payload.get("source_id"),
            payload=dict(payload.get("payload") or {}),
        )


def legacy_terminal_inbox_entry(text: str) -> TerminalInboxEntry:
    return TerminalInboxEntry(kind="legacy", text=text, created_at=None, payload={})


def terminal_message_inbox_entry(
    src_terminal_id: str,
    message: str,
    *,
    created_at: str | None,
) -> TerminalInboxEntry:
    return TerminalInboxEntry(
        kind="terminal-message",
        text=f"from {src_terminal_id}: {message}",
        created_at=created_at,
        source_terminal_id=src_terminal_id,
        payload={"message": message},
    )


def runtime_event_inbox_entry(event: RuntimeEvent) -> TerminalInboxEntry:
    return TerminalInboxEntry(
        kind="runtime-event",
        text=f"[{event.event_id}] {event.kind} source={event.source_type}:{event.source_id} payload={event.payload}",
        created_at=event.timestamp,
        event_id=event.event_id,
        event_kind=event.kind,
        source_type=event.source_type,
        source_id=event.source_id,
        payload=dict(event.payload),
    )


def render_terminal_inbox_entries(entries: list[TerminalInboxEntry]) -> list[str]:
    return [entry.text for entry in entries]
