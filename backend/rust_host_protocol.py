from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from core.models import AgentSession, AuditEvent, ExecutionSession, PolicyState, WriteProposal
from core.workspace_models import (
    WorkspaceCatalogEntry,
    WorkspaceIndexEntry,
    WorkspaceIndexSnapshot,
    WorkspaceSearchMatch,
)


VALID_PROPOSAL_STATUSES = {"pending", "applied", "rejected", "conflict"}
VALID_SESSION_STATUSES = {"active", "stale"}


class RustHostProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class PolicyChangeResult:
    changed: bool
    rotated: bool
    execution_session_id: str
    agent_session_id: str
    policy_version: int


@dataclass(frozen=True)
class PolicyChangeSnapshot:
    result: PolicyChangeResult
    snapshot: RustHostSnapshot


@dataclass(frozen=True)
class AgentSessionSnapshot:
    session: AgentSession
    snapshot: RustHostSnapshot


@dataclass(frozen=True)
class RustHostSnapshot:
    policy_state: PolicyState
    execution_session: ExecutionSession
    agent_session: AgentSession
    review_count: int
    pending_review_count: int


@dataclass(frozen=True)
class RustHostError:
    code: str
    message: str


@dataclass(frozen=True)
class RuntimeMetricsSnapshot:
    list_count: int
    read_count: int
    write_count: int
    grep_count: int
    blocked_count: int
    terminal_runs: int
    audit_event_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "list_count": self.list_count,
            "read_count": self.read_count,
            "write_count": self.write_count,
            "grep_count": self.grep_count,
            "blocked_count": self.blocked_count,
            "terminal_runs": self.terminal_runs,
            "audit_event_count": self.audit_event_count,
        }


@dataclass(frozen=True)
class RustHostResponseEnvelope:
    ok: bool
    response_type: str | None
    data: object | None
    error: RustHostError | None


@dataclass(frozen=True)
class RenderedProposal:
    proposal_id: str
    content: str


def to_json_line(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True)


def snapshot_request() -> dict[str, object]:
    return {"type": "snapshot"}


def rotate_agent_session_request(agent_kind: str | None = None) -> dict[str, object]:
    return {"type": "rotate_agent_session", "agent_kind": agent_kind}


def policy_add_deny_rule_request(rule: str) -> dict[str, object]:
    return {"type": "policy_add_deny_rule", "rule": rule}


def policy_remove_deny_rule_request(rule: str) -> dict[str, object]:
    return {"type": "policy_remove_deny_rule", "rule": rule}


def policy_sync_request() -> dict[str, object]:
    return {"type": "policy_sync"}


def metrics_show_request() -> dict[str, object]:
    return {"type": "metrics_show"}


def audit_list_request(limit: int = 20, allowed: bool | None = None) -> dict[str, object]:
    if limit < 0:
        raise ValueError("Audit list limit must be non-negative")
    return {"type": "audit_list", "limit": limit, "allowed": allowed}


def workspace_list_request(target: str = ".") -> dict[str, object]:
    return {"type": "workspace_list", "target": target}


def workspace_tree_request(target: str = ".") -> dict[str, object]:
    return {"type": "workspace_tree", "target": target}


def workspace_grep_request(pattern: str, target: str = ".") -> dict[str, object]:
    return {"type": "workspace_grep", "pattern": pattern, "target": target}


def workspace_index_show_request() -> dict[str, object]:
    return {"type": "workspace_index_show"}


def workspace_index_refresh_request() -> dict[str, object]:
    return {"type": "workspace_index_refresh"}


def review_list_request(status: str | None = None, limit: int = 20) -> dict[str, object]:
    if status is not None and status not in VALID_PROPOSAL_STATUSES:
        raise ValueError(f"Invalid review status: {status}")
    if limit < 0:
        raise ValueError("Review list limit must be non-negative")
    return {"type": "review_list", "status": status, "limit": limit}


def review_get_request(proposal_id: str) -> dict[str, object]:
    return {"type": "review_get", "proposal_id": proposal_id}


def review_render_request(proposal_id: str) -> dict[str, object]:
    return {"type": "review_render", "proposal_id": proposal_id}


def review_stage_write_request(
    target: str,
    proposed_text: str,
    *,
    session_id: str | None = None,
    agent_session_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "review_stage_write",
        "target": target,
        "proposed_text": proposed_text,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    if agent_session_id is not None:
        payload["agent_session_id"] = agent_session_id
    return payload


def review_apply_request(proposal_id: str) -> dict[str, object]:
    return {"type": "review_apply", "proposal_id": proposal_id}


def review_reject_request(proposal_id: str) -> dict[str, object]:
    return {"type": "review_reject", "proposal_id": proposal_id}


def parse_response_envelope(text: str) -> RustHostResponseEnvelope:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RustHostProtocolError("Invalid host response JSON") from exc

    mapping = _require_mapping(payload, "response envelope")
    ok = _require_bool(mapping.get("ok"), "ok")

    if ok:
        response = _require_mapping(mapping.get("response"), "response")
        response_type = _require_str(response.get("type"), "response.type")
        data = _parse_response_data(response_type, response.get("data"))
        return RustHostResponseEnvelope(
            ok=True,
            response_type=response_type,
            data=data,
            error=None,
        )

    error_mapping = _require_mapping(mapping.get("error"), "error")
    return RustHostResponseEnvelope(
        ok=False,
        response_type=None,
        data=None,
        error=RustHostError(
            code=_require_str(error_mapping.get("code"), "error.code"),
            message=_require_str(error_mapping.get("message"), "error.message"),
        ),
    )


def _parse_response_data(response_type: str, payload: object) -> object:
    if response_type == "snapshot":
        return _parse_snapshot(_require_mapping(payload, "response.data"))
    if response_type == "agent_session":
        return _parse_agent_session(_require_mapping(payload, "response.data"))
    if response_type == "agent_session_snapshot":
        return _parse_agent_session_snapshot(_require_mapping(payload, "response.data"))
    if response_type == "policy_change":
        return _parse_policy_change(_require_mapping(payload, "response.data"))
    if response_type == "policy_change_snapshot":
        return _parse_policy_change_snapshot(_require_mapping(payload, "response.data"))
    if response_type == "runtime_metrics_snapshot":
        return _parse_runtime_metrics_snapshot(_require_mapping(payload, "response.data"))
    if response_type == "audit_list":
        return _parse_audit_events(payload)
    if response_type in {"workspace_list", "workspace_tree"}:
        return _parse_workspace_entries(payload)
    if response_type == "workspace_grep":
        return _parse_workspace_matches(payload)
    if response_type == "workspace_index_snapshot":
        return _parse_workspace_index_snapshot(_require_mapping(payload, "response.data"))
    if response_type == "review_proposal":
        return _parse_write_proposal(_require_mapping(payload, "response.data"))
    if response_type == "review_render":
        mapping = _require_mapping(payload, "response.data")
        return RenderedProposal(
            proposal_id=_require_str(mapping.get("proposal_id"), "proposal_id"),
            content=_require_str(mapping.get("content"), "content"),
        )
    if response_type == "review_list":
        items = _require_list(payload, "response.data")
        return [_parse_write_proposal(_require_mapping(item, "response.data[]")) for item in items]
    raise RustHostProtocolError(f"Unknown response type: {response_type}")


def _parse_snapshot(mapping: Mapping[str, object]) -> RustHostSnapshot:
    return RustHostSnapshot(
        policy_state=_parse_policy_state(_require_mapping(mapping.get("policy_state"), "policy_state")),
        execution_session=_parse_execution_session(
            _require_mapping(mapping.get("execution_session"), "execution_session")
        ),
        agent_session=_parse_agent_session(_require_mapping(mapping.get("agent_session"), "agent_session")),
        review_count=_require_int(mapping.get("review_count"), "review_count"),
        pending_review_count=_require_int(mapping.get("pending_review_count"), "pending_review_count"),
    )


def _parse_policy_change(mapping: Mapping[str, object]) -> PolicyChangeResult:
    return PolicyChangeResult(
        changed=_require_bool(mapping.get("changed"), "changed"),
        rotated=_require_bool(mapping.get("rotated"), "rotated"),
        execution_session_id=_require_str(mapping.get("execution_session_id"), "execution_session_id"),
        agent_session_id=_require_str(mapping.get("agent_session_id"), "agent_session_id"),
        policy_version=_require_int(mapping.get("policy_version"), "policy_version"),
    )


def _parse_policy_change_snapshot(mapping: Mapping[str, object]) -> PolicyChangeSnapshot:
    return PolicyChangeSnapshot(
        result=_parse_policy_change(_require_mapping(mapping.get("result"), "result")),
        snapshot=_parse_snapshot(_require_mapping(mapping.get("snapshot"), "snapshot")),
    )


def _parse_agent_session_snapshot(mapping: Mapping[str, object]) -> AgentSessionSnapshot:
    return AgentSessionSnapshot(
        session=_parse_agent_session(_require_mapping(mapping.get("session"), "session")),
        snapshot=_parse_snapshot(_require_mapping(mapping.get("snapshot"), "snapshot")),
    )


def _parse_runtime_metrics_snapshot(mapping: Mapping[str, object]) -> RuntimeMetricsSnapshot:
    return RuntimeMetricsSnapshot(
        list_count=_require_int(mapping.get("list_count"), "list_count"),
        read_count=_require_int(mapping.get("read_count"), "read_count"),
        write_count=_require_int(mapping.get("write_count"), "write_count"),
        grep_count=_require_int(mapping.get("grep_count"), "grep_count"),
        blocked_count=_require_int(mapping.get("blocked_count"), "blocked_count"),
        terminal_runs=_require_int(mapping.get("terminal_runs"), "terminal_runs"),
        audit_event_count=_require_int(mapping.get("audit_event_count"), "audit_event_count"),
    )


def _parse_policy_state(mapping: Mapping[str, object]) -> PolicyState:
    return PolicyState(
        deny_globs=_require_string_list(mapping.get("deny_globs"), "deny_globs"),
        version=_require_int(mapping.get("version"), "version"),
    )


def _parse_execution_session(mapping: Mapping[str, object]) -> ExecutionSession:
    status = _require_str(mapping.get("status"), "status")
    if status not in VALID_SESSION_STATUSES:
        raise RustHostProtocolError(f"Invalid execution session status: {status}")
    return ExecutionSession(
        session_id=_require_str(mapping.get("session_id"), "session_id"),
        policy_version=_require_int(mapping.get("policy_version"), "policy_version"),
        created_at=_require_str(mapping.get("created_at"), "created_at"),
        status=status,
        rotated_from=_optional_str(mapping.get("rotated_from"), "rotated_from"),
    )


def _parse_agent_session(mapping: Mapping[str, object]) -> AgentSession:
    status = _require_str(mapping.get("status"), "status")
    if status not in VALID_SESSION_STATUSES:
        raise RustHostProtocolError(f"Invalid agent session status: {status}")
    return AgentSession(
        agent_session_id=_require_str(mapping.get("agent_session_id"), "agent_session_id"),
        execution_session_id=_require_str(mapping.get("execution_session_id"), "execution_session_id"),
        agent_kind=_require_str(mapping.get("agent_kind"), "agent_kind"),
        created_at=_require_str(mapping.get("created_at"), "created_at"),
        status=status,
        rotated_from=_optional_str(mapping.get("rotated_from"), "rotated_from"),
    )


def _parse_workspace_entries(payload: object) -> list[WorkspaceCatalogEntry]:
    items = _require_list(payload, "response.data")
    return [_parse_workspace_entry(_require_mapping(item, "response.data[]")) for item in items]


def _parse_workspace_entry(mapping: Mapping[str, object]) -> WorkspaceCatalogEntry:
    path = _require_str(mapping.get("path"), "path")
    is_dir = _require_bool(mapping.get("is_dir"), "is_dir")
    name = _require_str(mapping.get("name"), "name")
    display_name = _require_str(mapping.get("display_name"), "display_name")
    display_path = _require_str(mapping.get("display_path"), "display_path")
    entry = WorkspaceCatalogEntry(path=path, is_dir=is_dir)
    if entry.name != name:
        raise RustHostProtocolError("workspace entry name did not match path")
    if entry.display_name != display_name:
        raise RustHostProtocolError("workspace entry display_name did not match payload")
    if entry.display_path != display_path:
        raise RustHostProtocolError("workspace entry display_path did not match payload")
    return entry


def _parse_audit_events(payload: object) -> list[AuditEvent]:
    items = _require_list(payload, "response.data")
    return [_parse_audit_event(_require_mapping(item, "response.data[]")) for item in items]


def _parse_audit_event(mapping: Mapping[str, object]) -> AuditEvent:
    return AuditEvent(
        timestamp=_require_str(mapping.get("timestamp"), "timestamp"),
        session_id=_require_str(mapping.get("session_id"), "session_id"),
        action=_require_str(mapping.get("action"), "action"),
        target=_require_str(mapping.get("target"), "target"),
        allowed=_require_bool(mapping.get("allowed"), "allowed"),
        detail=_require_str(mapping.get("detail"), "detail"),
    )


def _parse_workspace_matches(payload: object) -> list[WorkspaceSearchMatch]:
    items = _require_list(payload, "response.data")
    return [_parse_workspace_match(_require_mapping(item, "response.data[]")) for item in items]


def _parse_workspace_match(mapping: Mapping[str, object]) -> WorkspaceSearchMatch:
    return WorkspaceSearchMatch(
        path=_require_str(mapping.get("path"), "path"),
        line_number=_require_int(mapping.get("line_number"), "line_number"),
        line_text=_require_str(mapping.get("line_text"), "line_text"),
    )


def _parse_workspace_index_snapshot(mapping: Mapping[str, object]) -> WorkspaceIndexSnapshot:
    entries = [
        _parse_workspace_index_entry(_require_mapping(item, "response.data.entries[]"))
        for item in _require_list(mapping.get("entries"), "entries")
    ]
    return WorkspaceIndexSnapshot(
        policy_version=_require_int(mapping.get("policy_version"), "policy_version"),
        entries=entries,
        signature=str(mapping.get("signature", "")),
    )


def _parse_workspace_index_entry(mapping: Mapping[str, object]) -> WorkspaceIndexEntry:
    return WorkspaceIndexEntry(
        path=_require_str(mapping.get("path"), "path"),
        is_dir=_require_bool(mapping.get("is_dir"), "is_dir"),
        size=_require_int(mapping.get("size"), "size"),
        modified_ns=_require_int(mapping.get("modified_ns"), "modified_ns"),
    )


def _parse_write_proposal(mapping: Mapping[str, object]) -> WriteProposal:
    status = _require_str(mapping.get("status"), "status")
    if status not in VALID_PROPOSAL_STATUSES:
        raise RustHostProtocolError(f"Invalid proposal status: {status}")
    return WriteProposal(
        proposal_id=_require_str(mapping.get("proposal_id"), "proposal_id"),
        target=_require_str(mapping.get("target"), "target"),
        session_id=_require_str(mapping.get("session_id"), "session_id"),
        agent_session_id=_require_str(mapping.get("agent_session_id"), "agent_session_id"),
        created_at=_require_str(mapping.get("created_at"), "created_at"),
        updated_at=_require_str(mapping.get("updated_at"), "updated_at"),
        status=status,
        base_sha256=_optional_str(mapping.get("base_sha256"), "base_sha256"),
        base_text=_optional_str(mapping.get("base_text"), "base_text"),
        proposed_text=_require_str(mapping.get("proposed_text"), "proposed_text"),
    )


def _require_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise RustHostProtocolError(f"{field_name} must be an object")
    return value


def _require_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise RustHostProtocolError(f"{field_name} must be a list")
    return value


def _require_string_list(value: object, field_name: str) -> list[str]:
    items = _require_list(value, field_name)
    result: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise RustHostProtocolError(f"{field_name}[{index}] must be a string")
        result.append(item)
    return result


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise RustHostProtocolError(f"{field_name} must be a string")
    return value


def _optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, field_name)


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RustHostProtocolError(f"{field_name} must be a boolean")
    return value


def _require_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RustHostProtocolError(f"{field_name} must be an integer")
    return value
