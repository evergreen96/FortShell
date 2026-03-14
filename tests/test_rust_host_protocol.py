from __future__ import annotations

import unittest

from ai_ide.models import AgentSession, ExecutionSession, PolicyState, WriteProposal
from ai_ide.models import AuditEvent
from ai_ide.rust_host_protocol import (
    AgentSessionSnapshot,
    PolicyChangeResult,
    PolicyChangeSnapshot,
    RenderedProposal,
    RuntimeMetricsSnapshot,
    RustHostError,
    RustHostSnapshot,
    audit_list_request,
    metrics_show_request,
    parse_response_envelope,
    policy_add_deny_rule_request,
    review_render_request,
    review_list_request,
    review_stage_write_request,
    rotate_agent_session_request,
    snapshot_request,
    workspace_grep_request,
    workspace_index_refresh_request,
    workspace_index_show_request,
    workspace_list_request,
    workspace_tree_request,
)
from ai_ide.workspace_models import (
    WorkspaceCatalogEntry,
    WorkspaceIndexEntry,
    WorkspaceIndexSnapshot,
    WorkspaceSearchMatch,
)


class RustHostProtocolTests(unittest.TestCase):
    def test_parse_snapshot_envelope_uses_existing_models(self) -> None:
        response = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "snapshot",
                "data": {
                  "policy_state": {"deny_globs": ["secrets/**"], "version": 2},
                  "execution_session": {
                    "session_id": "sess-00000001",
                    "policy_version": 2,
                    "created_at": "1970-01-01T00:00:01Z",
                    "status": "active",
                    "rotated_from": null
                  },
                  "agent_session": {
                    "agent_session_id": "agent-00000001",
                    "execution_session_id": "sess-00000001",
                    "agent_kind": "codex",
                    "created_at": "1970-01-01T00:00:02Z",
                    "status": "active",
                    "rotated_from": null
                  },
                  "review_count": 4,
                  "pending_review_count": 1
                }
              },
              "error": null
            }
            """
        )

        self.assertTrue(response.ok)
        self.assertEqual("snapshot", response.response_type)
        self.assertEqual(
            RustHostSnapshot(
                policy_state=PolicyState(["secrets/**"], version=2),
                execution_session=ExecutionSession(
                    session_id="sess-00000001",
                    policy_version=2,
                    created_at="1970-01-01T00:00:01Z",
                    status="active",
                    rotated_from=None,
                ),
                agent_session=AgentSession(
                    agent_session_id="agent-00000001",
                    execution_session_id="sess-00000001",
                    agent_kind="codex",
                    created_at="1970-01-01T00:00:02Z",
                    status="active",
                    rotated_from=None,
                ),
                review_count=4,
                pending_review_count=1,
            ),
            response.data,
        )

    def test_parse_workspace_envelopes_return_workspace_models(self) -> None:
        listing = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "workspace_list",
                "data": [
                  {
                    "path": "notes",
                    "name": "notes",
                    "is_dir": true,
                    "display_name": "notes/",
                    "display_path": "notes/"
                  }
                ]
              },
              "error": null
            }
            """
        )
        grep = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "workspace_grep",
                "data": [
                  {
                    "path": "notes/todo.txt",
                    "line_number": 1,
                    "line_text": "visible plan"
                  }
                ]
              },
              "error": null
            }
            """
        )

        self.assertEqual("workspace_list", listing.response_type)
        self.assertEqual([WorkspaceCatalogEntry(path="notes", is_dir=True)], listing.data)
        self.assertEqual("workspace_grep", grep.response_type)
        self.assertEqual(
            [WorkspaceSearchMatch(path="notes/todo.txt", line_number=1, line_text="visible plan")],
            grep.data,
        )

    def test_parse_workspace_index_envelope_returns_snapshot_model(self) -> None:
        response = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "workspace_index_snapshot",
                "data": {
                  "policy_version": 3,
                  "entries": [
                    {
                      "path": "notes/todo.txt",
                      "is_dir": false,
                      "size": 12,
                      "modified_ns": 42
                    }
                  ]
                }
              },
              "error": null
            }
            """
        )

        self.assertEqual("workspace_index_snapshot", response.response_type)
        self.assertEqual(
            WorkspaceIndexSnapshot(
                policy_version=3,
                entries=[
                    WorkspaceIndexEntry(
                        path="notes/todo.txt",
                        is_dir=False,
                        size=12,
                        modified_ns=42,
                    )
                ],
            ),
            response.data,
        )

    def test_parse_policy_change_snapshot_envelope_returns_combined_model(self) -> None:
        response = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "policy_change_snapshot",
                "data": {
                  "result": {
                    "changed": true,
                    "rotated": true,
                    "execution_session_id": "sess-00000002",
                    "agent_session_id": "agent-00000002",
                    "policy_version": 3
                  },
                  "snapshot": {
                    "policy_state": {"deny_globs": ["secrets/**"], "version": 3},
                    "execution_session": {
                      "session_id": "sess-00000002",
                      "policy_version": 3,
                      "created_at": "1970-01-01T00:00:03Z",
                      "status": "active",
                      "rotated_from": "sess-00000001"
                    },
                    "agent_session": {
                      "agent_session_id": "agent-00000002",
                      "execution_session_id": "sess-00000002",
                      "agent_kind": "codex",
                      "created_at": "1970-01-01T00:00:04Z",
                      "status": "active",
                      "rotated_from": "agent-00000001"
                    },
                    "review_count": 0,
                    "pending_review_count": 0
                  }
                }
              },
              "error": null
            }
            """
        )

        self.assertTrue(response.ok)
        self.assertEqual("policy_change_snapshot", response.response_type)
        self.assertEqual(
            PolicyChangeSnapshot(
                result=PolicyChangeResult(
                    changed=True,
                    rotated=True,
                    execution_session_id="sess-00000002",
                    agent_session_id="agent-00000002",
                    policy_version=3,
                ),
                snapshot=RustHostSnapshot(
                    policy_state=PolicyState(["secrets/**"], version=3),
                    execution_session=ExecutionSession(
                        session_id="sess-00000002",
                        policy_version=3,
                        created_at="1970-01-01T00:00:03Z",
                        status="active",
                        rotated_from="sess-00000001",
                    ),
                    agent_session=AgentSession(
                        agent_session_id="agent-00000002",
                        execution_session_id="sess-00000002",
                        agent_kind="codex",
                        created_at="1970-01-01T00:00:04Z",
                        status="active",
                        rotated_from="agent-00000001",
                    ),
                    review_count=0,
                    pending_review_count=0,
                ),
            ),
            response.data,
        )

    def test_parse_runtime_metrics_and_audit_envelopes(self) -> None:
        metrics = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "runtime_metrics_snapshot",
                "data": {
                  "list_count": 1,
                  "read_count": 2,
                  "write_count": 3,
                  "grep_count": 4,
                  "blocked_count": 5,
                  "terminal_runs": 6,
                  "audit_event_count": 7
                }
              },
              "error": null
            }
            """
        )
        audit = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "audit_list",
                "data": [
                  {
                    "timestamp": "2026-03-07T00:00:00Z",
                    "session_id": "sess-1",
                    "action": "read",
                    "target": "C:/repo/file.txt",
                    "allowed": true,
                    "detail": "bytes=10"
                  }
                ]
              },
              "error": null
            }
            """
        )

        self.assertEqual(
            RuntimeMetricsSnapshot(
                list_count=1,
                read_count=2,
                write_count=3,
                grep_count=4,
                blocked_count=5,
                terminal_runs=6,
                audit_event_count=7,
            ),
            metrics.data,
        )
        self.assertEqual(
            [
                AuditEvent(
                    timestamp="2026-03-07T00:00:00Z",
                    session_id="sess-1",
                    action="read",
                    target="C:/repo/file.txt",
                    allowed=True,
                    detail="bytes=10",
                )
            ],
            audit.data,
        )

    def test_parse_agent_session_snapshot_envelope_returns_combined_model(self) -> None:
        response = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "agent_session_snapshot",
                "data": {
                  "session": {
                    "agent_session_id": "agent-00000002",
                    "execution_session_id": "sess-00000001",
                    "agent_kind": "claude",
                    "created_at": "1970-01-01T00:00:02Z",
                    "status": "active",
                    "rotated_from": "agent-00000001"
                  },
                  "snapshot": {
                    "policy_state": {"deny_globs": [], "version": 1},
                    "execution_session": {
                      "session_id": "sess-00000001",
                      "policy_version": 1,
                      "created_at": "1970-01-01T00:00:01Z",
                      "status": "active",
                      "rotated_from": null
                    },
                    "agent_session": {
                      "agent_session_id": "agent-00000002",
                      "execution_session_id": "sess-00000001",
                      "agent_kind": "claude",
                      "created_at": "1970-01-01T00:00:02Z",
                      "status": "active",
                      "rotated_from": "agent-00000001"
                    },
                    "review_count": 0,
                    "pending_review_count": 0
                  }
                }
              },
              "error": null
            }
            """
        )

        self.assertTrue(response.ok)
        self.assertEqual("agent_session_snapshot", response.response_type)
        self.assertEqual(
            AgentSessionSnapshot(
                session=AgentSession(
                    agent_session_id="agent-00000002",
                    execution_session_id="sess-00000001",
                    agent_kind="claude",
                    created_at="1970-01-01T00:00:02Z",
                    status="active",
                    rotated_from="agent-00000001",
                ),
                snapshot=RustHostSnapshot(
                    policy_state=PolicyState([], version=1),
                    execution_session=ExecutionSession(
                        session_id="sess-00000001",
                        policy_version=1,
                        created_at="1970-01-01T00:00:01Z",
                        status="active",
                        rotated_from=None,
                    ),
                    agent_session=AgentSession(
                        agent_session_id="agent-00000002",
                        execution_session_id="sess-00000001",
                        agent_kind="claude",
                        created_at="1970-01-01T00:00:02Z",
                        status="active",
                        rotated_from="agent-00000001",
                    ),
                    review_count=0,
                    pending_review_count=0,
                ),
            ),
            response.data,
        )

    def test_parse_review_list_envelope_returns_write_proposals(self) -> None:
        response = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "review_list",
                "data": [
                  {
                    "proposal_id": "rev-00000001",
                    "target": "src/app.py",
                    "session_id": "sess-00000001",
                    "agent_session_id": "agent-00000001",
                    "created_at": "1970-01-01T00:00:01Z",
                    "updated_at": "1970-01-01T00:00:02Z",
                    "status": "pending",
                    "base_sha256": "abc123",
                    "base_text": "old\\n",
                    "proposed_text": "new\\n"
                  }
                ]
              },
              "error": null
            }
            """
        )

        self.assertEqual("review_list", response.response_type)
        self.assertEqual(
            [
                WriteProposal(
                    proposal_id="rev-00000001",
                    target="src/app.py",
                    session_id="sess-00000001",
                    agent_session_id="agent-00000001",
                    created_at="1970-01-01T00:00:01Z",
                    updated_at="1970-01-01T00:00:02Z",
                    status="pending",
                    base_sha256="abc123",
                    base_text="old\n",
                    proposed_text="new\n",
                )
            ],
            response.data,
        )

    def test_parse_error_envelope_returns_machine_readable_error(self) -> None:
        response = parse_response_envelope(
            """
            {
              "ok": false,
              "response": null,
              "error": {
                "code": "review_blocked_by_policy",
                "message": "Blocked by policy: secrets/token.txt"
              }
            }
            """
        )

        self.assertFalse(response.ok)
        self.assertIsNone(response.response_type)
        self.assertEqual(
            RustHostError(
                code="review_blocked_by_policy",
                message="Blocked by policy: secrets/token.txt",
            ),
            response.error,
        )

    def test_parse_review_render_envelope_returns_rendered_payload(self) -> None:
        response = parse_response_envelope(
            """
            {
              "ok": true,
              "response": {
                "type": "review_render",
                "data": {
                  "proposal_id": "rev-00000001",
                  "content": "proposal_id=rev-00000001 target=src/app.py\\n--- a/src/app.py\\n+++ b/src/app.py"
                }
              },
              "error": null
            }
            """
        )

        self.assertEqual("review_render", response.response_type)
        self.assertEqual(
            RenderedProposal(
                proposal_id="rev-00000001",
                content="proposal_id=rev-00000001 target=src/app.py\n--- a/src/app.py\n+++ b/src/app.py",
            ),
            response.data,
        )

    def test_request_builders_use_stable_wire_types(self) -> None:
        self.assertEqual({"type": "snapshot"}, snapshot_request())
        self.assertEqual(
            {"type": "rotate_agent_session", "agent_kind": "claude"},
            rotate_agent_session_request("claude"),
        )
        self.assertEqual(
            {"type": "policy_add_deny_rule", "rule": "secrets/**"},
            policy_add_deny_rule_request("secrets/**"),
        )
        self.assertEqual(
            {"type": "workspace_list", "target": "notes"},
            workspace_list_request("notes"),
        )
        self.assertEqual(
            {"type": "workspace_tree", "target": "src"},
            workspace_tree_request("src"),
        )
        self.assertEqual(
            {"type": "workspace_grep", "pattern": "todo", "target": "notes"},
            workspace_grep_request("todo", "notes"),
        )
        self.assertEqual({"type": "workspace_index_show"}, workspace_index_show_request())
        self.assertEqual({"type": "workspace_index_refresh"}, workspace_index_refresh_request())
        self.assertEqual({"type": "metrics_show"}, metrics_show_request())
        self.assertEqual({"type": "audit_list", "limit": 10, "allowed": False}, audit_list_request(10, False))
        self.assertEqual(
            {"type": "review_list", "status": "pending", "limit": 10},
            review_list_request("pending", 10),
        )
        self.assertEqual(
            {"type": "review_render", "proposal_id": "rev-1"},
            review_render_request("rev-1"),
        )
        self.assertEqual(
            {
                "type": "review_stage_write",
                "target": "src/app.py",
                "proposed_text": "print('hello')\n",
            },
            review_stage_write_request("src/app.py", "print('hello')\n"),
        )
        self.assertEqual(
            {
                "type": "review_stage_write",
                "target": "src/app.py",
                "proposed_text": "print('hello')\n",
                "session_id": "sess-1",
                "agent_session_id": "agent-1",
            },
            review_stage_write_request(
                "src/app.py",
                "print('hello')\n",
                session_id="sess-1",
                agent_session_id="agent-1",
            ),
        )


if __name__ == "__main__":
    unittest.main()
