use std::fmt;

use serde::{Deserialize, Serialize};

fn to_json<T: Serialize>(value: &T) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "\"serialization_error\"".to_owned())
}

fn from_json<T: for<'de> Deserialize<'de>>(text: &str) -> Option<T> {
    serde_json::from_str(text).ok()
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PolicyState {
    pub deny_globs: Vec<String>,
    pub version: u64,
}

impl PolicyState {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

impl Default for PolicyState {
    fn default() -> Self {
        Self {
            deny_globs: Vec::new(),
            version: 1,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SessionStatus {
    Active,
    Stale,
}

impl SessionStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            SessionStatus::Active => "active",
            SessionStatus::Stale => "stale",
        }
    }
}

impl fmt::Display for SessionStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ProposalStatus {
    Pending,
    Applied,
    Rejected,
    Conflict,
}

impl ProposalStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            ProposalStatus::Pending => "pending",
            ProposalStatus::Applied => "applied",
            ProposalStatus::Rejected => "rejected",
            ProposalStatus::Conflict => "conflict",
        }
    }
}

impl fmt::Display for ProposalStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct WriteProposal {
    pub proposal_id: String,
    pub target: String,
    pub session_id: String,
    pub agent_session_id: String,
    pub created_at: String,
    pub updated_at: String,
    pub status: ProposalStatus,
    pub base_sha256: Option<String>,
    pub base_text: Option<String>,
    pub proposed_text: String,
}

impl WriteProposal {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionSession {
    pub session_id: String,
    pub policy_version: u64,
    pub created_at: String,
    pub status: SessionStatus,
    pub rotated_from: Option<String>,
}

impl ExecutionSession {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct AgentSession {
    pub agent_session_id: String,
    pub execution_session_id: String,
    pub agent_kind: String,
    pub created_at: String,
    pub status: SessionStatus,
    pub rotated_from: Option<String>,
}

impl AgentSession {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuditEvent {
    pub timestamp: String,
    pub session_id: String,
    pub action: String,
    pub target: String,
    pub allowed: bool,
    pub detail: String,
}

impl AuditEvent {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct UsageMetrics {
    pub list_count: u64,
    pub read_count: u64,
    pub write_count: u64,
    pub grep_count: u64,
    pub blocked_count: u64,
    pub terminal_runs: u64,
}

impl UsageMetrics {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RuntimeStatusSnapshot {
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

impl RuntimeStatusSnapshot {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RuntimeMetricsSnapshot {
    pub list_count: u64,
    pub read_count: u64,
    pub write_count: u64,
    pub grep_count: u64,
    pub blocked_count: u64,
    pub terminal_runs: u64,
    pub audit_event_count: u64,
}

impl RuntimeMetricsSnapshot {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PolicyChangeResult {
    pub changed: bool,
    pub rotated: bool,
    pub execution_session_id: String,
    pub agent_session_id: String,
    pub policy_version: u64,
}

impl PolicyChangeResult {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PolicyChangeSnapshot {
    pub result: PolicyChangeResult,
    pub snapshot: HostSnapshot,
}

impl PolicyChangeSnapshot {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct AgentSessionSnapshot {
    pub session: AgentSession,
    pub snapshot: HostSnapshot,
}

impl AgentSessionSnapshot {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct HostSnapshot {
    pub policy_state: PolicyState,
    pub execution_session: ExecutionSession,
    pub agent_session: AgentSession,
    pub review_count: u64,
    pub pending_review_count: u64,
}

impl HostSnapshot {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RenderedProposal {
    pub proposal_id: String,
    pub content: String,
}

impl RenderedProposal {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceCatalogEntry {
    pub path: String,
    pub name: String,
    pub is_dir: bool,
    pub display_name: String,
    pub display_path: String,
}

impl WorkspaceCatalogEntry {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceSearchMatch {
    pub path: String,
    pub line_number: u64,
    pub line_text: String,
}

impl WorkspaceSearchMatch {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceIndexEntry {
    pub path: String,
    pub is_dir: bool,
    pub size: u64,
    pub modified_ns: u64,
}

impl WorkspaceIndexEntry {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceIndexSnapshot {
    pub policy_version: u64,
    pub entries: Vec<WorkspaceIndexEntry>,
}

impl WorkspaceIndexSnapshot {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct HostError {
    pub code: String,
    pub message: String,
}

impl HostError {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum HostRequest {
    Snapshot,
    RotateAgentSession {
        agent_kind: Option<String>,
    },
    PolicyAddDenyRule {
        rule: String,
    },
    PolicyRemoveDenyRule {
        rule: String,
    },
    PolicySync,
    MetricsShow,
    AuditList {
        limit: usize,
        allowed: Option<bool>,
    },
    WorkspaceList {
        target: String,
    },
    WorkspaceTree {
        target: String,
    },
    WorkspaceGrep {
        pattern: String,
        target: String,
    },
    WorkspaceIndexShow,
    WorkspaceIndexRefresh,
    ReviewList {
        status: Option<ProposalStatus>,
        limit: usize,
    },
    ReviewGet {
        proposal_id: String,
    },
    ReviewRender {
        proposal_id: String,
    },
    ReviewStageWrite {
        target: String,
        proposed_text: String,
        session_id: Option<String>,
        agent_session_id: Option<String>,
    },
    ReviewApply {
        proposal_id: String,
    },
    ReviewReject {
        proposal_id: String,
    },
}

impl HostRequest {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", content = "data", rename_all = "snake_case")]
pub enum HostResponse {
    Snapshot(HostSnapshot),
    AgentSessionSnapshot(AgentSessionSnapshot),
    PolicyChangeSnapshot(PolicyChangeSnapshot),
    RuntimeMetricsSnapshot(RuntimeMetricsSnapshot),
    AuditList(Vec<AuditEvent>),
    WorkspaceList(Vec<WorkspaceCatalogEntry>),
    WorkspaceTree(Vec<WorkspaceCatalogEntry>),
    WorkspaceGrep(Vec<WorkspaceSearchMatch>),
    WorkspaceIndexSnapshot(WorkspaceIndexSnapshot),
    ReviewProposal(WriteProposal),
    ReviewRender(RenderedProposal),
    ReviewList(Vec<WriteProposal>),
}

impl HostResponse {
    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct HostResponseEnvelope {
    pub ok: bool,
    pub response: Option<HostResponse>,
    pub error: Option<HostError>,
}

impl HostResponseEnvelope {
    pub fn success(response: HostResponse) -> Self {
        Self {
            ok: true,
            response: Some(response),
            error: None,
        }
    }

    pub fn error(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            ok: false,
            response: None,
            error: Some(HostError {
                code: code.into(),
                message: message.into(),
            }),
        }
    }

    pub fn to_json(&self) -> String {
        to_json(self)
    }

    pub fn from_json(text: &str) -> Option<Self> {
        from_json(text)
    }
}
