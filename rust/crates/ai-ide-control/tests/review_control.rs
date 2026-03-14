use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use ai_ide_control::{ReviewControlError, ReviewController};
use ai_ide_policy::PolicyEngine;
use ai_ide_protocol::ProposalStatus;

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "ai-ide-review-control-{name}-{}-{id}",
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
fn create_file_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

#[cfg(windows)]
fn create_file_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_file(target, link)
}

fn symlink_creation_unavailable(error: &std::io::Error) -> bool {
    matches!(error.kind(), std::io::ErrorKind::PermissionDenied)
        || error.raw_os_error() == Some(1314)
}

#[test]
fn stage_and_apply_round_trip_updates_target_file() {
    let root = TestDir::new("apply");
    std::fs::create_dir_all(root.path().join("src")).unwrap();
    let target = root.path().join("src").join("app.py");
    std::fs::write(&target, "print('old')\n").unwrap();
    let policy = PolicyEngine::new(root.path());
    let store_path = root.path().join(".runtime").join("reviews.json");
    let mut reviews = ReviewController::with_store_path(root.path(), &store_path).unwrap();

    let proposal = reviews
        .stage_write(&policy, "src/app.py", "print('new')\n", "sess-1", "agent-1")
        .unwrap();
    let applied = reviews
        .apply_proposal(&policy, &proposal.proposal_id)
        .unwrap();

    assert_eq!(applied.status, ProposalStatus::Applied);
    assert_eq!(std::fs::read_to_string(&target).unwrap(), "print('new')\n");
    assert_eq!(
        reviews
            .get_proposal(&proposal.proposal_id)
            .expect("proposal should exist")
            .status,
        ProposalStatus::Applied
    );
}

#[test]
fn apply_marks_conflict_when_file_changed_after_staging() {
    let root = TestDir::new("conflict");
    std::fs::create_dir_all(root.path().join("src")).unwrap();
    let target = root.path().join("src").join("app.py");
    std::fs::write(&target, "print('old')\n").unwrap();
    let policy = PolicyEngine::new(root.path());
    let store_path = root.path().join(".runtime").join("reviews.json");
    let mut reviews = ReviewController::with_store_path(root.path(), &store_path).unwrap();
    let proposal = reviews
        .stage_write(&policy, "src/app.py", "print('new')\n", "sess-1", "agent-1")
        .unwrap();

    std::fs::write(&target, "print('changed')\n").unwrap();

    let error = reviews
        .apply_proposal(&policy, &proposal.proposal_id)
        .expect_err("proposal should conflict");

    assert!(matches!(
        error,
        ReviewControlError::Review(ai_ide_core::review::ReviewError::Conflict { .. })
    ));
    assert_eq!(
        reviews
            .get_proposal(&proposal.proposal_id)
            .expect("proposal should exist")
            .status,
        ProposalStatus::Conflict
    );
}

#[test]
fn stage_write_rejects_policy_blocked_and_internal_paths() {
    let root = TestDir::new("blocked");
    std::fs::create_dir_all(root.path().join("safe")).unwrap();
    std::fs::create_dir_all(root.path().join("secrets")).unwrap();
    let mut policy = PolicyEngine::new(root.path());
    policy.add_deny_rule("secrets/**");
    let mut reviews = ReviewController::new(root.path());

    let blocked = reviews
        .stage_write(
            &policy,
            "secrets/token.txt",
            "secret\n",
            "sess-1",
            "agent-1",
        )
        .expect_err("policy should block denied paths");
    let internal = reviews
        .stage_write(&policy, ".ai-ide/policy.json", "{}\n", "sess-1", "agent-1")
        .expect_err("internal metadata path should be blocked");

    assert!(matches!(
        blocked,
        ReviewControlError::BlockedByPolicy { .. }
    ));
    assert!(matches!(internal, ReviewControlError::InternalPath { .. }));
}

#[test]
fn reject_updates_status_and_persists_snapshot() {
    let root = TestDir::new("reject");
    let policy = PolicyEngine::new(root.path());
    let store_path = root.path().join(".runtime").join("reviews.json");
    let mut reviews = ReviewController::with_store_path(root.path(), &store_path).unwrap();
    let proposal = reviews
        .stage_write(&policy, "src/app.py", "created\n", "sess-1", "agent-1")
        .unwrap();

    let rejected = reviews.reject_proposal(&proposal.proposal_id).unwrap();
    let restored = ReviewController::with_store_path(root.path(), &store_path).unwrap();

    assert_eq!(rejected.status, ProposalStatus::Rejected);
    assert_eq!(restored.count_proposals(Some(ProposalStatus::Rejected)), 1);
}

#[test]
fn render_proposal_returns_diff_text_for_pending_proposal() {
    let root = TestDir::new("render");
    std::fs::create_dir_all(root.path().join("src")).unwrap();
    std::fs::write(root.path().join("src").join("app.py"), "print('old')\n").unwrap();
    let policy = PolicyEngine::new(root.path());
    let mut reviews = ReviewController::new(root.path());
    let proposal = reviews
        .stage_write(&policy, "src/app.py", "print('new')\n", "sess-1", "agent-1")
        .unwrap();

    let rendered = reviews.render_proposal(&proposal.proposal_id).unwrap();

    assert!(rendered.contains("proposal_id="));
    assert!(rendered.contains("--- a/src/app.py"));
    assert!(rendered.contains("+++ b/src/app.py"));
}

#[test]
fn stage_write_rejects_symlink_aliases() {
    let root = TestDir::new("symlink");
    std::fs::create_dir_all(root.path().join("safe")).unwrap();
    std::fs::create_dir_all(root.path().join("secrets")).unwrap();
    std::fs::write(root.path().join("secrets").join("token.txt"), "secret\n").unwrap();

    match create_file_symlink(
        &root.path().join("secrets").join("token.txt"),
        &root.path().join("safe").join("token-link.txt"),
    ) {
        Ok(()) => {}
        Err(error) if symlink_creation_unavailable(&error) => return,
        Err(error) => panic!("failed to create symlink: {error}"),
    }

    let mut reviews = ReviewController::new(root.path());
    let policy = PolicyEngine::new(root.path());
    let error = reviews
        .stage_write(
            &policy,
            "safe/token-link.txt",
            "changed\n",
            "sess-1",
            "agent-1",
        )
        .expect_err("symlink aliases should be rejected");

    assert!(matches!(error, ReviewControlError::SymlinkPath { .. }));
}

#[test]
fn stage_write_rejects_hardlink_aliases() {
    let root = TestDir::new("hardlink");
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

    let mut reviews = ReviewController::new(root.path());
    let policy = PolicyEngine::new(root.path());
    let error = reviews
        .stage_write(
            &policy,
            "safe/token-alias.txt",
            "changed\n",
            "sess-1",
            "agent-1",
        )
        .expect_err("hardlink aliases should be rejected");

    assert!(matches!(error, ReviewControlError::HardlinkPath { .. }));
}
