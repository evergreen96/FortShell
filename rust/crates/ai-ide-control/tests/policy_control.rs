use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use ai_ide_control::ControlPlane;
use ai_ide_persistence::PolicyStateStore;
use ai_ide_protocol::PolicyState;

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path =
            std::env::temp_dir().join(format!("ai-ide-control-{name}-{}-{id}", std::process::id()));
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
fn new_loads_persisted_policy_state() {
    let root = TestDir::new("load");
    let store = PolicyStateStore::new(root.path());
    store
        .save(&PolicyState {
            deny_globs: vec!["secrets/**".to_owned()],
            version: 4,
        })
        .unwrap();

    let plane = ControlPlane::new(root.path(), "codex").unwrap();

    assert_eq!(plane.policy().state().deny_globs, vec!["secrets/**"]);
    assert_eq!(plane.policy().state().version, 4);
    assert_eq!(plane.sessions().policy_version(), 4);
}

#[test]
fn add_rule_persists_and_rotates_session() {
    let root = TestDir::new("add");
    let mut plane = ControlPlane::new(root.path(), "codex").unwrap();
    let initial_execution = plane.current_execution_session_id().to_owned();
    let initial_agent = plane.current_agent_session_id().to_owned();

    let outcome = plane.add_deny_rule("secrets/**").unwrap();

    assert!(outcome.changed);
    assert!(outcome.rotated);
    assert_ne!(outcome.execution_session_id, initial_execution);
    assert_ne!(outcome.agent_session_id, initial_agent);
    assert_eq!(plane.store().load().unwrap().deny_globs, vec!["secrets/**"]);
}

#[test]
fn duplicate_add_is_a_noop() {
    let root = TestDir::new("noop-add");
    let mut plane = ControlPlane::new(root.path(), "codex").unwrap();
    plane.add_deny_rule("secrets/**").unwrap();
    let execution = plane.current_execution_session_id().to_owned();
    let agent = plane.current_agent_session_id().to_owned();

    let outcome = plane.add_deny_rule("secrets/**").unwrap();

    assert!(!outcome.changed);
    assert!(!outcome.rotated);
    assert_eq!(outcome.execution_session_id, execution);
    assert_eq!(outcome.agent_session_id, agent);
}

#[test]
fn remove_rule_persists_and_rotates_session() {
    let root = TestDir::new("remove");
    let mut plane = ControlPlane::new(root.path(), "codex").unwrap();
    plane.add_deny_rule("secrets/**").unwrap();
    let execution = plane.current_execution_session_id().to_owned();

    let outcome = plane.remove_deny_rule("secrets/**").unwrap();

    assert!(outcome.changed);
    assert!(outcome.rotated);
    assert_ne!(outcome.execution_session_id, execution);
    assert_eq!(
        plane.store().load().unwrap().deny_globs,
        Vec::<String>::new()
    );
}

#[test]
fn sync_from_store_is_a_noop_when_state_matches() {
    let root = TestDir::new("sync-noop");
    let mut plane = ControlPlane::new(root.path(), "codex").unwrap();
    let execution = plane.current_execution_session_id().to_owned();

    let outcome = plane.sync_from_store().unwrap();

    assert!(!outcome.changed);
    assert!(!outcome.rotated);
    assert_eq!(outcome.execution_session_id, execution);
}

#[test]
fn sync_from_store_preserves_selected_agent_kind() {
    let root = TestDir::new("sync-kind");
    let mut left = ControlPlane::new(root.path(), "default").unwrap();
    let mut right = ControlPlane::new(root.path(), "default").unwrap();
    right.rotate_agent_session(Some("claude"));
    let previous_execution = right.current_execution_session_id().to_owned();
    let previous_agent = right.current_agent_session_id().to_owned();

    left.add_deny_rule("secrets/**").unwrap();
    let outcome = right.sync_from_store().unwrap();

    assert!(outcome.changed);
    assert!(outcome.rotated);
    assert_ne!(right.current_execution_session_id(), previous_execution);
    assert_ne!(right.current_agent_session_id(), previous_agent);
    assert_eq!(
        right.sessions().current_agent_session().agent_kind,
        "claude"
    );
}

#[test]
fn sync_from_store_bumps_version_for_older_external_state() {
    let root = TestDir::new("sync-version");
    let mut plane = ControlPlane::new(root.path(), "codex").unwrap();
    plane.add_deny_rule("src/**").unwrap();
    let store = PolicyStateStore::new(root.path());
    store
        .save(&PolicyState {
            deny_globs: vec!["secrets/**".to_owned()],
            version: 1,
        })
        .unwrap();

    let outcome = plane.sync_from_store().unwrap();

    assert!(outcome.changed);
    assert!(outcome.rotated);
    assert_eq!(plane.policy().state().deny_globs, vec!["secrets/**"]);
    assert_eq!(plane.policy().state().version, 3);
    assert_eq!(store.load().unwrap().version, 3);
}
