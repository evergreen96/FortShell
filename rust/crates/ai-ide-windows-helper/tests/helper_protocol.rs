use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use ai_ide_windows_helper::{
    HelperControlCommand, HelperStatusState, WindowsStrictHelperControlMessage,
    WindowsStrictHelperStatusMessage, encode_visible_host_path_token,
    helper_protocol_temp_directory, parse_helper_request_args, read_helper_control_message,
    read_helper_status_message, write_helper_control_message, write_helper_status_message,
};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "ai-ide-windows-helper-{name}-{}-{id}",
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
fn parses_one_shot_command_request() {
    let root = TestDir::new("oneshot");
    let request = parse_helper_request_args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
        "--setenv",
        "AI_IDE_STRICT_BACKEND",
        "restricted-host-helper",
        "--command",
        "python -c \"print(1)\"",
    ])
    .unwrap();

    assert_eq!(root.path(), request.workspace);
    assert_eq!("/workspace", request.cwd);
    assert_eq!(
        Some("restricted-host-helper"),
        request
            .environment
            .get("AI_IDE_STRICT_BACKEND")
            .map(|value| value.as_str())
    );
    assert_eq!(Some("python -c \"print(1)\""), request.command.as_deref());
    assert!(request.argv.is_empty());
    assert!(!request.stdio_proxy);
}

#[test]
fn parses_stdio_proxy_request_with_control_and_response_files() {
    let root = TestDir::new("proxy");
    let request = parse_helper_request_args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
        "--setenv",
        "AI_IDE_STRICT_BACKEND",
        "restricted-host-helper",
        "--setenv",
        "AI_IDE_SANDBOX_ROOT",
        "/workspace",
        "--stdio-proxy",
        "--control-file",
        "runtime/helper-control.json",
        "--response-file",
        "runtime/helper-status.json",
        "--argv=python",
        "--argv=-u",
        "--argv=-c",
        "--argv=print('ok')",
    ])
    .unwrap();

    assert!(request.stdio_proxy);
    assert_eq!(vec!["python", "-u", "-c", "print('ok')"], request.argv);
    assert!(request.command.is_none());
    assert!(
        request
            .control_file
            .unwrap()
            .ends_with(Path::new("runtime/helper-control.json"))
    );
    assert!(
        request
            .response_file
            .unwrap()
            .ends_with(Path::new("runtime/helper-status.json"))
    );
}

#[test]
fn parses_one_shot_argv_request() {
    let root = TestDir::new("oneshot-argv");
    let request = parse_helper_request_args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
        "--argv=python",
        "--argv=-c",
        "--argv=print('ok')",
    ])
    .unwrap();

    assert!(!request.stdio_proxy);
    assert_eq!(vec!["python", "-c", "print('ok')"], request.argv);
    assert!(request.command.is_none());
}

#[test]
fn rejects_missing_command_and_argv() {
    let root = TestDir::new("invalid");
    let error = parse_helper_request_args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
    ])
    .unwrap_err();

    assert_eq!(
        "expected --command or at least one --argv value",
        error.to_string()
    );
}

#[test]
fn control_message_roundtrip_is_structured() {
    let root = TestDir::new("control");
    let path = root.path().join("control.json");
    let message = WindowsStrictHelperControlMessage {
        version: 1,
        command: HelperControlCommand::Status,
        request_id: Some("req-1".to_string()),
        run_id: Some("run-1".to_string()),
        backend: Some("restricted-host-helper".to_string()),
    };

    write_helper_control_message(&path, &message).unwrap();
    let restored = read_helper_control_message(&path).unwrap();

    assert_eq!(message, restored);
}

#[test]
fn status_message_roundtrip_is_structured() {
    let root = TestDir::new("status");
    let path = root.path().join("status.json");
    let message = WindowsStrictHelperStatusMessage {
        version: 1,
        request_id: Some("req-2".to_string()),
        run_id: Some("run-1".to_string()),
        backend: Some("restricted-host-helper".to_string()),
        state: HelperStatusState::Running,
        pid: Some(4321),
        returncode: None,
    };

    write_helper_status_message(&path, &message).unwrap();
    let restored = read_helper_status_message(&path).unwrap();

    assert_eq!(message, restored);
}

#[test]
fn helper_protocol_temp_directory_is_sibling_scoped_per_target_file() {
    let root = TestDir::new("protocol-temp-dir");
    let control_path = root.path().join("runtime").join("control.json");
    let response_path = root.path().join("runtime").join("status.json");

    let control_temp = helper_protocol_temp_directory(&control_path);
    let response_temp = helper_protocol_temp_directory(&response_path);

    assert_eq!(control_path.parent(), control_temp.parent());
    assert_eq!(response_path.parent(), response_temp.parent());
    assert_ne!(control_temp, response_temp);
    assert!(
        control_temp
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or_default()
            .contains("control.json")
    );
}

#[test]
fn helper_host_path_tokens_use_expected_scheme() {
    let root = TestDir::new("token");
    let token =
        encode_visible_host_path_token(&root.path().join("notes").join("todo.txt")).unwrap();

    assert!(token.starts_with("aiide-helper://host-path/"));
    assert!(!token.ends_with('='));
}
