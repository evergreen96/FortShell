from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app import AIIdeApp


def policy_snapshot(app: "AIIdeApp") -> dict[str, object]:
    return {
        "kind": "policy",
        "version": app.policy.state.version,
        "deny_globs": list(app.policy.state.deny_globs),
        "execution_session_id": app.sessions.current_session_id,
        "agent_session_id": app.sessions.current_agent_session_id,
    }


def add_policy_rule(app: "AIIdeApp", rule: str) -> dict[str, object]:
    changed, execution_session_id = app.add_policy_rule(rule)
    if changed:
        app.note_workspace_visibility_change("policy.add", target=rule)
    return {
        "kind": "policy_change",
        "action": "add",
        "rule": rule,
        "changed": changed,
        "policy_version": app.policy.state.version,
        "execution_session_id": execution_session_id,
        "agent_session_id": app.sessions.current_agent_session_id,
    }


def remove_policy_rule(app: "AIIdeApp", rule: str) -> dict[str, object]:
    changed, execution_session_id = app.remove_policy_rule(rule)
    if changed:
        app.note_workspace_visibility_change("policy.remove", target=rule)
    return {
        "kind": "policy_change",
        "action": "remove",
        "rule": rule,
        "changed": changed,
        "policy_version": app.policy.state.version,
        "execution_session_id": execution_session_id,
        "agent_session_id": app.sessions.current_agent_session_id,
    }


def handle_policy_command(app: "AIIdeApp", parts: list[str]) -> str:
    if len(parts) < 2:
        raise ValueError("Usage: policy show|add|remove ...")

    subcommand = parts[1]
    json_output = bool(parts[-1:] and parts[-1] == "json")
    args = parts[2:-1] if json_output else parts[2:]
    if subcommand == "show":
        snapshot = policy_snapshot(app)
        if json_output:
            return json.dumps(snapshot, sort_keys=True)
        rules = snapshot["deny_globs"]
        return "deny rules: " + (", ".join(rules) if rules else "(none)")

    if subcommand == "add" and len(args) == 1:
        result = add_policy_rule(app, args[0])
        if json_output:
            return json.dumps(result, sort_keys=True)
        if not result["changed"]:
            return "rule unchanged"
        return (
            f"rule added; new_session={result['execution_session_id']} "
            f"new_agent_session={result['agent_session_id']}"
        )

    if subcommand == "remove" and len(args) == 1:
        result = remove_policy_rule(app, args[0])
        if json_output:
            return json.dumps(result, sort_keys=True)
        if not result["changed"]:
            return "rule not found"
        return (
            f"rule removed; new_session={result['execution_session_id']} "
            f"new_agent_session={result['agent_session_id']}"
        )

    raise ValueError("Usage: policy show [json]|add <glob> [json]|remove <glob> [json]")
