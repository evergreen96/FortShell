from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app import AIIdeApp


def handle_audit_command(app: "AIIdeApp", parts: list[str]) -> str:
    if len(parts) < 2:
        raise ValueError("Usage: audit list [limit] [all|allowed|blocked]")

    subcommand = parts[1]
    if subcommand == "list":
        limit = int(parts[2]) if len(parts) >= 3 else 20
        allowed_filter = _parse_allowed_filter(parts[3]) if len(parts) >= 4 else None
        events = app.list_audit_events(limit, allowed=allowed_filter)
        rows = [
            f"{event.timestamp} session={event.session_id} action={event.action} "
            f"allowed={str(event.allowed).lower()} target={_display_target(app, event.target)} detail={event.detail}"
            for event in events
        ]
        return "\n".join(rows) if rows else "(no audit events)"

    raise ValueError("Usage: audit list [limit] [all|allowed|blocked]")


def _parse_allowed_filter(value: str) -> bool | None:
    normalized = value.lower()
    if normalized in {"all", "none", "-"}:
        return None
    if normalized == "allowed":
        return True
    if normalized == "blocked":
        return False
    raise ValueError("Usage: audit list [limit] [all|allowed|blocked]")


def _display_target(app: "AIIdeApp", target: str) -> str:
    try:
        relative = Path(target).resolve(strict=False).relative_to(app.root)
    except ValueError:
        return target
    return relative.as_posix() or "."
