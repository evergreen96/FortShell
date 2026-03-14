from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_ide.app import AIIdeApp


def handle_workspace_command(app: "AIIdeApp", parts: list[str]) -> str:
    if len(parts) < 2:
        raise ValueError("Usage: workspace list|tree|grep|panel|index ...")

    subcommand = parts[1]
    json_output = bool(parts[-1:] and parts[-1] == "json")
    args = parts[2:-1] if json_output else parts[2:]

    if subcommand == "list":
        if len(args) > 1:
            raise ValueError("Usage: workspace list [dir] [json]")
        target = args[0] if args else "."
        entries = app.list_workspace_entries(target)
        if json_output:
            return json.dumps(
                {
                    "kind": "list",
                    "target": target,
                    "entries": [entry.to_dict() for entry in entries],
                },
                sort_keys=True,
            )
        return "\n".join(entry.display_name for entry in entries) if entries else "(empty)"

    if subcommand == "tree":
        if len(args) > 1:
            raise ValueError("Usage: workspace tree [dir] [json]")
        target = args[0] if args else "."
        entries = app.tree_workspace_entries(target)
        if json_output:
            return json.dumps(
                {
                    "kind": "tree",
                    "target": target,
                    "entries": [entry.to_dict() for entry in entries],
                },
                sort_keys=True,
            )
        return "\n".join(entry.display_path for entry in entries) if entries else "(empty)"

    if subcommand == "grep":
        if not args or len(args) > 2:
            raise ValueError("Usage: workspace grep <pattern> [dir] [json]")
        pattern = args[0]
        target = args[1] if len(args) == 2 else "."
        matches = app.grep_workspace(pattern, target)
        if json_output:
            return json.dumps(
                {
                    "kind": "grep",
                    "pattern": pattern,
                    "target": target,
                    "matches": [match.to_dict() for match in matches],
                },
                sort_keys=True,
            )
        return "\n".join(match.format_cli() for match in matches) if matches else "(no matches)"

    if subcommand == "panel":
        if len(args) > 1:
            raise ValueError("Usage: workspace panel [dir] [json]")
        target = args[0] if args else "."
        snapshot = app.workspace_panel.snapshot(target=target)
        if json_output:
            entries = [
                {
                    "path": entry["path"],
                    "name": entry["name"],
                    "is_dir": entry["is_dir"],
                    "display_name": entry["display_name"],
                    "display_path": entry["display_path"],
                    "deny_rule": entry["suggested_deny_rule"],
                }
                for entry in snapshot["workspace"]["entries"]
            ]
            return json.dumps(
                {
                    "kind": "panel",
                    "target": snapshot["target"],
                    "policy_version": snapshot["policy"]["version"],
                    "deny_rules": snapshot["policy"]["deny_globs"],
                    "execution_session_id": snapshot["session"]["execution_session_id"],
                    "agent_session_id": snapshot["session"]["agent_session_id"],
                    "entries": entries,
                },
                sort_keys=True,
            )
        entry_lines = "\n".join(
            f"{entry['display_path']} deny_rule={entry['suggested_deny_rule']}"
            for entry in snapshot["workspace"]["entries"]
        )
        deny_rules = ", ".join(snapshot["policy"]["deny_globs"]) or "(none)"
        body = entry_lines if entry_lines else "(empty)"
        return (
            f"policy_version={snapshot['policy']['version']} target={snapshot['target']} "
            f"deny_rules={deny_rules}\n{body}"
        )

    if subcommand == "index":
        if len(args) > 1 or (args and args[0] not in {"show", "refresh"}):
            raise ValueError("Usage: workspace index [show|refresh] [json]")
        action = args[0] if args else "show"
        snapshot = (
            app.refresh_workspace_index_snapshot()
            if action == "refresh"
            else app.load_workspace_index_snapshot()
        )
        stale_reasons = app.workspace_index_stale_reasons(snapshot)
        stale = bool(stale_reasons)
        if json_output:
            return json.dumps(
                {
                    "kind": "index",
                    "action": action,
                    **snapshot.to_dict(stale=stale, stale_reasons=stale_reasons),
                },
                sort_keys=True,
            )
        reason_text = ",".join(stale_reasons) if stale_reasons else "-"
        return (
            f"policy_version={snapshot.policy_version} stale={'true' if stale else 'false'} "
            f"reasons={reason_text} entries={snapshot.entry_count} "
            f"files={snapshot.file_count} directories={snapshot.directory_count}"
        )

    raise ValueError(
        "Usage: workspace list [dir] [json] | workspace tree [dir] [json] | "
        "workspace grep <pattern> [dir] [json] | workspace panel [dir] [json] | "
        "workspace index [show|refresh] [json]"
    )
