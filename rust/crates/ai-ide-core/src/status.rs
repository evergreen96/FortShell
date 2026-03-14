use ai_ide_protocol::{RuntimeMetricsSnapshot, RuntimeStatusSnapshot, SessionStatus, UsageMetrics};

pub struct RuntimeStatusService;

pub struct StatusSnapshotInput {
    pub execution_session_id: String,
    pub execution_status: SessionStatus,
    pub agent_session_id: String,
    pub agent_kind: String,
    pub agent_status: SessionStatus,
    pub runner_mode: String,
    pub policy_version: u64,
    pub deny_rule_count: u64,
    pub terminal_count: u64,
    pub event_count: u64,
    pub pending_review_count: u64,
}

impl RuntimeStatusService {
    pub fn build_status_snapshot(input: StatusSnapshotInput) -> RuntimeStatusSnapshot {
        RuntimeStatusSnapshot {
            execution_session_id: input.execution_session_id,
            execution_status: input.execution_status,
            agent_session_id: input.agent_session_id,
            agent_kind: input.agent_kind,
            agent_status: input.agent_status,
            runner_mode: input.runner_mode,
            policy_version: input.policy_version,
            deny_rule_count: input.deny_rule_count,
            terminal_count: input.terminal_count,
            event_count: input.event_count,
            pending_review_count: input.pending_review_count,
        }
    }

    pub fn build_metrics_snapshot(
        metrics: &UsageMetrics,
        audit_event_count: u64,
    ) -> RuntimeMetricsSnapshot {
        RuntimeMetricsSnapshot {
            list_count: metrics.list_count,
            read_count: metrics.read_count,
            write_count: metrics.write_count,
            grep_count: metrics.grep_count,
            blocked_count: metrics.blocked_count,
            terminal_runs: metrics.terminal_runs,
            audit_event_count,
        }
    }

    pub fn status_text(snapshot: &RuntimeStatusSnapshot) -> String {
        format!(
            "execution_session={} agent_session={} policy_version={} deny_rules={} terminals={} events={}",
            snapshot.execution_session_id,
            snapshot.agent_session_id,
            snapshot.policy_version,
            snapshot.deny_rule_count,
            snapshot.terminal_count,
            snapshot.event_count,
        )
    }

    pub fn session_text(snapshot: &RuntimeStatusSnapshot) -> String {
        format!(
            "execution_session_id={} agent_session_id={} policy_version={} execution_status={}",
            snapshot.execution_session_id,
            snapshot.agent_session_id,
            snapshot.policy_version,
            snapshot.execution_status,
        )
    }

    pub fn metrics_text(snapshot: &RuntimeMetricsSnapshot) -> String {
        format!(
            "list={} read={} write={} grep={} blocked={} terminal_runs={} audit_events={}",
            snapshot.list_count,
            snapshot.read_count,
            snapshot.write_count,
            snapshot.grep_count,
            snapshot.blocked_count,
            snapshot.terminal_runs,
            snapshot.audit_event_count,
        )
    }
}
