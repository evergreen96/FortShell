use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use ai_ide_persistence::{POLICY_STATE_FILENAME, PROJECT_METADATA_DIR_NAME, PolicyStateStore};
use ai_ide_protocol::PolicyState;

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "ai-ide-persistence-{name}-{}-{id}",
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

#[test]
fn default_store_path_is_inside_project_metadata_dir() {
    let root = TestDir::new("default-path");
    let store = PolicyStateStore::new(root.path());

    assert_eq!(
        store.path(),
        root.path()
            .join(PROJECT_METADATA_DIR_NAME)
            .join(POLICY_STATE_FILENAME)
    );
}

#[test]
fn load_returns_default_state_when_file_is_missing() {
    let root = TestDir::new("missing");
    let store = PolicyStateStore::new(root.path());

    let state = store.load().unwrap();

    assert_eq!(state.deny_globs, Vec::<String>::new());
    assert_eq!(state.version, 1);
}

#[test]
fn save_and_load_round_trip_policy_state() {
    let root = TestDir::new("roundtrip");
    let store = PolicyStateStore::new(root.path());
    store
        .save(&PolicyState {
            deny_globs: vec!["secrets/**".to_owned(), "env/".to_owned()],
            version: 4,
        })
        .unwrap();

    let state = store.load().unwrap();

    assert_eq!(state.deny_globs, vec!["secrets/**", "env/"]);
    assert_eq!(state.version, 4);
}

#[test]
fn save_clamps_version_to_one() {
    let root = TestDir::new("clamp-save");
    let store = PolicyStateStore::new(root.path());
    store
        .save(&PolicyState {
            deny_globs: vec!["secrets/**".to_owned()],
            version: 0,
        })
        .unwrap();

    let state = store.load().unwrap();

    assert_eq!(state.version, 1);
}

#[test]
fn load_clamps_version_to_one() {
    let root = TestDir::new("clamp-load");
    let store = PolicyStateStore::new(root.path());
    std::fs::create_dir_all(store.path().parent().unwrap()).unwrap();
    std::fs::write(
        store.path(),
        "{\n  \"deny_globs\": [\"secrets/**\"],\n  \"version\": 0\n}\n",
    )
    .unwrap();

    let state = store.load().unwrap();

    assert_eq!(state.deny_globs, vec!["secrets/**"]);
    assert_eq!(state.version, 1);
}

#[test]
fn load_returns_parse_error_for_invalid_json() {
    let root = TestDir::new("parse-error");
    let store = PolicyStateStore::new(root.path());
    std::fs::create_dir_all(store.path().parent().unwrap()).unwrap();
    std::fs::write(store.path(), "{not-json}\n").unwrap();

    let error = store.load().unwrap_err();

    assert!(matches!(error, ai_ide_persistence::StoreError::Parse(_)));
}

#[test]
fn custom_path_is_respected() {
    let root = TestDir::new("custom-path");
    let custom_path = root.path().join("state").join("policy-state.json");
    let store = PolicyStateStore::with_path(root.path(), &custom_path);
    store
        .save(&PolicyState {
            deny_globs: vec!["src/**".to_owned()],
            version: 5,
        })
        .unwrap();

    assert_eq!(store.path(), custom_path);
    assert!(custom_path.exists());
    assert_eq!(store.load().unwrap().version, 5);
}
