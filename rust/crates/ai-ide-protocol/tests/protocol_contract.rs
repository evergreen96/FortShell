use ai_ide_protocol::{
    AgentSession, AgentSessionSnapshot, AuditEvent, ExecutionSession, HostRequest, HostResponse,
    HostResponseEnvelope, HostSnapshot, PolicyChangeResult, PolicyChangeSnapshot, PolicyState,
    ProposalStatus, RenderedProposal, RuntimeMetricsSnapshot, RuntimeStatusSnapshot,
    SessionStatus, UsageMetrics, WorkspaceCatalogEntry, WorkspaceIndexEntry,
    WorkspaceIndexSnapshot, WorkspaceSearchMatch, WriteProposal,
};

#[test]
fn policy_state_roundtrip_json_contract() {
    let state = PolicyState {
        deny_globs: vec!["**/secrets/**".to_owned(), "*.key".to_owned()],
        version: 4,
    };
    let json = state.to_json();
    let restored = PolicyState::from_json(&json).expect("policy state should deserialize");

    assert_eq!(state, restored);
}

#[test]
fn policy_state_default_contract() {
    let state = PolicyState::default();
    let json = state.to_json();
    assert!(json.contains("\"version\":1"));

    let restored = PolicyState::from_json(&json).expect("default policy state should deserialize");
    assert_eq!(state, restored);
}

#[test]
fn runtime_status_json_uses_u64_counts() {
    let status = RuntimeStatusSnapshot {
        execution_session_id: "sess-0001".to_owned(),
        execution_status: SessionStatus::Active,
        agent_session_id: "agent-0001".to_owned(),
        agent_kind: "codex".to_owned(),
        agent_status: SessionStatus::Active,
        runner_mode: "projected".to_owned(),
        policy_version: 2,
        deny_rule_count: 12,
        terminal_count: 3,
        event_count: 18,
        pending_review_count: 4,
    };

    let json = status.to_json();
    let parsed: serde_json::Value = serde_json::from_str(&json).expect("status JSON should parse");

    assert_eq!(parsed["execution_status"], "active");
    assert_eq!(parsed["deny_rule_count"], 12);
    assert_eq!(parsed["terminal_count"], 3);
    assert_eq!(parsed["event_count"], 18);
    assert_eq!(parsed["pending_review_count"], 4);

    let restored = RuntimeStatusSnapshot::from_json(&json).expect("status should deserialize");
    assert_eq!(status, restored);
}

#[test]
fn runtime_metrics_roundtrip_uses_u64() {
    let metrics = UsageMetrics {
        list_count: 1,
        read_count: 2,
        write_count: 3,
        grep_count: 4,
        blocked_count: 5,
        terminal_runs: 6,
    };

    let snapshot = RuntimeMetricsSnapshot {
        list_count: metrics.list_count,
        read_count: metrics.read_count,
        write_count: metrics.write_count,
        grep_count: metrics.grep_count,
        blocked_count: metrics.blocked_count,
        terminal_runs: metrics.terminal_runs,
        audit_event_count: 7,
    };

    let parsed: serde_json::Value =
        serde_json::from_str(&snapshot.to_json()).expect("metrics snapshot should parse");
    assert_eq!(parsed["list_count"], 1);
    assert_eq!(parsed["audit_event_count"], 7);

    let restored = RuntimeMetricsSnapshot::from_json(&snapshot.to_json())
        .expect("metrics snapshot should deserialize");
    assert_eq!(snapshot, restored);
}

#[test]
fn host_request_roundtrip_json_contract() {
    let request = HostRequest::ReviewStageWrite {
        target: "src/main.rs".to_owned(),
        proposed_text: "fn main() {}\n".to_owned(),
        session_id: Some("sess-00000001".to_owned()),
        agent_session_id: Some("agent-00000001".to_owned()),
    };

    let json = request.to_json();
    let restored = HostRequest::from_json(&json).expect("host request should deserialize");

    assert_eq!(request, restored);
}

#[test]
fn metrics_and_audit_host_request_roundtrip_json_contract() {
    let metrics = HostRequest::MetricsShow;
    let audit = HostRequest::AuditList {
        limit: 10,
        allowed: Some(false),
    };

    assert_eq!(metrics, HostRequest::from_json(&metrics.to_json()).unwrap());
    assert_eq!(audit, HostRequest::from_json(&audit.to_json()).unwrap());
}

#[test]
fn host_response_envelope_roundtrip_json_contract() {
    let response =
        HostResponseEnvelope::success(HostResponse::PolicyChangeSnapshot(PolicyChangeSnapshot {
            result: PolicyChangeResult {
                changed: true,
                rotated: true,
                execution_session_id: "sess-00000002".to_owned(),
                agent_session_id: "agent-00000002".to_owned(),
                policy_version: 3,
            },
            snapshot: HostSnapshot {
                policy_state: PolicyState {
                    deny_globs: vec!["secrets/**".to_owned()],
                    version: 3,
                },
                execution_session: ExecutionSession {
                    session_id: "sess-00000002".to_owned(),
                    policy_version: 3,
                    created_at: "1970-01-01T00:00:03Z".to_owned(),
                    status: SessionStatus::Active,
                    rotated_from: Some("sess-00000001".to_owned()),
                },
                agent_session: AgentSession {
                    agent_session_id: "agent-00000002".to_owned(),
                    execution_session_id: "sess-00000002".to_owned(),
                    agent_kind: "codex".to_owned(),
                    created_at: "1970-01-01T00:00:04Z".to_owned(),
                    status: SessionStatus::Active,
                    rotated_from: Some("agent-00000001".to_owned()),
                },
                review_count: 0,
                pending_review_count: 0,
            },
        }));

    let json = response.to_json();
    let restored =
        HostResponseEnvelope::from_json(&json).expect("host response envelope should deserialize");

    assert_eq!(response, restored);
}

#[test]
fn metrics_and_audit_host_response_roundtrip_json_contract() {
    let metrics = HostResponse::RuntimeMetricsSnapshot(RuntimeMetricsSnapshot {
        list_count: 1,
        read_count: 2,
        write_count: 3,
        grep_count: 4,
        blocked_count: 5,
        terminal_runs: 6,
        audit_event_count: 7,
    });
    let audit = HostResponse::AuditList(vec![AuditEvent {
        timestamp: "2026-03-07T00:00:00Z".to_owned(),
        session_id: "sess-1".to_owned(),
        action: "read".to_owned(),
        target: "C:/repo/file.txt".to_owned(),
        allowed: true,
        detail: "bytes=10".to_owned(),
    }]);

    assert_eq!(metrics, HostResponse::from_json(&metrics.to_json()).unwrap());
    assert_eq!(audit, HostResponse::from_json(&audit.to_json()).unwrap());
}

#[test]
fn agent_session_snapshot_roundtrip_json_contract() {
    let response =
        HostResponseEnvelope::success(HostResponse::AgentSessionSnapshot(AgentSessionSnapshot {
            session: AgentSession {
                agent_session_id: "agent-00000002".to_owned(),
                execution_session_id: "sess-00000001".to_owned(),
                agent_kind: "claude".to_owned(),
                created_at: "1970-01-01T00:00:02Z".to_owned(),
                status: SessionStatus::Active,
                rotated_from: Some("agent-00000001".to_owned()),
            },
            snapshot: HostSnapshot {
                policy_state: PolicyState {
                    deny_globs: Vec::new(),
                    version: 1,
                },
                execution_session: ExecutionSession {
                    session_id: "sess-00000001".to_owned(),
                    policy_version: 1,
                    created_at: "1970-01-01T00:00:01Z".to_owned(),
                    status: SessionStatus::Active,
                    rotated_from: None,
                },
                agent_session: AgentSession {
                    agent_session_id: "agent-00000002".to_owned(),
                    execution_session_id: "sess-00000001".to_owned(),
                    agent_kind: "claude".to_owned(),
                    created_at: "1970-01-01T00:00:02Z".to_owned(),
                    status: SessionStatus::Active,
                    rotated_from: Some("agent-00000001".to_owned()),
                },
                review_count: 0,
                pending_review_count: 0,
            },
        }));

    let json = response.to_json();
    let restored =
        HostResponseEnvelope::from_json(&json).expect("agent session snapshot should deserialize");

    assert_eq!(response, restored);
}

#[test]
fn host_snapshot_roundtrip_json_contract() {
    let snapshot = HostSnapshot {
        policy_state: PolicyState {
            deny_globs: vec!["secrets/**".to_owned()],
            version: 2,
        },
        execution_session: ExecutionSession {
            session_id: "sess-00000001".to_owned(),
            policy_version: 2,
            created_at: "1970-01-01T00:00:01Z".to_owned(),
            status: SessionStatus::Active,
            rotated_from: None,
        },
        agent_session: AgentSession {
            agent_session_id: "agent-00000001".to_owned(),
            execution_session_id: "sess-00000001".to_owned(),
            agent_kind: "codex".to_owned(),
            created_at: "1970-01-01T00:00:02Z".to_owned(),
            status: SessionStatus::Active,
            rotated_from: None,
        },
        review_count: 4,
        pending_review_count: 1,
    };

    let json = snapshot.to_json();
    let restored = HostSnapshot::from_json(&json).expect("host snapshot should deserialize");

    assert_eq!(snapshot, restored);
}

#[test]
fn host_error_envelope_serializes_machine_readable_error() {
    let response =
        HostResponseEnvelope::error("blocked_by_policy", "Blocked by policy: secrets/key.txt");
    let parsed: serde_json::Value =
        serde_json::from_str(&response.to_json()).expect("host error envelope should parse");

    assert_eq!(parsed["ok"], false);
    assert_eq!(parsed["error"]["code"], "blocked_by_policy");
    assert_eq!(
        parsed["error"]["message"],
        "Blocked by policy: secrets/key.txt"
    );
}

#[test]
fn host_response_roundtrip_review_proposal_contract() {
    let response = HostResponse::ReviewProposal(WriteProposal {
        proposal_id: "rev-00000001".to_owned(),
        target: "src/main.rs".to_owned(),
        session_id: "sess-00000001".to_owned(),
        agent_session_id: "agent-00000001".to_owned(),
        created_at: "1970-01-01T00:00:01Z".to_owned(),
        updated_at: "1970-01-01T00:00:02Z".to_owned(),
        status: ProposalStatus::Pending,
        base_sha256: Some("abc123".to_owned()),
        base_text: Some("old\n".to_owned()),
        proposed_text: "new\n".to_owned(),
    });

    let json = response.to_json();
    let restored = HostResponse::from_json(&json).expect("host response should deserialize");

    assert_eq!(response, restored);
}

#[test]
fn rendered_proposal_roundtrip_json_contract() {
    let rendered = RenderedProposal {
        proposal_id: "rev-00000001".to_owned(),
        content: "proposal_id=rev-00000001\n(no diff)".to_owned(),
    };

    let json = rendered.to_json();
    let restored =
        RenderedProposal::from_json(&json).expect("rendered proposal should deserialize");

    assert_eq!(rendered, restored);
}

#[test]
fn workspace_host_request_roundtrip_json_contract() {
    let request = HostRequest::WorkspaceGrep {
        pattern: "todo".to_owned(),
        target: "notes".to_owned(),
    };

    let json = request.to_json();
    let restored = HostRequest::from_json(&json).expect("workspace request should deserialize");

    assert_eq!(request, restored);
}

#[test]
fn workspace_host_response_roundtrip_json_contract() {
    let response = HostResponse::WorkspaceTree(vec![WorkspaceCatalogEntry {
        path: "notes".to_owned(),
        name: "notes".to_owned(),
        is_dir: true,
        display_name: "notes/".to_owned(),
        display_path: "notes/".to_owned(),
    }]);

    let json = response.to_json();
    let restored = HostResponse::from_json(&json).expect("workspace response should deserialize");

    assert_eq!(response, restored);
}

#[test]
fn workspace_search_match_roundtrip_json_contract() {
    let matches = vec![WorkspaceSearchMatch {
        path: "notes/todo.txt".to_owned(),
        line_number: 1,
        line_text: "visible plan".to_owned(),
    }];

    let response = HostResponse::WorkspaceGrep(matches.clone());
    let json = response.to_json();
    let restored =
        HostResponse::from_json(&json).expect("workspace grep response should deserialize");

    assert_eq!(response, restored);
}

#[test]
fn workspace_index_request_roundtrip_json_contract() {
    let request = HostRequest::WorkspaceIndexRefresh;

    let json = request.to_json();
    let restored =
        HostRequest::from_json(&json).expect("workspace index request should deserialize");

    assert_eq!(request, restored);
}

#[test]
fn workspace_index_snapshot_roundtrip_json_contract() {
    let snapshot = WorkspaceIndexSnapshot {
        policy_version: 3,
        entries: vec![WorkspaceIndexEntry {
            path: "notes/todo.txt".to_owned(),
            is_dir: false,
            size: 12,
            modified_ns: 42,
        }],
    };

    let response = HostResponse::WorkspaceIndexSnapshot(snapshot.clone());
    let json = response.to_json();
    let restored =
        HostResponse::from_json(&json).expect("workspace index response should deserialize");

    assert_eq!(response, restored);
}
