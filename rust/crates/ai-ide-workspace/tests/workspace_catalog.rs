use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use ai_ide_policy::PolicyEngine;
use ai_ide_workspace::{WorkspaceCatalog, WorkspaceError, WorkspaceIndexService};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "ai-ide-workspace-{name}-{}-{id}",
            std::process::id()
        ));
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

#[cfg(unix)]
fn create_dir_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

#[cfg(windows)]
fn create_dir_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_dir(target, link)
}

fn symlink_creation_unavailable(error: &std::io::Error) -> bool {
    matches!(error.kind(), std::io::ErrorKind::PermissionDenied)
        || error.raw_os_error() == Some(1314)
}

#[test]
fn list_tree_and_grep_hide_internal_and_policy_blocked_entries() {
    let root = TestDir::new("visible");
    std::fs::create_dir_all(root.path().join("notes").join("nested")).unwrap();
    std::fs::create_dir_all(root.path().join("secrets")).unwrap();
    std::fs::create_dir_all(root.path().join(".ai-ide")).unwrap();
    std::fs::write(root.path().join("notes").join("todo.txt"), "visible text\n").unwrap();
    std::fs::write(
        root.path().join("notes").join("nested").join("deep.txt"),
        "deep text\n",
    )
    .unwrap();
    std::fs::write(
        root.path().join("secrets").join("token.txt"),
        "hidden text\n",
    )
    .unwrap();
    std::fs::write(root.path().join(".ai-ide").join("policy.json"), "{}\n").unwrap();

    let mut policy = PolicyEngine::new(root.path());
    policy.add_deny_rule("secrets/**");
    let catalog = WorkspaceCatalog::new(root.path());

    let listing = catalog.list_dir(&policy, ".").unwrap();
    let tree = catalog.tree(&policy, "notes").unwrap();
    let grep = catalog.grep(&policy, "text", ".").unwrap();

    assert_eq!(
        vec!["notes"],
        listing
            .iter()
            .map(|entry| entry.path.clone())
            .collect::<Vec<_>>()
    );
    assert_eq!(
        vec!["notes/"],
        listing
            .iter()
            .map(|entry| entry.display_path.clone())
            .collect::<Vec<_>>()
    );
    assert_eq!(
        vec!["notes/nested", "notes/nested/deep.txt", "notes/todo.txt"],
        tree.iter()
            .map(|entry| entry.path.clone())
            .collect::<Vec<_>>()
    );
    assert_eq!(
        vec![
            "notes/nested/deep.txt:1:deep text",
            "notes/todo.txt:1:visible text"
        ],
        grep.iter()
            .map(|item| format!("{}:{}:{}", item.path, item.line_number, item.line_text))
            .collect::<Vec<_>>()
    );
}

#[test]
fn blocked_internal_missing_and_escaped_targets_return_explicit_errors() {
    let root = TestDir::new("blocked");
    std::fs::create_dir_all(root.path().join("notes")).unwrap();
    std::fs::create_dir_all(root.path().join("secrets")).unwrap();
    std::fs::create_dir_all(root.path().join(".ai-ide")).unwrap();

    let mut policy = PolicyEngine::new(root.path());
    policy.add_deny_rule("secrets/**");
    let catalog = WorkspaceCatalog::new(root.path());

    match catalog.list_dir(&policy, "secrets") {
        Err(WorkspaceError::BlockedByPolicy { target }) => assert_eq!(target, "secrets"),
        other => panic!("unexpected result: {other:?}"),
    }

    match catalog.tree(&policy, ".ai-ide") {
        Err(WorkspaceError::InternalPath { target }) => assert_eq!(target, ".ai-ide"),
        other => panic!("unexpected result: {other:?}"),
    }

    match catalog.grep(&policy, "text", "missing") {
        Err(WorkspaceError::DirectoryNotFound { target }) => assert_eq!(target, "missing"),
        other => panic!("unexpected result: {other:?}"),
    }

    match catalog.list_dir(&policy, "../outside") {
        Err(WorkspaceError::DirectoryNotFound { target }) => assert_eq!(target, "../outside"),
        other => panic!("unexpected result: {other:?}"),
    }
}

#[test]
fn workspace_index_refresh_persists_visible_entries_and_tracks_policy_version() {
    let root = TestDir::new("index");
    std::fs::create_dir_all(root.path().join("notes").join("nested")).unwrap();
    std::fs::create_dir_all(root.path().join("secrets")).unwrap();
    std::fs::write(root.path().join("notes").join("todo.txt"), "visible text\n").unwrap();
    std::fs::write(
        root.path().join("notes").join("nested").join("deep.txt"),
        "deep text\n",
    )
    .unwrap();
    std::fs::write(
        root.path().join("secrets").join("token.txt"),
        "hidden text\n",
    )
    .unwrap();

    let mut policy = PolicyEngine::new(root.path());
    policy.add_deny_rule("secrets/**");
    let service = WorkspaceIndexService::with_store_path(
        root.path(),
        root.path()
            .join(".runtime")
            .join("workspace")
            .join("index.json"),
    );

    let snapshot = service.refresh(&policy).unwrap();
    let restored = service.load().unwrap();

    assert_eq!(snapshot, restored);
    assert_eq!(snapshot.policy_version, policy.state().version);
    assert_eq!(
        vec![
            "notes",
            "notes/nested",
            "notes/nested/deep.txt",
            "notes/todo.txt",
        ],
        snapshot
            .entries
            .iter()
            .map(|entry| entry.path.clone())
            .collect::<Vec<_>>()
    );
    assert!(snapshot.entries.iter().all(|entry| entry.modified_ns > 0));
}

#[test]
fn symlink_aliases_are_hidden_and_rejected() {
    let root = TestDir::new("symlink");
    std::fs::create_dir_all(root.path().join("notes")).unwrap();
    std::fs::create_dir_all(root.path().join("secrets")).unwrap();
    std::fs::write(
        root.path().join("secrets").join("token.txt"),
        "hidden text\n",
    )
    .unwrap();

    match create_dir_symlink(
        &root.path().join("secrets"),
        &root.path().join("notes").join("secret-link"),
    ) {
        Ok(()) => {}
        Err(error) if symlink_creation_unavailable(&error) => return,
        Err(error) => panic!("failed to create symlink: {error}"),
    }

    let mut policy = PolicyEngine::new(root.path());
    policy.add_deny_rule("secrets/**");
    let catalog = WorkspaceCatalog::new(root.path());
    let service = WorkspaceIndexService::with_store_path(
        root.path(),
        root.path()
            .join(".runtime")
            .join("workspace")
            .join("index.json"),
    );

    let tree = catalog.tree(&policy, "notes").unwrap();
    let snapshot = service.refresh(&policy).unwrap();

    assert!(tree.is_empty());
    assert!(
        snapshot
            .entries
            .iter()
            .all(|entry| entry.path != "notes/secret-link")
    );
    match catalog.tree(&policy, "notes/secret-link") {
        Err(WorkspaceError::SymlinkPath { target }) => assert_eq!(target, "notes/secret-link"),
        other => panic!("unexpected result: {other:?}"),
    }
}

#[test]
fn hardlink_aliases_are_hidden_and_rejected() {
    let root = TestDir::new("hardlink");
    std::fs::create_dir_all(root.path().join("notes")).unwrap();
    std::fs::create_dir_all(root.path().join("secrets")).unwrap();
    std::fs::write(
        root.path().join("secrets").join("token.txt"),
        "hidden token\n",
    )
    .unwrap();

    match std::fs::hard_link(
        root.path().join("secrets").join("token.txt"),
        root.path().join("notes").join("token-alias.txt"),
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

    let mut policy = PolicyEngine::new(root.path());
    policy.add_deny_rule("secrets/**");
    let catalog = WorkspaceCatalog::new(root.path());
    let service = WorkspaceIndexService::with_store_path(
        root.path(),
        root.path()
            .join(".runtime")
            .join("workspace")
            .join("index.json"),
    );

    let tree = catalog.tree(&policy, "notes").unwrap();
    let snapshot = service.refresh(&policy).unwrap();

    assert!(tree.is_empty());
    assert!(
        snapshot
            .entries
            .iter()
            .all(|entry| entry.path != "notes/token-alias.txt")
    );
    match catalog.tree(&policy, "notes/token-alias.txt") {
        Err(WorkspaceError::HardlinkPath { target }) => {
            assert_eq!(target, "notes/token-alias.txt")
        }
        other => panic!("unexpected result: {other:?}"),
    }
}
