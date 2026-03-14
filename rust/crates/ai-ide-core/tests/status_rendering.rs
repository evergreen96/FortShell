use ai_ide_core::status::{RuntimeStatusService, StatusSnapshotInput};
use ai_ide_protocol::{SessionStatus, UsageMetrics};

#[test]
fn status_and_metrics_rendering_matches_control_plane_contract() {
    let status = RuntimeStatusService::build_status_snapshot(StatusSnapshotInput {
        execution_session_id: "sess-00000002".to_owned(),
        execution_status: SessionStatus::Active,
        agent_session_id: "agent-00000003".to_owned(),
        agent_kind: "codex".to_owned(),
        agent_status: SessionStatus::Active,
        runner_mode: "projected".to_owned(),
        policy_version: 3,
        deny_rule_count: 2,
        terminal_count: 1,
        event_count: 8,
        pending_review_count: 4,
    });
    let metrics = RuntimeStatusService::build_metrics_snapshot(
        &UsageMetrics {
            list_count: 1,
            read_count: 2,
            write_count: 3,
            grep_count: 4,
            blocked_count: 5,
            terminal_runs: 6,
        },
        7,
    );

    assert_eq!(
        RuntimeStatusService::status_text(&status),
        "execution_session=sess-00000002 agent_session=agent-00000003 policy_version=3 deny_rules=2 terminals=1 events=8"
    );
    assert_eq!(
        RuntimeStatusService::session_text(&status),
        "execution_session_id=sess-00000002 agent_session_id=agent-00000003 policy_version=3 execution_status=active"
    );
    assert_eq!(
        RuntimeStatusService::metrics_text(&metrics),
        "list=1 read=2 write=3 grep=4 blocked=5 terminal_runs=6 audit_events=7"
    );
}
