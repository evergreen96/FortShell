use ai_ide_core::session::SessionManager;
use ai_ide_protocol::SessionStatus;

#[test]
fn execution_rotation_preserves_agent_kind_and_marks_previous_sessions_stale() {
    let mut manager = SessionManager::new(1, "codex");

    let rotated = manager.ensure_fresh_execution_session(2, false);

    assert!(rotated);
    assert_eq!(manager.policy_version(), 2);
    assert_eq!(manager.current_agent_session().agent_kind, "codex");
    assert_eq!(manager.execution_sessions()[0].status, SessionStatus::Stale);
    assert_eq!(manager.agent_sessions()[0].status, SessionStatus::Stale);
    assert_eq!(
        manager.current_execution_session().rotated_from.as_deref(),
        Some("sess-00000001")
    );
    assert_eq!(
        manager.current_agent_session().rotated_from.as_deref(),
        Some("agent-00000001")
    );
}

#[test]
fn agent_rotation_keeps_execution_session_and_replaces_agent_session() {
    let mut manager = SessionManager::new(1, "default");
    let execution_id = manager.current_session_id().to_owned();

    let agent = manager.rotate_agent_session(Some("claude"));

    assert_eq!(agent.execution_session_id, execution_id);
    assert_eq!(agent.agent_kind, "claude");
    assert_eq!(manager.agent_sessions()[0].status, SessionStatus::Stale);
    assert!(manager.is_current_execution_session(&execution_id));
}
