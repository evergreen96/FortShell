use ai_ide_protocol::{AgentSession, ExecutionSession, SessionStatus};

use crate::time::mock_timestamp;

pub struct SessionManager {
    execution_sessions: Vec<ExecutionSession>,
    agent_sessions: Vec<AgentSession>,
    execution_counter: u64,
    agent_counter: u64,
    timestamp_counter: u64,
    current_execution_index: usize,
    current_agent_index: usize,
}

impl SessionManager {
    pub fn new(initial_policy_version: u64, default_agent_kind: impl Into<String>) -> Self {
        let mut manager = Self {
            execution_sessions: Vec::new(),
            agent_sessions: Vec::new(),
            execution_counter: 0,
            agent_counter: 0,
            timestamp_counter: 0,
            current_execution_index: 0,
            current_agent_index: 0,
        };
        let execution = manager.create_execution_session(initial_policy_version, None);
        let agent = manager.create_agent_session(
            execution.session_id.clone(),
            default_agent_kind.into(),
            None,
        );
        manager.execution_sessions.push(execution);
        manager.agent_sessions.push(agent);
        manager
    }

    pub fn current_execution_session(&self) -> &ExecutionSession {
        &self.execution_sessions[self.current_execution_index]
    }

    pub fn current_agent_session(&self) -> &AgentSession {
        &self.agent_sessions[self.current_agent_index]
    }

    pub fn current_session_id(&self) -> &str {
        &self.current_execution_session().session_id
    }

    pub fn current_agent_session_id(&self) -> &str {
        &self.current_agent_session().agent_session_id
    }

    pub fn policy_version(&self) -> u64 {
        self.current_execution_session().policy_version
    }

    pub fn execution_sessions(&self) -> &[ExecutionSession] {
        &self.execution_sessions
    }

    pub fn agent_sessions(&self) -> &[AgentSession] {
        &self.agent_sessions
    }

    pub fn is_current_execution_session(&self, session_id: &str) -> bool {
        self.current_session_id() == session_id
    }

    pub fn ensure_fresh_execution_session(&mut self, policy_version: u64, force: bool) -> bool {
        if !force && self.current_execution_session().policy_version == policy_version {
            return false;
        }

        let previous_execution = self.current_execution_session().clone();
        let previous_agent = self.current_agent_session().clone();
        self.execution_sessions[self.current_execution_index].status = SessionStatus::Stale;
        self.agent_sessions[self.current_agent_index].status = SessionStatus::Stale;

        let execution = self
            .create_execution_session(policy_version, Some(previous_execution.session_id.clone()));
        self.execution_sessions.push(execution);
        self.current_execution_index = self.execution_sessions.len() - 1;

        let agent = self.create_agent_session(
            self.current_session_id().to_owned(),
            previous_agent.agent_kind,
            Some(previous_agent.agent_session_id),
        );
        self.agent_sessions.push(agent);
        self.current_agent_index = self.agent_sessions.len() - 1;
        true
    }

    pub fn rotate_agent_session(&mut self, agent_kind: Option<&str>) -> AgentSession {
        let previous = self.current_agent_session().clone();
        self.agent_sessions[self.current_agent_index].status = SessionStatus::Stale;
        let session = self.create_agent_session(
            self.current_session_id().to_owned(),
            agent_kind.unwrap_or(&previous.agent_kind).to_owned(),
            Some(previous.agent_session_id),
        );
        self.agent_sessions.push(session.clone());
        self.current_agent_index = self.agent_sessions.len() - 1;
        session
    }

    pub fn list_agent_sessions(&self, execution_session_id: Option<&str>) -> Vec<AgentSession> {
        self.agent_sessions
            .iter()
            .filter(|session| {
                execution_session_id.is_none_or(|value| session.execution_session_id == value)
            })
            .cloned()
            .collect()
    }

    fn create_execution_session(
        &mut self,
        policy_version: u64,
        rotated_from: Option<String>,
    ) -> ExecutionSession {
        ExecutionSession {
            session_id: self.next_execution_id(),
            policy_version,
            created_at: self.next_timestamp(),
            status: SessionStatus::Active,
            rotated_from,
        }
    }

    fn create_agent_session(
        &mut self,
        execution_session_id: String,
        agent_kind: String,
        rotated_from: Option<String>,
    ) -> AgentSession {
        AgentSession {
            agent_session_id: self.next_agent_id(),
            execution_session_id,
            agent_kind,
            created_at: self.next_timestamp(),
            status: SessionStatus::Active,
            rotated_from,
        }
    }

    fn next_execution_id(&mut self) -> String {
        self.execution_counter += 1;
        format!("sess-{:08x}", self.execution_counter)
    }

    fn next_agent_id(&mut self) -> String {
        self.agent_counter += 1;
        format!("agent-{:08x}", self.agent_counter)
    }

    fn next_timestamp(&mut self) -> String {
        self.timestamp_counter += 1;
        mock_timestamp(self.timestamp_counter)
    }
}
