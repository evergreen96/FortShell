use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use ai_ide_persistence::WorkspaceIndexStateStore;
use ai_ide_protocol::{WorkspaceIndexEntry, WorkspaceIndexSnapshot};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "ai-ide-workspace-index-store-{name}-{}-{id}",
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

#[test]
fn load_returns_default_snapshot_when_file_is_missing() {
    let root = TestDir::new("missing");
    let store =
        WorkspaceIndexStateStore::with_path(root.path().join("workspace").join("index.json"));

    let snapshot = store.load().unwrap();

    assert_eq!(snapshot.policy_version, 0);
    assert!(snapshot.entries.is_empty());
}

#[test]
fn save_and_load_round_trip_snapshot() {
    let root = TestDir::new("roundtrip");
    let store =
        WorkspaceIndexStateStore::with_path(root.path().join("workspace").join("index.json"));
    let snapshot = WorkspaceIndexSnapshot {
        policy_version: 3,
        entries: vec![WorkspaceIndexEntry {
            path: "notes/todo.txt".to_owned(),
            is_dir: false,
            size: 12,
            modified_ns: 42,
        }],
    };

    store.save(&snapshot).unwrap();
    let restored = store.load().unwrap();

    assert_eq!(snapshot, restored);
}
