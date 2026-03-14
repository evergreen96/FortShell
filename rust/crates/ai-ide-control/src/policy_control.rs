use std::error::Error;
use std::fmt;
use std::path::Path;

use ai_ide_core::session::SessionManager;
use ai_ide_persistence::{PolicyStateStore, StoreError};
use ai_ide_policy::PolicyEngine;
use ai_ide_protocol::{AgentSession, PolicyChangeResult, PolicyState};

#[derive(Debug)]
pub enum ControlPlaneError {
    Store(StoreError),
}

impl fmt::Display for ControlPlaneError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ControlPlaneError::Store(error) => write!(f, "{error}"),
        }
    }
}

impl Error for ControlPlaneError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            ControlPlaneError::Store(error) => Some(error),
        }
    }
}

impl From<StoreError> for ControlPlaneError {
    fn from(value: StoreError) -> Self {
        Self::Store(value)
    }
}

pub struct ControlPlane {
    policy: PolicyEngine,
    store: PolicyStateStore,
    sessions: SessionManager,
}

impl ControlPlane {
    pub fn new(
        root: impl AsRef<Path>,
        default_agent_kind: impl Into<String>,
    ) -> Result<Self, ControlPlaneError> {
        let root = root.as_ref().to_path_buf();
        let mut policy = PolicyEngine::new(&root);
        let store = PolicyStateStore::new(&root);
        policy.replace_state(store.load()?);
        let sessions = SessionManager::new(policy.state().version, default_agent_kind);

        Ok(Self {
            policy,
            store,
            sessions,
        })
    }

    pub fn with_store_path(
        root: impl AsRef<Path>,
        store_path: impl AsRef<Path>,
        default_agent_kind: impl Into<String>,
    ) -> Result<Self, ControlPlaneError> {
        let root = root.as_ref().to_path_buf();
        let mut policy = PolicyEngine::new(&root);
        let store = PolicyStateStore::with_path(&root, store_path);
        policy.replace_state(store.load()?);
        let sessions = SessionManager::new(policy.state().version, default_agent_kind);

        Ok(Self {
            policy,
            store,
            sessions,
        })
    }

    pub fn policy(&self) -> &PolicyEngine {
        &self.policy
    }

    pub fn sessions(&self) -> &SessionManager {
        &self.sessions
    }

    pub fn store(&self) -> &PolicyStateStore {
        &self.store
    }

    pub fn current_execution_session_id(&self) -> &str {
        self.sessions.current_session_id()
    }

    pub fn current_agent_session_id(&self) -> &str {
        self.sessions.current_agent_session_id()
    }

    pub fn rotate_agent_session(&mut self, agent_kind: Option<&str>) -> AgentSession {
        self.sessions.rotate_agent_session(agent_kind)
    }

    pub fn add_deny_rule(&mut self, rule: &str) -> Result<PolicyChangeResult, ControlPlaneError> {
        if !self.policy.add_deny_rule(rule) {
            return Ok(self.current_outcome(false, false));
        }

        self.store.save(self.policy.state())?;
        Ok(self.rotate_sessions(true))
    }

    pub fn remove_deny_rule(
        &mut self,
        rule: &str,
    ) -> Result<PolicyChangeResult, ControlPlaneError> {
        if !self.policy.remove_deny_rule(rule) {
            return Ok(self.current_outcome(false, false));
        }

        self.store.save(self.policy.state())?;
        Ok(self.rotate_sessions(true))
    }

    pub fn sync_from_store(&mut self) -> Result<PolicyChangeResult, ControlPlaneError> {
        let current_state = self.policy.state().clone();
        let mut stored_state = self.store.load()?;
        if stored_state == current_state {
            return Ok(self.current_outcome(false, false));
        }

        if stored_state.version <= current_state.version {
            stored_state = PolicyState {
                deny_globs: stored_state.deny_globs,
                version: current_state.version + 1,
            };
            self.store.save(&stored_state)?;
        }

        self.policy.replace_state(stored_state);
        Ok(self.rotate_sessions(true))
    }

    fn rotate_sessions(&mut self, force: bool) -> PolicyChangeResult {
        let rotated = self
            .sessions
            .ensure_fresh_execution_session(self.policy.state().version, force);
        self.current_outcome(true, rotated)
    }

    fn current_outcome(&self, changed: bool, rotated: bool) -> PolicyChangeResult {
        PolicyChangeResult {
            changed,
            rotated,
            execution_session_id: self.current_execution_session_id().to_owned(),
            agent_session_id: self.current_agent_session_id().to_owned(),
            policy_version: self.policy.state().version,
        }
    }
}
