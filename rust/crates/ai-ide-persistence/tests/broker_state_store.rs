use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use ai_ide_persistence::BrokerStateStore;
use ai_ide_protocol::{AuditEvent, UsageMetrics};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "ai-ide-broker-state-{name}-{}-{id}",
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
fn load_returns_default_snapshot_when_file_is_missing() {
    let root = TestDir::new("missing");
    let store = BrokerStateStore::new(root.path().join("broker").join("state.json"));

    let snapshot = store.load().unwrap();

    assert_eq!(snapshot.metrics.read_count, 0);
    assert!(snapshot.audit_log.is_empty());
}

#[test]
fn save_and_load_round_trip_metrics_and_audit_log() {
    let root = TestDir::new("roundtrip");
    let store = BrokerStateStore::new(root.path().join("broker").join("state.json"));
    let metrics = UsageMetrics {
        read_count: 2,
        grep_count: 1,
        blocked_count: 3,
        ..UsageMetrics::default()
    };
    let audit_log = vec![
        AuditEvent {
            timestamp: "2026-03-07T00:00:00Z".to_owned(),
            session_id: "sess-1".to_owned(),
            action: "read".to_owned(),
            target: "C:/repo/file.txt".to_owned(),
            allowed: true,
            detail: "bytes=10".to_owned(),
        },
        AuditEvent {
            timestamp: "2026-03-07T00:00:01Z".to_owned(),
            session_id: "sess-1".to_owned(),
            action: "read".to_owned(),
            target: "C:/repo/secret.txt".to_owned(),
            allowed: false,
            detail: "denied by policy".to_owned(),
        },
    ];
    store.save(&metrics, &audit_log).unwrap();

    let snapshot = store.load().unwrap();

    assert_eq!(snapshot.metrics.read_count, 2);
    assert_eq!(snapshot.metrics.grep_count, 1);
    assert_eq!(snapshot.metrics.blocked_count, 3);
    assert_eq!(snapshot.audit_log.len(), 2);
    assert!(!snapshot.audit_log[1].allowed);
}
