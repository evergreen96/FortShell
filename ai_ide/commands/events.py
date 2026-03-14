from __future__ import annotations

from typing import TYPE_CHECKING

from ai_ide.commands.common import format_runtime_events, parse_event_query_args

if TYPE_CHECKING:
    from ai_ide.app import AIIdeApp


def handle_events_command(app: "AIIdeApp", parts: list[str]) -> str:
    if len(parts) < 2:
        raise ValueError(
            "Usage: events list [limit] [kind_prefix|none] [source_type|none] [source_id|none] | "
            "events tail <after_event_id> [limit] [kind_prefix|none] [source_type|none] [source_id|none] | "
            "events cursor <consumer_id> | events ack <consumer_id> <event_id|none> | "
            "events pull <consumer_id> [limit] [kind_prefix|none] [source_type|none] [source_id|none] | "
            "events compact <retain_last> | events gc <max_age_seconds>"
        )
    subcommand = parts[1]
    if subcommand == "list":
        app.agent_runtime.refresh_active_runs()
        limit, kind_prefix, source_type, source_id = parse_event_query_args(parts, 2)
        events = app.events.list_events(
            limit=limit,
            kind_prefix=kind_prefix,
            source_type=source_type,
            source_id=source_id,
        )
        return format_runtime_events(events)
    if subcommand == "tail" and len(parts) >= 3:
        app.agent_runtime.refresh_active_runs()
        after_event_id = parts[2]
        limit, kind_prefix, source_type, source_id = parse_event_query_args(parts, 3)
        events = app.events.list_events(
            after_event_id=after_event_id,
            limit=limit,
            kind_prefix=kind_prefix,
            source_type=source_type,
            source_id=source_id,
        )
        return format_runtime_events(events)
    if subcommand == "cursor" and len(parts) >= 3:
        cursor = app.events.get_cursor(parts[2]) or "(none)"
        updated_at = app.events.get_cursor_updated_at(parts[2]) or "(none)"
        return f"consumer={parts[2]} cursor={cursor} updated_at={updated_at}"
    if subcommand == "ack" and len(parts) >= 4:
        event_id = None if parts[3].lower() == "none" else parts[3]
        app.events.set_cursor(parts[2], event_id)
        return f"consumer={parts[2]} cursor={event_id or '(none)'}"
    if subcommand == "pull" and len(parts) >= 3:
        app.agent_runtime.refresh_active_runs()
        consumer_id = parts[2]
        limit, kind_prefix, source_type, source_id = parse_event_query_args(parts, 3)
        events = app.events.pull_events(
            consumer_id,
            limit=limit,
            kind_prefix=kind_prefix,
            source_type=source_type,
            source_id=source_id,
            advance=True,
        )
        return format_runtime_events(events)
    if subcommand == "compact" and len(parts) >= 3:
        retained, removed = app.events.compact(int(parts[2]))
        return f"retained={retained} removed={removed}"
    if subcommand == "gc" and len(parts) >= 3:
        removed = app.events.cleanup_stale_cursors(int(parts[2]))
        return f"removed_cursors={removed}"
    raise ValueError(
        "Usage: events list [limit] [kind_prefix|none] [source_type|none] [source_id|none] | "
        "events tail <after_event_id> [limit] [kind_prefix|none] [source_type|none] [source_id|none] | "
        "events cursor <consumer_id> | events ack <consumer_id> <event_id|none> | "
        "events pull <consumer_id> [limit] [kind_prefix|none] [source_type|none] [source_id|none] | "
        "events compact <retain_last> | events gc <max_age_seconds>"
    )
