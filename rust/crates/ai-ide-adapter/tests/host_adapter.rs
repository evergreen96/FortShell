use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use ai_ide_adapter::{HostAdapter, HostConfig};
use ai_ide_protocol::{HostRequest, HostResponse, HostResponseEnvelope, ProposalStatus};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path =
            std::env::temp_dir().join(format!("ai-ide-adapter-{name}-{}-{id}", std::process::id()));
        std::fs::create_dir_all(&path).unwrap();
        Self { path }
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TestDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

#[test]
fn snapshot_and_policy_mutation_round_trip_through_json() {
    let root = TestDir::new("policy");
    let policy_store = root.path().join(".runtime").join("policy.json");
    let review_store = root.path().join(".runtime").join("reviews.json");
    let mut host = HostAdapter::new(
        HostConfig::new(root.path(), "codex")
            .with_policy_store_path(&policy_store)
            .with_review_store_path(&review_store),
    )
    .unwrap();

    let initial = HostResponseEnvelope::from_json(
        &host.handle_request_json(&HostRequest::Snapshot.to_json()),
    )
    .unwrap();
    let change = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::PolicyAddDenyRule {
                rule: "secrets/**".to_owned(),
            }
            .to_json(),
        ),
    )
    .unwrap();
    let updated = HostResponseEnvelope::from_json(
        &host.handle_request_json(&HostRequest::Snapshot.to_json()),
    )
    .unwrap();

    assert!(initial.ok);
    assert!(change.ok);
    assert!(updated.ok);

    match change
        .response
        .expect("policy change response should exist")
    {
        HostResponse::PolicyChangeSnapshot(payload) => {
            assert!(payload.result.changed);
            assert!(payload.result.rotated);
            assert_eq!(payload.result.policy_version, 2);
            assert_eq!(payload.snapshot.policy_state.deny_globs, vec!["secrets/**"]);
            assert_eq!(payload.snapshot.policy_state.version, 2);
        }
        other => panic!("unexpected response: {other:?}"),
    }

    match updated.response.expect("snapshot response should exist") {
        HostResponse::Snapshot(snapshot) => {
            assert_eq!(snapshot.policy_state.deny_globs, vec!["secrets/**"]);
            assert_eq!(snapshot.policy_state.version, 2);
        }
        other => panic!("unexpected response: {other:?}"),
    }
}

#[test]
fn metrics_and_audit_queries_round_trip_through_json() {
    let root = TestDir::new("broker-state");
    let broker_store = root.path().join(".runtime").join("broker-state.json");
    std::fs::create_dir_all(broker_store.parent().unwrap()).unwrap();
    std::fs::write(
        &broker_store,
        concat!(
            "{\n",
            "  \"metrics\": {\n",
            "    \"list_count\": 1,\n",
            "    \"read_count\": 2,\n",
            "    \"write_count\": 3,\n",
            "    \"grep_count\": 4,\n",
            "    \"blocked_count\": 5,\n",
            "    \"terminal_runs\": 6\n",
            "  },\n",
            "  \"audit_log\": [\n",
            "    {\n",
            "      \"timestamp\": \"2026-03-07T00:00:00Z\",\n",
            "      \"session_id\": \"sess-1\",\n",
            "      \"action\": \"read\",\n",
            "      \"target\": \"C:/repo/file.txt\",\n",
            "      \"allowed\": true,\n",
            "      \"detail\": \"bytes=10\"\n",
            "    },\n",
            "    {\n",
            "      \"timestamp\": \"2026-03-07T00:00:01Z\",\n",
            "      \"session_id\": \"sess-1\",\n",
            "      \"action\": \"read\",\n",
            "      \"target\": \"C:/repo/secret.txt\",\n",
            "      \"allowed\": false,\n",
            "      \"detail\": \"denied by policy\"\n",
            "    }\n",
            "  ]\n",
            "}\n"
        ),
    )
    .unwrap();
    let mut host = HostAdapter::new(
        HostConfig::new(root.path(), "codex").with_broker_store_path(&broker_store),
    )
    .unwrap();

    let metrics = HostResponseEnvelope::from_json(
        &host.handle_request_json(&HostRequest::MetricsShow.to_json()),
    )
    .unwrap();
    let audit = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::AuditList {
                limit: 10,
                allowed: Some(false),
            }
            .to_json(),
        ),
    )
    .unwrap();

    assert!(metrics.ok);
    match metrics.response.expect("metrics response should exist") {
        HostResponse::RuntimeMetricsSnapshot(snapshot) => {
            assert_eq!(snapshot.read_count, 2);
            assert_eq!(snapshot.audit_event_count, 2);
        }
        other => panic!("unexpected response: {other:?}"),
    }

    assert!(audit.ok);
    match audit.response.expect("audit response should exist") {
        HostResponse::AuditList(events) => {
            assert_eq!(events.len(), 1);
            assert_eq!(events[0].target, "C:/repo/secret.txt");
            assert!(!events[0].allowed);
        }
        other => panic!("unexpected response: {other:?}"),
    }
}

#[test]
fn review_stage_apply_flow_uses_host_session_state() {
    let root = TestDir::new("review");
    std::fs::create_dir_all(root.path().join("src")).unwrap();
    std::fs::write(root.path().join("src").join("app.py"), "print('old')\n").unwrap();
    let mut host = HostAdapter::new(HostConfig::new(root.path(), "codex")).unwrap();

    let staged = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::ReviewStageWrite {
                target: "src/app.py".to_owned(),
                proposed_text: "print('new')\n".to_owned(),
                session_id: None,
                agent_session_id: None,
            }
            .to_json(),
        ),
    )
    .unwrap();

    let proposal_id = match staged.response.expect("stage response should exist") {
        HostResponse::ReviewProposal(proposal) => {
            assert_eq!(proposal.session_id, "sess-00000001");
            assert_eq!(proposal.agent_session_id, "agent-00000001");
            proposal.proposal_id
        }
        other => panic!("unexpected response: {other:?}"),
    };

    let applied = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::ReviewApply {
                proposal_id: proposal_id.clone(),
            }
            .to_json(),
        ),
    )
    .unwrap();

    assert!(applied.ok);
    match applied.response.expect("apply response should exist") {
        HostResponse::ReviewProposal(proposal) => {
            assert_eq!(proposal.proposal_id, proposal_id);
            assert_eq!(proposal.status, ProposalStatus::Applied);
        }
        other => panic!("unexpected response: {other:?}"),
    }
    assert_eq!(
        std::fs::read_to_string(root.path().join("src").join("app.py")).unwrap(),
        "print('new')\n"
    );
}

#[test]
fn review_stage_can_use_explicit_session_ids_from_request() {
    let root = TestDir::new("review-explicit-session");
    let mut host = HostAdapter::new(HostConfig::new(root.path(), "codex")).unwrap();

    let staged = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::ReviewStageWrite {
                target: "src/app.py".to_owned(),
                proposed_text: "print('new')\n".to_owned(),
                session_id: Some("sess-python".to_owned()),
                agent_session_id: Some("agent-python".to_owned()),
            }
            .to_json(),
        ),
    )
    .unwrap();

    assert!(staged.ok);
    match staged.response.expect("stage response should exist") {
        HostResponse::ReviewProposal(proposal) => {
            assert_eq!(proposal.session_id, "sess-python");
            assert_eq!(proposal.agent_session_id, "agent-python");
        }
        other => panic!("unexpected response: {other:?}"),
    }
}

#[test]
fn review_render_returns_machine_readable_rendered_content() {
    let root = TestDir::new("review-render");
    std::fs::create_dir_all(root.path().join("src")).unwrap();
    std::fs::write(root.path().join("src").join("app.py"), "print('old')\n").unwrap();
    let mut host = HostAdapter::new(HostConfig::new(root.path(), "codex")).unwrap();
    let staged = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::ReviewStageWrite {
                target: "src/app.py".to_owned(),
                proposed_text: "print('new')\n".to_owned(),
                session_id: None,
                agent_session_id: None,
            }
            .to_json(),
        ),
    )
    .unwrap();
    let proposal_id = match staged.response.expect("stage response should exist") {
        HostResponse::ReviewProposal(proposal) => proposal.proposal_id,
        other => panic!("unexpected response: {other:?}"),
    };

    let rendered = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::ReviewRender {
                proposal_id: proposal_id.clone(),
            }
            .to_json(),
        ),
    )
    .unwrap();

    assert!(rendered.ok);
    match rendered.response.expect("render response should exist") {
        HostResponse::ReviewRender(payload) => {
            assert_eq!(payload.proposal_id, proposal_id);
            assert!(payload.content.contains("--- a/src/app.py"));
            assert!(payload.content.contains("+++ b/src/app.py"));
        }
        other => panic!("unexpected response: {other:?}"),
    }
}

#[test]
fn rotate_agent_session_and_review_list_are_machine_readable() {
    let root = TestDir::new("rotate");
    let mut host = HostAdapter::new(HostConfig::new(root.path(), "codex")).unwrap();

    let rotate = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::RotateAgentSession {
                agent_kind: Some("claude".to_owned()),
            }
            .to_json(),
        ),
    )
    .unwrap();
    let list = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::ReviewList {
                status: Some(ProposalStatus::Pending),
                limit: 10,
            }
            .to_json(),
        ),
    )
    .unwrap();

    assert!(rotate.ok);
    match rotate.response.expect("rotate response should exist") {
        HostResponse::AgentSessionSnapshot(payload) => {
            assert_eq!(payload.session.agent_kind, "claude");
            assert_eq!(payload.session.execution_session_id, "sess-00000001");
            assert_eq!(
                payload.snapshot.agent_session.agent_session_id,
                payload.session.agent_session_id
            );
        }
        other => panic!("unexpected response: {other:?}"),
    }

    assert!(list.ok);
    match list.response.expect("list response should exist") {
        HostResponse::ReviewList(proposals) => assert!(proposals.is_empty()),
        other => panic!("unexpected response: {other:?}"),
    }
}

#[test]
fn blocked_review_stage_returns_machine_readable_error() {
    let root = TestDir::new("blocked");
    let mut host = HostAdapter::new(HostConfig::new(root.path(), "codex")).unwrap();
    let _ = host.handle_request_json(
        &HostRequest::PolicyAddDenyRule {
            rule: "secrets/**".to_owned(),
        }
        .to_json(),
    );

    let response = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::ReviewStageWrite {
                target: "secrets/token.txt".to_owned(),
                proposed_text: "secret\n".to_owned(),
                session_id: None,
                agent_session_id: None,
            }
            .to_json(),
        ),
    )
    .unwrap();

    assert!(!response.ok);
    let error = response.error.expect("error payload should exist");
    assert_eq!(error.code, "review_blocked_by_policy");
    assert_eq!(error.message, "Blocked by policy: secrets/token.txt");
}

#[test]
fn invalid_json_request_returns_invalid_request_error() {
    let root = TestDir::new("invalid");
    let mut host = HostAdapter::new(HostConfig::new(root.path(), "codex")).unwrap();

    let response =
        HostResponseEnvelope::from_json(&host.handle_request_json("{not-json}")).unwrap();

    assert!(!response.ok);
    let error = response.error.expect("error payload should exist");
    assert_eq!(error.code, "invalid_request");
}

#[test]
fn workspace_queries_round_trip_through_json() {
    let root = TestDir::new("workspace");
    let workspace_index_store = root
        .path()
        .join(".runtime")
        .join("workspace")
        .join("index.json");
    std::fs::create_dir_all(root.path().join("notes").join("nested")).unwrap();
    std::fs::create_dir_all(root.path().join("secrets")).unwrap();
    std::fs::write(root.path().join("notes").join("todo.txt"), "visible plan\n").unwrap();
    std::fs::write(
        root.path().join("notes").join("nested").join("deep.txt"),
        "deep plan\n",
    )
    .unwrap();
    std::fs::write(
        root.path().join("secrets").join("token.txt"),
        "hidden plan\n",
    )
    .unwrap();
    let mut host = HostAdapter::new(
        HostConfig::new(root.path(), "codex")
            .with_workspace_index_store_path(&workspace_index_store),
    )
    .unwrap();
    let _ = host.handle_request_json(
        &HostRequest::PolicyAddDenyRule {
            rule: "secrets/**".to_owned(),
        }
        .to_json(),
    );

    let listing = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::WorkspaceList {
                target: ".".to_owned(),
            }
            .to_json(),
        ),
    )
    .unwrap();
    let grep = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::WorkspaceGrep {
                pattern: "plan".to_owned(),
                target: ".".to_owned(),
            }
            .to_json(),
        ),
    )
    .unwrap();

    assert!(listing.ok);
    match listing
        .response
        .expect("workspace list response should exist")
    {
        HostResponse::WorkspaceList(entries) => {
            assert_eq!(entries.len(), 1);
            assert_eq!(entries[0].path, "notes");
            assert_eq!(entries[0].display_path, "notes/");
        }
        other => panic!("unexpected response: {other:?}"),
    }

    assert!(grep.ok);
    match grep.response.expect("workspace grep response should exist") {
        HostResponse::WorkspaceGrep(matches) => {
            assert_eq!(matches.len(), 2);
            assert_eq!(matches[0].path, "notes/nested/deep.txt");
            assert_eq!(matches[1].path, "notes/todo.txt");
        }
        other => panic!("unexpected response: {other:?}"),
    }

    let initial_index = HostResponseEnvelope::from_json(
        &host.handle_request_json(&HostRequest::WorkspaceIndexShow.to_json()),
    )
    .unwrap();
    let refreshed_index = HostResponseEnvelope::from_json(
        &host.handle_request_json(&HostRequest::WorkspaceIndexRefresh.to_json()),
    )
    .unwrap();

    assert!(initial_index.ok);
    match initial_index
        .response
        .expect("workspace index show response should exist")
    {
        HostResponse::WorkspaceIndexSnapshot(snapshot) => {
            assert_eq!(snapshot.policy_version, 0);
            assert!(snapshot.entries.is_empty());
        }
        other => panic!("unexpected response: {other:?}"),
    }

    assert!(refreshed_index.ok);
    match refreshed_index
        .response
        .expect("workspace index refresh response should exist")
    {
        HostResponse::WorkspaceIndexSnapshot(snapshot) => {
            assert_eq!(snapshot.policy_version, 2);
            assert_eq!(snapshot.entries.len(), 4);
            assert_eq!(snapshot.entries[0].path, "notes");
            assert_eq!(snapshot.entries[1].path, "notes/nested");
            assert_eq!(snapshot.entries[2].path, "notes/nested/deep.txt");
            assert_eq!(snapshot.entries[3].path, "notes/todo.txt");
        }
        other => panic!("unexpected response: {other:?}"),
    }
}

#[test]
fn alias_guard_errors_are_machine_readable() {
    let root = TestDir::new("alias-errors");
    std::fs::create_dir_all(root.path().join("safe")).unwrap();
    std::fs::create_dir_all(root.path().join("secrets")).unwrap();
    std::fs::write(root.path().join("secrets").join("token.txt"), "secret\n").unwrap();
    match std::fs::hard_link(
        root.path().join("secrets").join("token.txt"),
        root.path().join("safe").join("token-alias.txt"),
    ) {
        Ok(()) => {}
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::PermissionDenied | std::io::ErrorKind::Unsupported
            ) =>
        {
            return;
        }
        Err(error) => panic!("failed to create hardlink: {error}"),
    }
    let mut host = HostAdapter::new(HostConfig::new(root.path(), "codex")).unwrap();

    let review = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::ReviewStageWrite {
                target: "safe/token-alias.txt".to_owned(),
                proposed_text: "changed\n".to_owned(),
                session_id: None,
                agent_session_id: None,
            }
            .to_json(),
        ),
    )
    .unwrap();
    let workspace = HostResponseEnvelope::from_json(
        &host.handle_request_json(
            &HostRequest::WorkspaceTree {
                target: "safe/token-alias.txt".to_owned(),
            }
            .to_json(),
        ),
    )
    .unwrap();

    assert!(!review.ok);
    assert_eq!(
        review
            .error
            .expect("review error payload should exist")
            .code,
        "review_hardlink_path"
    );
    assert!(!workspace.ok);
    assert_eq!(
        workspace
            .error
            .expect("workspace error payload should exist")
            .code,
        "workspace_hardlink_path"
    );
}
