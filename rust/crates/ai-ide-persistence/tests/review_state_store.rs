use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use ai_ide_persistence::ReviewStateStore;
use ai_ide_protocol::{ProposalStatus, WriteProposal};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "ai-ide-review-store-{name}-{}-{id}",
            std::process::id()
        ));
        std::fs::create_dir_all(&path).unwrap();
        Self { path }
    }

    fn path(&self) -> &std::path::Path {
        &self.path
    }
}

impl Drop for TestDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

fn sample_proposal() -> WriteProposal {
    WriteProposal {
        proposal_id: "rev-1234".to_owned(),
        target: "src/app.py".to_owned(),
        session_id: "sess-1".to_owned(),
        agent_session_id: "agent-1".to_owned(),
        created_at: "2026-03-07T00:00:00Z".to_owned(),
        updated_at: "2026-03-07T00:00:01Z".to_owned(),
        status: ProposalStatus::Pending,
        base_sha256: Some("abc".to_owned()),
        base_text: Some("print('old')\n".to_owned()),
        proposed_text: "print('new')\n".to_owned(),
    }
}

#[test]
fn load_returns_empty_snapshot_when_file_is_missing() {
    let root = TestDir::new("missing");
    let store = ReviewStateStore::new(root.path().join("reviews").join("state.json"));

    let snapshot = store.load().unwrap();

    assert_eq!(snapshot.proposals, Vec::<WriteProposal>::new());
}

#[test]
fn save_and_load_round_trip_proposals() {
    let root = TestDir::new("roundtrip");
    let store = ReviewStateStore::new(root.path().join("reviews").join("state.json"));
    let proposals = vec![sample_proposal()];

    store.save(&proposals).unwrap();
    let snapshot = store.load().unwrap();

    assert_eq!(snapshot.proposals, proposals);
}

#[test]
fn disabled_store_is_a_noop() {
    let store = ReviewStateStore::disabled();
    let proposals = vec![sample_proposal()];

    store.save(&proposals).unwrap();
    let snapshot = store.load().unwrap();

    assert_eq!(snapshot.proposals, Vec::<WriteProposal>::new());
}
