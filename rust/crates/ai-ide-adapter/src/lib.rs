use std::path::{Path, PathBuf};

use ai_ide_control::{ControlPlane, ControlPlaneError, ReviewControlError, ReviewController};
use ai_ide_core::review::ReviewError;
use ai_ide_core::status::RuntimeStatusService;
use ai_ide_persistence::{BrokerStateStore, StoreError};
use ai_ide_protocol::{
    AgentSessionSnapshot, HostError, HostRequest, HostResponse, HostResponseEnvelope, HostSnapshot,
    PolicyChangeResult, PolicyChangeSnapshot, ProposalStatus, RenderedProposal,
    RuntimeMetricsSnapshot, WriteProposal,
};
use ai_ide_workspace::{
    WorkspaceCatalog, WorkspaceError, WorkspaceIndexError, WorkspaceIndexService,
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HostConfig {
    root: PathBuf,
    default_agent_kind: String,
    policy_store_path: Option<PathBuf>,
    review_store_path: Option<PathBuf>,
    workspace_index_store_path: Option<PathBuf>,
    broker_store_path: Option<PathBuf>,
}

impl HostConfig {
    pub fn new(root: impl AsRef<Path>, default_agent_kind: impl Into<String>) -> Self {
        Self {
            root: root.as_ref().to_path_buf(),
            default_agent_kind: default_agent_kind.into(),
            policy_store_path: None,
            review_store_path: None,
            workspace_index_store_path: None,
            broker_store_path: None,
        }
    }

    pub fn with_policy_store_path(mut self, path: impl AsRef<Path>) -> Self {
        self.policy_store_path = Some(path.as_ref().to_path_buf());
        self
    }

    pub fn with_review_store_path(mut self, path: impl AsRef<Path>) -> Self {
        self.review_store_path = Some(path.as_ref().to_path_buf());
        self
    }

    pub fn with_workspace_index_store_path(mut self, path: impl AsRef<Path>) -> Self {
        self.workspace_index_store_path = Some(path.as_ref().to_path_buf());
        self
    }

    pub fn with_broker_store_path(mut self, path: impl AsRef<Path>) -> Self {
        self.broker_store_path = Some(path.as_ref().to_path_buf());
        self
    }
}

#[derive(Debug)]
pub enum HostAdapterInitError {
    Control(ControlPlaneError),
    Review(ReviewControlError),
}

impl HostAdapterInitError {
    pub fn to_host_error(&self) -> HostError {
        match self {
            HostAdapterInitError::Control(error) => HostError {
                code: "control_init_error".to_owned(),
                message: error.to_string(),
            },
            HostAdapterInitError::Review(error) => review_error_payload(error),
        }
    }
}

impl From<ControlPlaneError> for HostAdapterInitError {
    fn from(value: ControlPlaneError) -> Self {
        Self::Control(value)
    }
}

impl From<ReviewControlError> for HostAdapterInitError {
    fn from(value: ReviewControlError) -> Self {
        Self::Review(value)
    }
}

pub struct HostAdapter {
    control: ControlPlane,
    reviews: ReviewController,
    workspace: WorkspaceCatalog,
    workspace_index: WorkspaceIndexService,
    broker_state: BrokerStateStore,
}

impl HostAdapter {
    pub fn new(config: HostConfig) -> Result<Self, HostAdapterInitError> {
        let control = match &config.policy_store_path {
            Some(path) => ControlPlane::with_store_path(
                &config.root,
                path,
                config.default_agent_kind.clone(),
            )?,
            None => ControlPlane::new(&config.root, config.default_agent_kind.clone())?,
        };
        let reviews = match &config.review_store_path {
            Some(path) => ReviewController::with_store_path(&config.root, path)?,
            None => ReviewController::new(&config.root),
        };
        let workspace = WorkspaceCatalog::new(&config.root);
        let workspace_index = WorkspaceIndexService::with_store_path(
            &config.root,
            config
                .workspace_index_store_path
                .clone()
                .unwrap_or_else(|| config.root.join(".ai-ide").join("workspace-index.json")),
        );
        let broker_state = match &config.broker_store_path {
            Some(path) => BrokerStateStore::new(path),
            None => BrokerStateStore::disabled(),
        };

        Ok(Self {
            control,
            reviews,
            workspace,
            workspace_index,
            broker_state,
        })
    }

    pub fn snapshot(&self) -> HostSnapshot {
        HostSnapshot {
            policy_state: self.control.policy().state().clone(),
            execution_session: self.control.sessions().current_execution_session().clone(),
            agent_session: self.control.sessions().current_agent_session().clone(),
            review_count: self.reviews.proposals().len() as u64,
            pending_review_count: self.reviews.count_proposals(Some(ProposalStatus::Pending))
                as u64,
        }
    }

    pub fn handle_request(&mut self, request: HostRequest) -> HostResponseEnvelope {
        match request {
            HostRequest::Snapshot => {
                HostResponseEnvelope::success(HostResponse::Snapshot(self.snapshot()))
            }
            HostRequest::RotateAgentSession { agent_kind } => {
                let session = self.control.rotate_agent_session(agent_kind.as_deref());
                HostResponseEnvelope::success(HostResponse::AgentSessionSnapshot(
                    AgentSessionSnapshot {
                        session,
                        snapshot: self.snapshot(),
                    },
                ))
            }
            HostRequest::PolicyAddDenyRule { rule } => {
                let result = self.control.add_deny_rule(&rule);
                self.handle_policy_result(result)
            }
            HostRequest::PolicyRemoveDenyRule { rule } => {
                let result = self.control.remove_deny_rule(&rule);
                self.handle_policy_result(result)
            }
            HostRequest::PolicySync => {
                let result = self.control.sync_from_store();
                self.handle_policy_result(result)
            }
            HostRequest::MetricsShow => match self.load_metrics_snapshot() {
                Ok(snapshot) => HostResponseEnvelope::success(
                    HostResponse::RuntimeMetricsSnapshot(snapshot),
                ),
                Err(error) => HostResponseEnvelope::error("broker_state_store_error", error.to_string()),
            },
            HostRequest::AuditList { limit, allowed } => match self.load_audit_events(limit, allowed) {
                Ok(events) => HostResponseEnvelope::success(HostResponse::AuditList(events)),
                Err(error) => HostResponseEnvelope::error("broker_state_store_error", error.to_string()),
            },
            HostRequest::WorkspaceList { target } => match self
                .workspace
                .list_dir(self.control.policy(), &target)
            {
                Ok(entries) => HostResponseEnvelope::success(HostResponse::WorkspaceList(entries)),
                Err(error) => {
                    let payload = workspace_error_payload(&error);
                    HostResponseEnvelope::error(payload.code, payload.message)
                }
            },
            HostRequest::WorkspaceTree { target } => match self
                .workspace
                .tree(self.control.policy(), &target)
            {
                Ok(entries) => HostResponseEnvelope::success(HostResponse::WorkspaceTree(entries)),
                Err(error) => {
                    let payload = workspace_error_payload(&error);
                    HostResponseEnvelope::error(payload.code, payload.message)
                }
            },
            HostRequest::WorkspaceGrep { pattern, target } => {
                match self
                    .workspace
                    .grep(self.control.policy(), &pattern, &target)
                {
                    Ok(matches) => {
                        HostResponseEnvelope::success(HostResponse::WorkspaceGrep(matches))
                    }
                    Err(error) => {
                        let payload = workspace_error_payload(&error);
                        HostResponseEnvelope::error(payload.code, payload.message)
                    }
                }
            }
            HostRequest::WorkspaceIndexShow => match self.workspace_index.load() {
                Ok(snapshot) => {
                    HostResponseEnvelope::success(HostResponse::WorkspaceIndexSnapshot(snapshot))
                }
                Err(error) => {
                    let payload = workspace_index_error_payload(&error);
                    HostResponseEnvelope::error(payload.code, payload.message)
                }
            },
            HostRequest::WorkspaceIndexRefresh => {
                match self.workspace_index.refresh(self.control.policy()) {
                    Ok(snapshot) => HostResponseEnvelope::success(
                        HostResponse::WorkspaceIndexSnapshot(snapshot),
                    ),
                    Err(error) => {
                        let payload = workspace_index_error_payload(&error);
                        HostResponseEnvelope::error(payload.code, payload.message)
                    }
                }
            }
            HostRequest::ReviewList { status, limit } => HostResponseEnvelope::success(
                HostResponse::ReviewList(self.reviews.list_proposals(status, limit)),
            ),
            HostRequest::ReviewGet { proposal_id } => match self.reviews.get_proposal(&proposal_id)
            {
                Some(proposal) => {
                    HostResponseEnvelope::success(HostResponse::ReviewProposal(proposal.clone()))
                }
                None => HostResponseEnvelope::error(
                    "review_unknown_proposal",
                    format!("Unknown review proposal: {proposal_id}"),
                ),
            },
            HostRequest::ReviewRender { proposal_id } => {
                match self.reviews.render_proposal(&proposal_id) {
                    Ok(content) => HostResponseEnvelope::success(HostResponse::ReviewRender(
                        RenderedProposal {
                            proposal_id,
                            content,
                        },
                    )),
                    Err(error) => {
                        let payload = review_error_payload(&error);
                        HostResponseEnvelope::error(payload.code, payload.message)
                    }
                }
            }
            HostRequest::ReviewStageWrite {
                target,
                proposed_text,
                session_id,
                agent_session_id,
            } => Self::handle_review_result(
                self.reviews.stage_write(
                    self.control.policy(),
                    &target,
                    &proposed_text,
                    session_id
                        .as_deref()
                        .unwrap_or(self.control.current_execution_session_id()),
                    agent_session_id
                        .as_deref()
                        .unwrap_or(self.control.current_agent_session_id()),
                ),
            ),
            HostRequest::ReviewApply { proposal_id } => Self::handle_review_result(
                self.reviews
                    .apply_proposal(self.control.policy(), &proposal_id),
            ),
            HostRequest::ReviewReject { proposal_id } => {
                Self::handle_review_result(self.reviews.reject_proposal(&proposal_id))
            }
        }
    }

    pub fn handle_request_json(&mut self, text: &str) -> String {
        match HostRequest::from_json(text) {
            Some(request) => self.handle_request(request).to_json(),
            None => {
                HostResponseEnvelope::error("invalid_request", "Failed to parse host request JSON")
                    .to_json()
            }
        }
    }

    fn handle_policy_result(
        &self,
        result: Result<PolicyChangeResult, ControlPlaneError>,
    ) -> HostResponseEnvelope {
        match result {
            Ok(change) => HostResponseEnvelope::success(HostResponse::PolicyChangeSnapshot(
                PolicyChangeSnapshot {
                    result: change,
                    snapshot: self.snapshot(),
                },
            )),
            Err(error) => HostResponseEnvelope::error("control_store_error", error.to_string()),
        }
    }

    fn handle_review_result(
        result: Result<WriteProposal, ReviewControlError>,
    ) -> HostResponseEnvelope {
        match result {
            Ok(proposal) => HostResponseEnvelope::success(HostResponse::ReviewProposal(proposal)),
            Err(error) => {
                let payload = review_error_payload(&error);
                HostResponseEnvelope::error(payload.code, payload.message)
            }
        }
    }

    fn load_metrics_snapshot(&self) -> Result<RuntimeMetricsSnapshot, StoreError> {
        let snapshot = self.broker_state.load()?;
        Ok(RuntimeStatusService::build_metrics_snapshot(
            &snapshot.metrics,
            snapshot.audit_log.len() as u64,
        ))
    }

    fn load_audit_events(
        &self,
        limit: usize,
        allowed: Option<bool>,
    ) -> Result<Vec<ai_ide_protocol::AuditEvent>, StoreError> {
        let snapshot = self.broker_state.load()?;
        let mut events = snapshot.audit_log;
        if let Some(allowed_filter) = allowed {
            events.retain(|event| event.allowed == allowed_filter);
        }
        let start = events.len().saturating_sub(limit);
        Ok(events.into_iter().skip(start).collect())
    }
}

fn review_error_payload(error: &ReviewControlError) -> HostError {
    match error {
        ReviewControlError::Review(review_error) => match review_error {
            ReviewError::Conflict { proposal_id } => HostError {
                code: "review_conflict".to_owned(),
                message: format!("Proposal conflicted with current file state: {proposal_id}"),
            },
            ReviewError::NotPending { proposal_id } => HostError {
                code: "review_not_pending".to_owned(),
                message: format!("Proposal is not pending: {proposal_id}"),
            },
            ReviewError::UnknownProposal { proposal_id } => HostError {
                code: "review_unknown_proposal".to_owned(),
                message: format!("Unknown review proposal: {proposal_id}"),
            },
        },
        ReviewControlError::Store(error) => HostError {
            code: "review_store_error".to_owned(),
            message: error.to_string(),
        },
        ReviewControlError::Io(error) => HostError {
            code: "review_io_error".to_owned(),
            message: error.to_string(),
        },
        ReviewControlError::PathEscapesRoot { target } => HostError {
            code: "review_path_escapes_root".to_owned(),
            message: format!("Path is not under workspace root: {target}"),
        },
        ReviewControlError::InternalPath { target } => HostError {
            code: "review_internal_path".to_owned(),
            message: format!("Blocked internal path: {target}"),
        },
        ReviewControlError::BlockedByPolicy { target } => HostError {
            code: "review_blocked_by_policy".to_owned(),
            message: format!("Blocked by policy: {target}"),
        },
        ReviewControlError::SymlinkPath { target } => HostError {
            code: "review_symlink_path".to_owned(),
            message: format!("Blocked symlink path: {target}"),
        },
        ReviewControlError::HardlinkPath { target } => HostError {
            code: "review_hardlink_path".to_owned(),
            message: format!("Blocked hardlink path: {target}"),
        },
        ReviewControlError::TargetIsDirectory { target } => HostError {
            code: "review_target_is_directory".to_owned(),
            message: format!("Target is not a file: {target}"),
        },
    }
}

fn workspace_error_payload(error: &WorkspaceError) -> HostError {
    match error {
        WorkspaceError::PathEscapesRoot { target } => HostError {
            code: "workspace_path_escapes_root".to_owned(),
            message: format!("Path is not under workspace root: {target}"),
        },
        WorkspaceError::InternalPath { target } => HostError {
            code: "workspace_internal_path".to_owned(),
            message: format!("Blocked internal path: {target}"),
        },
        WorkspaceError::BlockedByPolicy { target } => HostError {
            code: "workspace_blocked_by_policy".to_owned(),
            message: format!("Blocked by policy: {target}"),
        },
        WorkspaceError::SymlinkPath { target } => HostError {
            code: "workspace_symlink_path".to_owned(),
            message: format!("Blocked symlink path: {target}"),
        },
        WorkspaceError::HardlinkPath { target } => HostError {
            code: "workspace_hardlink_path".to_owned(),
            message: format!("Blocked hardlink path: {target}"),
        },
        WorkspaceError::DirectoryNotFound { target } => HostError {
            code: "workspace_directory_not_found".to_owned(),
            message: format!("Directory not found: {target}"),
        },
        WorkspaceError::Io(error) => HostError {
            code: "workspace_io_error".to_owned(),
            message: error.to_string(),
        },
    }
}

fn workspace_index_error_payload(error: &WorkspaceIndexError) -> HostError {
    match error {
        WorkspaceIndexError::Workspace(error) => workspace_error_payload(error),
        WorkspaceIndexError::Store(StoreError::Io(error)) => HostError {
            code: "workspace_index_store_error".to_owned(),
            message: error.to_string(),
        },
        WorkspaceIndexError::Store(StoreError::Parse(error)) => HostError {
            code: "workspace_index_store_error".to_owned(),
            message: error.to_string(),
        },
    }
}
