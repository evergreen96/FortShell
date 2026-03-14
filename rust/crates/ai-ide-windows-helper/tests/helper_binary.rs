use std::cell::RefCell;
use std::collections::HashMap;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use ai_ide_windows_helper::{
    HelperControlCommand, WindowsStrictHelperControlMessage, capture_label_security_descriptor,
    helper_protocol_temp_directory, read_helper_status_message, write_helper_control_message,
};

static COUNTER: AtomicU64 = AtomicU64::new(0);
static HELPER_BINARY_TEST_MUTEX: Mutex<()> = Mutex::new(());
static HELPER_CAPABILITY_CACHE: OnceLock<Mutex<HashMap<PathBuf, HelperCapabilities>>> = OnceLock::new();
thread_local! {
    static HELPER_BINARY_TEST_LOCK: RefCell<Option<std::sync::MutexGuard<'static, ()>>> = const { RefCell::new(None) };
    static HELPER_BINARY_TEST_LOCK_DEPTH: RefCell<usize> = const { RefCell::new(0) };
}

#[derive(Clone, Copy)]
struct HelperCapabilities {
    restricted_token: bool,
    write_boundary: bool,
    read_boundary: bool,
}

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> Self {
        HELPER_BINARY_TEST_LOCK.with(|slot| {
            if slot.borrow().is_none() {
                let guard = HELPER_BINARY_TEST_MUTEX
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                *slot.borrow_mut() = Some(guard);
            }
        });
        HELPER_BINARY_TEST_LOCK_DEPTH.with(|depth| *depth.borrow_mut() += 1);
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "ai-ide-windows-helper-bin-{name}-{}-{id}",
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
        HELPER_BINARY_TEST_LOCK_DEPTH.with(|depth| {
            let mut depth = depth.borrow_mut();
            *depth = depth.saturating_sub(1);
            if *depth == 0 {
                HELPER_BINARY_TEST_LOCK.with(|slot| {
                    slot.borrow_mut().take();
                });
            }
        });
    }
}

#[cfg(windows)]
fn helper_reports_restricted_token_enabled(root: &TestDir) -> bool {
    helper_capabilities(root.path()).restricted_token
}

#[cfg(windows)]
fn helper_reports_write_boundary_enabled(root: &TestDir) -> bool {
    helper_capabilities(root.path()).write_boundary
}

#[cfg(windows)]
fn helper_reports_read_boundary_enabled(root: &TestDir) -> bool {
    helper_capabilities(root.path()).read_boundary
}

#[cfg(windows)]
fn host_python_executable() -> Option<PathBuf> {
    if let Ok(value) = std::env::var("PYTHON") {
        let path = PathBuf::from(value);
        if path.exists() {
            return Some(path);
        }
    }
    let output = Command::new("python")
        .args(["-c", "import sys; print(sys.executable)"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let value = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if value.is_empty() {
        return None;
    }
    let path = PathBuf::from(value);
    path.exists().then_some(path)
}

#[cfg(windows)]
fn helper_reports_restricted_token_enabled_raw(workspace: &Path) -> bool {
    helper_capabilities(workspace).restricted_token
}

#[cfg(windows)]
fn helper_reports_write_boundary_enabled_raw(workspace: &Path) -> bool {
    helper_capabilities(workspace).write_boundary
}

#[cfg(windows)]
fn helper_reports_read_boundary_enabled_raw(workspace: &Path) -> bool {
    helper_capabilities(workspace).read_boundary
}

#[cfg(windows)]
fn helper_capabilities(workspace: &Path) -> HelperCapabilities {
    let workspace = workspace.to_path_buf();
    let cache = HELPER_CAPABILITY_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Some(result) = cache.lock().unwrap().get(&workspace).copied() {
        return result;
    }

    let helper_root = helper_env_root(workspace.file_name().unwrap().to_str().unwrap());
    let fixture_command = "echo __AI_IDE_FIXTURE__ .ai_ide_strict_fixture.txt";
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--command",
            fixture_command,
        ])
        .output()
        .unwrap();

    let stdout = String::from_utf8_lossy(&output.stdout);
    let result = HelperCapabilities {
        restricted_token: stdout.contains("__AI_IDE_FIXTURE__ restricted_token=enabled"),
        write_boundary: stdout.contains("__AI_IDE_FIXTURE__ write_boundary=enabled"),
        read_boundary: stdout.contains("__AI_IDE_FIXTURE__ read_boundary=enabled"),
    };
    cache.lock().unwrap().insert(workspace, result);
    result
}

#[cfg(windows)]
fn create_junction(link: &Path, target: &Path) -> bool {
    Command::new("cmd")
        .args([
            "/C",
            "mklink",
            "/J",
            link.to_str().unwrap(),
            target.to_str().unwrap(),
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

#[test]
fn binary_runs_one_shot_command_and_forwards_output() {
    let root = TestDir::new("oneshot");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "echo helper-one-shot",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("helper-one-shot"),
        "stdout was: {}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_internal_workspace_metadata_cwd() {
    let root = TestDir::new("cwd-internal-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace/.ai_ide_runtime/processes",
            "--command",
            "echo should-not-run",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("internal workspace metadata"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_alternate_data_stream_cwd() {
    let root = TestDir::new("cwd-ads-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace/src:secret",
            "--command",
            "echo should-not-run",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("alternate data streams"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_reserved_device_name_cwd() {
    let root = TestDir::new("cwd-reserved-device-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace/NUL",
            "--command",
            "echo should-not-run",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("Windows reserved device names"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_workspace_reparse_point_cwd() {
    let root = TestDir::new("cwd-reparse-reject");
    let outside = root.path().join("outside");
    let link = root.path().join("linked");
    std::fs::create_dir_all(&outside).unwrap();
    if !create_junction(&link, &outside) {
        return;
    }

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace/linked",
            "--command",
            "echo should-not-run",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("reparse points"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let _ = std::fs::remove_dir_all(&outside);
}

#[cfg(windows)]
#[test]
fn binary_rejects_pythonpath_override_through_workspace_reparse_point() {
    let root = TestDir::new("env-reparse-reject");
    let outside = root.path().join("outside");
    let link = root.path().join("linked");
    std::fs::create_dir_all(&outside).unwrap();
    if !create_junction(&link, &outside) {
        return;
    }

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            link.to_str().unwrap(),
            "--command",
            "echo should-not-run",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("reparse points"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let _ = std::fs::remove_dir_all(&outside);
}

#[cfg(windows)]
#[test]
fn binary_rejects_pythonpath_override_with_alternate_data_stream() {
    let root = TestDir::new("env-ads-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            r".\src\pkg.py:secret",
            "--command",
            "echo should-not-run",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("alternate data streams"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_pythonpath_override_with_reserved_device_name() {
    let root = TestDir::new("env-reserved-device-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            "NUL.txt",
            "--command",
            "echo should-not-run",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("Windows reserved device names"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_pythonpath_override_through_helper_reparse_point() {
    let root = TestDir::new("env-helper-reparse-reject");
    let helper_root = helper_env_root("env-helper-reparse-reject");
    let home = helper_root.join("home");
    let outside = helper_root.join("outside");
    let link = home.join("linked");
    std::fs::create_dir_all(&home).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&outside).unwrap();
    if !create_junction(&link, &outside) {
        return;
    }

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "HOME",
            home.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "PYTHONPATH",
            link.to_str().unwrap(),
            "--command",
            "echo should-not-run",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("reparse points"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_dir_builtin_in_shell_mode() {
    let root = TestDir::new("shell-dir-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "dir",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("filesystem builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_type_builtin_in_shell_mode() {
    let root = TestDir::new("shell-type-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "type",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("filesystem builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_copy_builtin_in_shell_mode() {
    let root = TestDir::new("shell-copy-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "copy a.txt b.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("filesystem builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_del_builtin_in_shell_mode() {
    let root = TestDir::new("shell-del-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "del note.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("filesystem builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_mkdir_builtin_in_shell_mode() {
    let root = TestDir::new("shell-mkdir-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "mkdir build",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("filesystem builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_shell_command_absolute_path_literal_outside_allowed_roots() {
    let root = TestDir::new("shell-abs-path-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type C:\Users\Public\secret.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("shell command path must stay under workspace"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_shell_command_attached_option_value_path_outside_workspace() {
    let root = TestDir::new("shell-option-path-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"echo --config=C:\Users\Public\outside.py",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("shell command path must stay under workspace"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_shell_command_response_file_path_outside_workspace() {
    let root = TestDir::new("shell-response-path-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"python @C:\Users\Public\outside.rsp",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("shell command path must stay under workspace"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_alternate_data_stream_literal() {
    let root = TestDir::new("shell-ads-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type .\file.txt:secret",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("alternate data streams"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_reserved_device_literal() {
    let root = TestDir::new("shell-reserved-device-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type NUL",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("Windows reserved device names"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_bare_internal_workspace_metadata_literal() {
    let root = TestDir::new("shell-internal-bare-reject");
    std::fs::create_dir_all(root.path().join(".ai_ide_runtime")).unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"dir .ai_ide_runtime",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("internal workspace metadata"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_parent_traversal_literal() {
    let root = TestDir::new("shell-parent-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type ..\secret.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("shell command path must not escape helper roots"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_drive_relative_literal() {
    let root = TestDir::new("shell-drive-relative-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type C:secret.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("drive-relative form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_root_relative_literal() {
    let root = TestDir::new("shell-root-relative-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type \Users\Public\secret.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("root-relative form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_forward_slash_root_relative_literal() {
    let root = TestDir::new("shell-root-relative-forward-slash-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "type /Users/Public/secret.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("root-relative form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_unc_literal() {
    let root = TestDir::new("shell-unc-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type \\server\share\secret.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("UNC or device form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_runs_shell_command_with_derived_local_shell_path_when_parent_env_is_hostile() {
    let root = TestDir::new("shell-derived-comspec");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("ComSpec", r"\\server\share\cmd.exe")
        .env("SystemRoot", r"\\server\share\Windows")
        .env("WINDIR", r"\\server\share\Windows")
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "echo helper-shell-derived",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("helper-shell-derived"),
        "stdout was: {} stderr was: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_runs_shell_command_with_workspace_env_expanded_path() {
    let root = TestDir::new("shell-env-path-allow");
    let script_dir = root.path().join("tools");
    std::fs::create_dir_all(&script_dir).unwrap();
    let script_path = script_dir.join("tool.cmd");
    std::fs::write(&script_path, "@echo off\r\necho helper-home-script\r\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "WORKSPACE_TOOL",
            script_path.to_str().unwrap(),
            "--command",
            r"call %WORKSPACE_TOOL%",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!(
        "helper-home-script",
        String::from_utf8_lossy(&output.stdout).trim()
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_with_bang_expanded_workspace_path() {
    let root = TestDir::new("shell-bang-env-path-reject");
    let script_dir = root.path().join("tools");
    std::fs::create_dir_all(&script_dir).unwrap();
    let script_path = script_dir.join("tool.cmd");
    std::fs::write(&script_path, "@echo off\r\necho helper-home-script\r\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "WORKSPACE_TOOL",
            script_path.to_str().unwrap(),
            "--command",
            r"call !WORKSPACE_TOOL!",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("must resolve invoked program under helper-owned PATH roots"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_with_unsafe_batch_argument() {
    let root = TestDir::new("shell-batch-unsafe-arg-reject");
    let script_path = root.path().join("tool.cmd");
    std::fs::write(&script_path, "@echo off\r\necho %1\r\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "WORKSPACE_TOOL",
            script_path.to_str().unwrap(),
            "--setenv",
            "WORKSPACE_FLAG",
            "100%done",
            "--command",
            r#"call %WORKSPACE_TOOL% "%WORKSPACE_FLAG%""#,
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("unsafe batch arguments"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_with_shell_metacharacter_batch_argument() {
    let root = TestDir::new("shell-batch-metachar-arg-reject");
    let script_path = root.path().join("tool.cmd");
    std::fs::write(&script_path, "@echo off\r\necho %1\r\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "WORKSPACE_TOOL",
            script_path.to_str().unwrap(),
            "--command",
            r#"call %WORKSPACE_TOOL% "hello & dir""#,
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("unsafe batch arguments"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_with_bare_batch_program_resolved_from_inherited_path() {
    let root = TestDir::new("shell-path-lookup");
    let bin = root.path().join("bin");
    std::fs::create_dir_all(&bin).unwrap();
    let tool = bin.join("helper-tool.cmd");
    std::fs::write(&tool, "@echo off\r\necho helper-path-tool\r\n").unwrap();
    let inherited_path = std::env::var("PATH").unwrap_or_default();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATH", format!("{};{}", bin.display(), inherited_path))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "helper-tool",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("explicit workspace batch-script paths"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_unknown_env_path_expansion() {
    let root = TestDir::new("shell-env-path-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type %MISSING%\secret.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("shell command path uses unknown environment reference"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
#[cfg(windows)]
fn binary_rejects_shell_command_unresolved_bare_program() {
    let root = TestDir::new("shell-bare-program-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATH", "")
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "missing-helper-program",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("must resolve invoked program under helper-owned PATH roots"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
#[cfg(windows)]
fn binary_rejects_shell_command_internal_workspace_metadata_literal() {
    let root = TestDir::new("shell-internal-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type .ai_ide_runtime\secret.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("internal workspace metadata"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn binary_rejects_nested_shell_in_argv_mode() {
    let root = TestDir::new("nested-shell-argv-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=powershell.exe",
            "--argv=-Command",
            "--argv=echo nope",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("nested shell programs"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn binary_rejects_wsl_in_argv_mode() {
    let root = TestDir::new("nested-wsl-argv-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=wsl.exe",
            "--argv=sh",
            "--argv=-lc",
            "--argv=echo nope",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("nested shell programs"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_unc_program_path() {
    let root = TestDir::new("unc-program-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=\\\\server\\share\\tool.cmd",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("UNC or device form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_nested_shell_in_shell_command_mode() {
    let root = TestDir::new("nested-shell-command-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"powershell -NoLogo -Command echo nope",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("nested shell programs"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_wsl_in_shell_command_mode() {
    let root = TestDir::new("nested-wsl-command-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"wsl.exe sh -lc echo nope",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("nested shell programs"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_start_builtin_in_shell_command_mode() {
    let root = TestDir::new("start-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"start helper.cmd",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("must not use start"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_control_operator_sequence() {
    let root = TestDir::new("shell-control-op-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"echo one && echo two",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("shell control operators"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_mklink_builtin() {
    let root = TestDir::new("shell-mklink-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"mklink linked note.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("filesystem builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_assoc_builtin() {
    let root = TestDir::new("shell-assoc-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"assoc .py=helper",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("stateful shell builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_break_builtin() {
    let root = TestDir::new("shell-break-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "break",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("stateful shell builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_batch_label_control_flow() {
    let root = TestDir::new("shell-batch-label-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "call :again",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("batch-label control flow"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_chcp_builtin() {
    let root = TestDir::new("shell-chcp-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "chcp 65001",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("stateful shell builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_dpath_builtin() {
    let root = TestDir::new("shell-dpath-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"dpath C:\tools",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("stateful shell builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_ftype_builtin() {
    let root = TestDir::new("shell-ftype-builtin-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r#"ftype helper="python" "%1""#,
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("stateful shell builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_redirection_operator() {
    let root = TestDir::new("shell-redirection-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"type note.txt > out.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("shell control operators"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_one_shot_simple_shell_command_prefers_direct_argv_and_blocks_grandchild_spawn() {
    let root = TestDir::new("shell-active-process-limit");
    if !helper_reports_restricted_token_enabled(&root) {
        return;
    }
    let script_path = root.path().join("attempt_spawn.py");
    std::fs::write(
        &script_path,
        concat!(
            "import subprocess, sys, time\n",
            "time.sleep(0.25)\n",
            "try:\n",
            "    subprocess.Popen(['python', '-c', 'import time; time.sleep(0.5)'])\n",
            "except Exception:\n",
            "    print('spawn-blocked')\n",
            "    sys.exit(0)\n",
            "print('spawn-allowed')\n",
            "sys.exit(1)\n",
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "python attempt_spawn.py",
        ])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        "spawn-blocked",
        String::from_utf8_lossy(&output.stdout).trim()
    );
}

#[cfg(windows)]
#[test]
fn binary_one_shot_call_wrapped_native_shell_command_prefers_direct_argv() {
    let root = TestDir::new("shell-call-active-process-limit");
    if !helper_reports_restricted_token_enabled(&root) {
        return;
    }
    let script_path = root.path().join("attempt_spawn.py");
    std::fs::write(
        &script_path,
        concat!(
            "import subprocess, sys, time\n",
            "time.sleep(0.25)\n",
            "try:\n",
            "    subprocess.Popen(['python', '-c', 'import time; time.sleep(0.5)'])\n",
            "except Exception:\n",
            "    print('spawn-blocked')\n",
            "    sys.exit(0)\n",
            "print('spawn-allowed')\n",
            "sys.exit(1)\n",
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "call python attempt_spawn.py",
        ])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        "spawn-blocked",
        String::from_utf8_lossy(&output.stdout).trim()
    );
}

#[cfg(windows)]
#[test]
fn binary_runs_env_expanded_executable_path_via_direct_argv() {
    let root = TestDir::new("shell-env-exe-direct-argv");
    let system_root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_string());
    let source_where_exe = Path::new(&system_root).join("System32").join("where.exe");
    let copied_where_exe = root.path().join("where-copy.exe");
    std::fs::copy(&source_where_exe, &copied_where_exe).unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "WORKSPACE_WHERE",
            copied_where_exe.to_str().unwrap(),
            "--command",
            "%WORKSPACE_WHERE% where-copy.exe",
        ])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stdout)
            .to_ascii_lowercase()
            .contains("where-copy.exe"),
        "stdout was: {} stderr was: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_runs_echo_without_shell_expanding_env_metacharacters() {
    let root = TestDir::new("shell-echo-helper-local");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "WORKSPACE_FLAG",
            "left & right",
            "--command",
            "echo %WORKSPACE_FLAG%",
        ])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        "left & right",
        String::from_utf8_lossy(&output.stdout).trim()
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).trim().is_empty(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_call_wrapped_echo_builtin() {
    let root = TestDir::new("shell-call-echo-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "call echo helper-ok",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("helper-local builtins"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_non_batch_script_file_association_shell_launch() {
    let root = TestDir::new("shell-file-association-reject");
    let script_path = root.path().join("helper.py");
    std::fs::write(&script_path, "print('helper')\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            "helper.py",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("file-association"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_runs_simple_shell_command_with_env_expanded_argument() {
    let root = TestDir::new("shell-env-arg-direct-argv");
    let script_path = root.path().join("print_arg.py");
    std::fs::write(
        &script_path,
        concat!("import sys\n", "print(sys.argv[1])\n",),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "WORKSPACE_FLAG",
            "helper-ok",
            "--command",
            "python print_arg.py %WORKSPACE_FLAG%",
        ])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!("helper-ok", String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
fn binary_rejects_shell_command_that_requires_raw_shell_fallback() {
    let root = TestDir::new("shell-raw-fallback-reject");
    let script_path = root.path().join("print_arg.py");
    std::fs::write(
        &script_path,
        concat!("import sys\n", "print(sys.argv[1])\n",),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "WORKSPACE_FLAG",
            "two words",
            "--command",
            "python print_arg.py %WORKSPACE_FLAG%",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("structured batch launch"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_runs_simple_shell_command_with_quoted_env_expanded_argument() {
    let root = TestDir::new("shell-env-arg-quoted-direct-argv");
    let script_path = root.path().join("print_arg.py");
    std::fs::write(
        &script_path,
        concat!("import sys\n", "print(sys.argv[1])\n",),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "WORKSPACE_FLAG",
            "helper ok",
            "--command",
            "python print_arg.py \"%WORKSPACE_FLAG%\"",
        ])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!("helper ok", String::from_utf8_lossy(&output.stdout).trim());
}

#[test]
fn binary_emulates_fixture_command() {
    let root = TestDir::new("fixture");
    let helper_root = helper_env_root("fixture");
    let fixture_command = "echo __AI_IDE_FIXTURE__ .ai_ide_strict_fixture.txt";
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--command",
            fixture_command,
        ])
        .output()
        .unwrap();

    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(output.status.success());
    assert!(stdout.contains("__AI_IDE_FIXTURE__ sandbox=/workspace"));
    assert!(stdout.contains(&format!(
        "__AI_IDE_FIXTURE__ home={}",
        helper_root.join("home").display()
    )));
    assert!(stdout.contains(&format!(
        "__AI_IDE_FIXTURE__ cache={}",
        helper_root.join("cache").display()
    )));
    assert!(stdout.contains("__AI_IDE_FIXTURE__ boundary_layout=ready"));
    assert!(stdout.contains("__AI_IDE_FIXTURE__ restricted_token="));
    assert!(stdout.contains("__AI_IDE_FIXTURE__ write_boundary="));
    assert!(stdout.contains("__AI_IDE_FIXTURE__ read_boundary="));
    assert!(root.path().join(".ai_ide_strict_fixture.txt").exists());
}

#[test]
fn binary_process_mode_answers_status_and_accepts_kill_control() {
    let root = TestDir::new("process");
    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    let control_file = root.path().join("control.json");
    let response_file = root.path().join("status.json");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--control-file",
            control_file.to_str().unwrap(),
            "--response-file",
            response_file.to_str().unwrap(),
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    for arg in blocking_child_argv() {
        command.arg(format!("--argv={arg}"));
    }
    let mut child = command.spawn().unwrap();

    write_helper_control_message(
        &control_file,
        &WindowsStrictHelperControlMessage {
            version: 1,
            command: HelperControlCommand::Status,
            request_id: Some("req-1".to_string()),
            run_id: Some("run-1".to_string()),
            backend: Some("restricted-host-helper".to_string()),
        },
    )
    .unwrap();

    let status = wait_for_status(&response_file).unwrap();
    assert_eq!(Some("req-1"), status.request_id.as_deref());
    assert_eq!(Some("run-1"), status.run_id.as_deref());
    assert_eq!(Some("restricted-host-helper"), status.backend.as_deref());

    write_helper_control_message(
        &control_file,
        &WindowsStrictHelperControlMessage {
            version: 1,
            command: HelperControlCommand::Kill,
            request_id: Some("req-2".to_string()),
            run_id: Some("run-1".to_string()),
            backend: Some("restricted-host-helper".to_string()),
        },
    )
    .unwrap();

    let exited = child.wait_timeout(Duration::from_secs(5)).unwrap();
    assert!(exited, "helper process did not exit after kill control");
}

#[test]
fn binary_runs_one_shot_argv_and_forwards_output() {
    let root = TestDir::new("oneshot-argv");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            "--argv=-c",
            "--argv=print('helper-one-shot-argv')",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("helper-one-shot-argv"),
        "stdout was: {}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[cfg(windows)]
#[test]
fn binary_runs_explicit_workspace_program_path() {
    let root = TestDir::new("workspace-program");
    let script_path = root.path().join("workspace-tool.cmd");
    std::fs::write(&script_path, "@echo off\r\necho workspace-program\r\n").unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command.args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
    ]);
    command.arg(format!("--argv={}", script_path.display()));
    let output = command.output().unwrap();

    assert!(output.status.success());
    assert_eq!(
        "workspace-program",
        String::from_utf8_lossy(&output.stdout).trim()
    );
}

#[cfg(windows)]
#[test]
fn binary_runs_python_with_workspace_script_argument() {
    let root = TestDir::new("workspace-script-arg");
    let script_path = root.path().join("workspace_script.py");
    std::fs::write(&script_path, "print('workspace-script-arg')\n").unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command.args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
        "--argv=python",
    ]);
    command.arg(format!("--argv={}", script_path.display()));
    let output = command.output().unwrap();

    assert!(output.status.success());
    assert_eq!(
        "workspace-script-arg",
        String::from_utf8_lossy(&output.stdout).trim()
    );
}

#[cfg(windows)]
#[test]
fn binary_one_shot_argv_hides_internal_workspace_roots_during_launch() {
    let root = TestDir::new("workspace-boundary-hide-internal");
    let internal_root = root.path().join(".ai_ide_runtime");
    std::fs::create_dir_all(&internal_root).unwrap();
    std::fs::write(internal_root.join("secret.txt"), b"hidden").unwrap();
    let script_path = root.path().join("probe_internal_root.py");
    std::fs::write(
        &script_path,
        r#"from pathlib import Path
target = Path(".ai_ide_runtime") / "secret.txt"
try:
    print(target.read_text())
except FileNotFoundError:
    print("internal-root-hidden")
"#,
    )
    .unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command.args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
        "--argv=python",
    ]);
    command.arg(format!("--argv={}", script_path.display()));
    let output = command.output().unwrap();

    assert!(output.status.success());
    assert_eq!(
        "internal-root-hidden",
        String::from_utf8_lossy(&output.stdout).trim()
    );
    assert!(internal_root.join("secret.txt").exists());
}

#[cfg(windows)]
#[test]
fn binary_stdio_proxy_blocks_guessed_hidden_internal_root_reads_under_restricted_boundary() {
    let root = TestDir::new("workspace-boundary-hidden-root-read-guard");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
    {
        return;
    }

    let internal_root = root.path().join(".ai_ide_runtime");
    std::fs::create_dir_all(&internal_root).unwrap();
    std::fs::write(internal_root.join("secret.txt"), b"hidden").unwrap();
    let script_path = root.path().join("probe_hidden_root_read.py");
    std::fs::write(
        &script_path,
        r#"import sys
from pathlib import Path

target = Path(sys.stdin.readline().strip())
try:
    print(target.read_text())
except PermissionError:
    print("internal-hidden-read-blocked")
except FileNotFoundError:
    print("internal-hidden-read-missing")
"#,
    )
    .unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut helper = command.spawn().unwrap();
    let hidden_path = root
        .path()
        .join(format!(".ai_ide_hidden_.ai_ide_runtime_{}_0", helper.id()))
        .join("secret.txt");
    {
        let mut stdin = helper.stdin.take().unwrap();
        use std::io::Write as _;
        writeln!(stdin, "{}", hidden_path.display()).unwrap();
    }
    let output = helper.wait_with_output().unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        "internal-hidden-read-blocked",
        String::from_utf8_lossy(&output.stdout).trim(),
        "stdout was: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(internal_root.join("secret.txt").exists());
}

#[cfg(windows)]
#[test]
fn binary_one_shot_argv_hides_internal_workspace_roots_from_subdir_cwd() {
    let root = TestDir::new("workspace-boundary-hide-internal-subdir");
    let subdir = root.path().join("subdir");
    std::fs::create_dir_all(&subdir).unwrap();
    let internal_root = root.path().join(".ai_ide_runtime");
    std::fs::create_dir_all(&internal_root).unwrap();
    std::fs::write(internal_root.join("secret.txt"), b"hidden").unwrap();
    let script_path = subdir.join("probe_internal_root.py");
    std::fs::write(
        &script_path,
        r#"from pathlib import Path
target = Path("..") / ".ai_ide_runtime" / "secret.txt"
try:
    print(target.read_text())
except FileNotFoundError:
    print("internal-root-hidden")
"#,
    )
    .unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command.args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace/subdir",
        "--argv=python",
    ]);
    command.arg(format!("--argv={}", script_path.display()));
    let output = command.output().unwrap();

    assert!(output.status.success());
    assert_eq!(
        "internal-root-hidden",
        String::from_utf8_lossy(&output.stdout).trim()
    );
    assert!(internal_root.join("secret.txt").exists());
}

#[cfg(windows)]
#[test]
fn binary_one_shot_argv_runs_under_restricted_token_when_available() {
    let root = TestDir::new("workspace-boundary-restricted-token");
    if !helper_reports_restricted_token_enabled(&root) {
        return;
    }

    let script_path = root.path().join("probe_restricted_token.py");
    std::fs::write(
        &script_path,
        r#"import ctypes
from ctypes import wintypes

advapi = ctypes.windll.advapi32
kernel = ctypes.windll.kernel32
TOKEN_QUERY = 0x0008
token = wintypes.HANDLE()
if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
    raise OSError(ctypes.get_last_error())
try:
    print("restricted" if advapi.IsTokenRestricted(token) else "unrestricted")
finally:
    kernel.CloseHandle(token)
"#,
    )
    .unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command.args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
        "--argv=python",
    ]);
    command.arg(format!("--argv={}", script_path.display()));
    let output = command.output().unwrap();

    assert!(output.status.success());
    assert_eq!("restricted", String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
fn binary_one_shot_structured_batch_hides_internal_workspace_roots_during_launch() {
    let root = TestDir::new("workspace-boundary-hide-internal-batch");
    let internal_root = root.path().join(".ai_ide_runtime");
    std::fs::create_dir_all(&internal_root).unwrap();
    std::fs::write(internal_root.join("secret.txt"), b"hidden").unwrap();
    let script_path = root.path().join("probe_internal_root.cmd");
    std::fs::write(
        &script_path,
        "@echo off\r\nif exist .ai_ide_runtime\\secret.txt (\r\n  echo internal-root-visible\r\n) else (\r\n  echo internal-root-hidden\r\n)\r\n",
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"call .\probe_internal_root.cmd",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!(
        "internal-root-hidden",
        String::from_utf8_lossy(&output.stdout).trim()
    );
    assert!(internal_root.join("secret.txt").exists());
}

#[cfg(windows)]
#[test]
fn binary_one_shot_structured_batch_runs_under_restricted_token_when_available() {
    let root = TestDir::new("workspace-boundary-batch-restricted-token");
    if !helper_reports_restricted_token_enabled(&root) {
        return;
    }

    let probe_script = root.path().join("probe_restricted_token.py");
    std::fs::write(
        &probe_script,
        r#"import ctypes
from ctypes import wintypes

advapi = ctypes.windll.advapi32
kernel = ctypes.windll.kernel32
TOKEN_QUERY = 0x0008
token = wintypes.HANDLE()
if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
    raise OSError(ctypes.get_last_error())
try:
    print("restricted" if advapi.IsTokenRestricted(token) else "unrestricted")
finally:
    kernel.CloseHandle(token)
"#,
    )
    .unwrap();
    let batch_script = root.path().join("probe_restricted_token.cmd");
    std::fs::write(
        &batch_script,
        format!("@echo off\r\npython \"{}\"\r\n", probe_script.display()),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--command",
            r"call .\probe_restricted_token.cmd",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!("restricted", String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
fn binary_stdio_proxy_hides_internal_workspace_roots_during_launch() {
    let root = TestDir::new("workspace-boundary-hide-internal-stdio");
    let internal_root = root.path().join(".ai_ide_runtime");
    std::fs::create_dir_all(&internal_root).unwrap();
    std::fs::write(internal_root.join("secret.txt"), b"hidden").unwrap();
    let script_path = root.path().join("probe_internal_root.py");
    std::fs::write(
        &script_path,
        r#"from pathlib import Path
target = Path(".ai_ide_runtime") / "secret.txt"
try:
    print(target.read_text())
except FileNotFoundError:
    print("internal-root-hidden")
"#,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!(
        "internal-root-hidden",
        String::from_utf8_lossy(&output.stdout).trim()
    );
    assert!(internal_root.join("secret.txt").exists());
}

#[cfg(windows)]
#[test]
fn binary_stdio_proxy_argv_runs_under_restricted_token_when_available() {
    let root = TestDir::new("workspace-boundary-stdio-restricted-token");
    if !helper_reports_restricted_token_enabled(&root) {
        return;
    }

    let script_path = root.path().join("probe_restricted_token.py");
    std::fs::write(
        &script_path,
        r#"import ctypes
from ctypes import wintypes

advapi = ctypes.windll.advapi32
kernel = ctypes.windll.kernel32
TOKEN_QUERY = 0x0008
token = wintypes.HANDLE()
if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
    raise OSError(ctypes.get_last_error())
try:
    print("restricted" if advapi.IsTokenRestricted(token) else "unrestricted")
finally:
    kernel.CloseHandle(token)
"#,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!("restricted", String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
fn binary_stdio_proxy_direct_shell_argv_runs_under_restricted_token_when_available() {
    let root = TestDir::new("workspace-boundary-stdio-shell-restricted-token");
    if !helper_reports_restricted_token_enabled(&root) {
        return;
    }

    let script_path = root.path().join("probe_restricted_token.py");
    std::fs::write(
        &script_path,
        r#"import ctypes
from ctypes import wintypes

advapi = ctypes.windll.advapi32
kernel = ctypes.windll.kernel32
TOKEN_QUERY = 0x0008
token = wintypes.HANDLE()
if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
    raise OSError(ctypes.get_last_error())
try:
    print("restricted" if advapi.IsTokenRestricted(token) else "unrestricted")
finally:
    kernel.CloseHandle(token)
"#,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--command",
            &format!(r#"python "{}""#, script_path.display()),
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!("restricted", String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
fn binary_stdio_proxy_structured_batch_runs_under_restricted_token_when_available() {
    let root = TestDir::new("workspace-boundary-stdio-batch-restricted-token");
    if !helper_reports_restricted_token_enabled(&root) {
        return;
    }

    let probe_script = root.path().join("probe_restricted_token.py");
    std::fs::write(
        &probe_script,
        r#"import ctypes
from ctypes import wintypes

advapi = ctypes.windll.advapi32
kernel = ctypes.windll.kernel32
TOKEN_QUERY = 0x0008
token = wintypes.HANDLE()
if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
    raise OSError(ctypes.get_last_error())
try:
    print("restricted" if advapi.IsTokenRestricted(token) else "unrestricted")
finally:
    kernel.CloseHandle(token)
"#,
    )
    .unwrap();
    let batch_script = root.path().join("probe_restricted_token.cmd");
    std::fs::write(
        &batch_script,
        format!("@echo off\r\npython \"{}\"\r\n", probe_script.display()),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--command",
            r"call .\probe_restricted_token.cmd",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!("restricted", String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_allows_workspace_write_and_blocks_outside_write() {
    let root = TestDir::new("workspace-boundary-write-slice");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
    {
        return;
    }

    let script_path = root.path().join("probe_write_boundary.py");
    std::fs::write(
        &script_path,
        r#"from pathlib import Path

workspace = Path.cwd()
allowed = workspace / "allowed.txt"
outside = Path(__file__).resolve().parent.parent / "outside-write.txt"

allowed.write_text("allowed")
try:
    outside.write_text("blocked")
except Exception:
    print("outside=blocked")
else:
    print("outside=allowed")

print(f"allowed={'yes' if allowed.exists() else 'no'}")
"#,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("outside=blocked"), "stdout was: {stdout}");
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(root.path().join("allowed.txt").exists());
    assert!(
        !root
            .path()
            .parent()
            .unwrap()
            .join("outside-write.txt")
            .exists()
    );
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_allows_workspace_read_and_blocks_blocked_read_root() {
    let root = TestDir::new("workspace-boundary-read-slice");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    let blocked_root = helper_root.join("blocked_read");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::create_dir_all(&blocked_root).unwrap();
    let blocked_file = blocked_root.join("secret.txt");
    std::fs::write(&blocked_file, b"blocked").unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();

    let script_path = root.path().join("probe_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = blocked_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(blocked_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_selected_external_read_root() {
    let root = TestDir::new("workspace-boundary-external-read-slice");
    let blocked_host = TestDir::new("workspace-boundary-external-read-host");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();
    let blocked_file = blocked_host.path().join("secret.txt");
    std::fs::write(&blocked_file, b"blocked").unwrap();

    let script_path = root.path().join("probe_external_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = blocked_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_host.path().to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(blocked_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_external_directory_enumeration() {
    let root = TestDir::new("workspace-boundary-external-enumeration-slice");
    let blocked_host = TestDir::new("workspace-boundary-external-enumeration-host");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();
    std::fs::write(blocked_host.path().join("secret.txt"), b"blocked").unwrap();

    let script_path = root.path().join("probe_external_enumeration_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"import os
from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    list(blocked.iterdir())
except PermissionError:
    print("iterdir=denied")
except FileNotFoundError:
    print("iterdir=missing")
except OSError as error:
    print(f"iterdir=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("iterdir=allowed")

try:
    list(blocked.rglob("*"))
except PermissionError:
    print("rglob=denied")
except FileNotFoundError:
    print("rglob=missing")
except OSError as error:
    print(f"rglob=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("rglob=allowed")

walk_errors = []

def onerror(error):
    walk_errors.append(getattr(error, "winerror", None))

entries = list(os.walk(blocked, onerror=onerror))
if walk_errors:
    print(f"walk=denied:{{walk_errors[0]}}")
elif entries:
    print("walk=allowed")
else:
    print("walk=empty")
"#,
            blocked = blocked_host.path().display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_host.path().to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("iterdir=denied") || stdout.contains("iterdir=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(
        stdout.contains("rglob=denied") || stdout.contains("rglob=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(stdout.contains("walk=denied"), "stdout was: {stdout}");
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_scopes_path_to_active_executable_root() {
    let root = TestDir::new("workspace-boundary-scoped-path-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-scoped-path-tool");
    let active_bin = tool_root.path().join("active").join("bin");
    let inactive_bin = tool_root.path().join("inactive").join("bin");
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(&inactive_bin).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let system_root = PathBuf::from(&real_system_root);
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "PATH",
            std::env::join_paths([active_bin.as_path(), inactive_bin.as_path()])
                .unwrap()
                .to_str()
                .unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=echo")
        .arg("--argv=%PATH%")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains(active_bin.to_str().unwrap()),
        "stdout was: {stdout}"
    );
    assert!(
        !stdout.contains(inactive_bin.to_str().unwrap()),
        "stdout was: {stdout}"
    );
    assert!(
        !stdout.contains(system_root.to_str().unwrap()),
        "stdout was: {stdout}"
    );
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_scopes_pathext_to_native_extensions_for_active_executable_root() {
    let root = TestDir::new("workspace-boundary-scoped-pathext-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-scoped-pathext-tool");
    let active_bin = tool_root.path().join("active").join("bin");
    std::fs::create_dir_all(&active_bin).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "PATHEXT",
            ".JS;.EXE;.BAT;.VBS;.CMD",
            "--argv=cmd",
            "--argv=/D",
            "--argv=/C",
            "--argv=echo %PATHEXT%",
        ])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(".COM;.EXE", String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_non_native_leaf_files_inside_external_active_root() {
    let root = TestDir::new("workspace-boundary-active-root-leaf-read-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-leaf-read-tool");
    let active_bin = tool_root.path().join("active").join("bin");
    std::fs::create_dir_all(&active_bin).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    let private_file = active_bin.join("private.txt");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    std::fs::write(&private_file, b"secret").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_BIN",
            active_bin.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ACTIVE_BIN%\\\\private.txt\"")
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_keeps_python_runtime_metadata_visible_inside_external_active_root() {
    let root = TestDir::new("workspace-boundary-active-root-python-metadata-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-python-metadata-tool");
    let active_bin = tool_root.path().join("active").join("bin");
    std::fs::create_dir_all(&active_bin).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    let python_pth = active_bin
        .parent()
        .expect("active bin parent")
        .join("python311._pth");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    std::fs::write(&python_pth, b".\\Lib\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            python_pth.parent().unwrap().to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ACTIVE_TOOL_ROOT%\\\\python311._pth\"")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(".\\Lib", String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_keeps_python_runtime_zip_visible_inside_external_active_root() {
    let root = TestDir::new("workspace-boundary-active-root-python-zip-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-python-zip-tool");
    let active_bin = tool_root.path().join("active").join("bin");
    std::fs::create_dir_all(&active_bin).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    let python_zip = active_bin
        .parent()
        .expect("active bin parent")
        .join("python311.zip");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    std::fs::write(&python_zip, b"zip-runtime").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            python_zip.parent().unwrap().to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ACTIVE_TOOL_ROOT%\\\\python311.zip\"")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        "zip-runtime",
        String::from_utf8_lossy(&output.stdout).trim()
    );
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_keeps_runtime_subtree_visible_inside_external_active_root() {
    let root = TestDir::new("workspace-boundary-active-root-runtime-subtree-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-runtime-subtree-tool");
    let active_bin = tool_root.path().join("active").join("bin");
    let active_tool_root = active_bin.parent().expect("active bin parent");
    let runtime_dirs = [
        active_tool_root.join("Lib"),
        active_tool_root.join("DLLs"),
        active_tool_root.join("share"),
        active_tool_root.join("libexec"),
    ];
    std::fs::create_dir_all(&active_bin).unwrap();
    for runtime_dir in &runtime_dirs {
        std::fs::create_dir_all(runtime_dir).unwrap();
    }
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    for runtime_dir in &runtime_dirs {
        std::fs::write(runtime_dir.join("runtime.txt"), b"runtime-subtree").unwrap();
    }

    for runtime_dir in ["Lib", "DLLs", "share", "libexec"] {
        let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
            .args([
                "--workspace",
                root.path().to_str().unwrap(),
                "--cwd",
                "/workspace",
                "--setenv",
                "AI_IDE_SANDBOX_ROOT",
                "/workspace",
                "--setenv",
                "HOME",
                helper_home.to_str().unwrap(),
                "--setenv",
                "XDG_CACHE_HOME",
                helper_cache.to_str().unwrap(),
                "--setenv",
                "TMPDIR",
                helper_tmp.to_str().unwrap(),
                "--setenv",
                "ACTIVE_TOOL_ROOT",
                active_tool_root.to_str().unwrap(),
            ])
            .arg(format!("--argv={}", copied_cmd.display()))
            .arg("--argv=/D")
            .arg("--argv=/C")
            .arg(format!(
                "--argv=type \"%ACTIVE_TOOL_ROOT%\\\\{runtime_dir}\\\\runtime.txt\""
            ))
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "stdout was: {}, stderr was: {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(
            "runtime-subtree",
            String::from_utf8_lossy(&output.stdout).trim()
        );
    }
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_parent_leaf_files_inside_external_active_root() {
    let root = TestDir::new("workspace-boundary-active-root-parent-leaf-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-parent-leaf-tool");
    let active_bin = tool_root.path().join("active").join("bin");
    std::fs::create_dir_all(&active_bin).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    let private_file = active_bin
        .parent()
        .expect("active bin parent")
        .join("private.txt");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    std::fs::write(&private_file, b"secret").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            private_file.parent().unwrap().to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ACTIVE_TOOL_ROOT%\\\\private.txt\"")
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_user_local_program_siblings_inside_external_active_root() {
    let base = TestDir::new("workspace-boundary-user-local-program-siblings-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let sibling_root = programs_root.join("NodeJS");
    let sibling_secret = sibling_root.join("secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(&sibling_root).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_user_profile_common_dirs_for_external_active_root() {
    let base = TestDir::new("workspace-boundary-user-profile-common-dirs-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let downloads_root = owner_root.join("Downloads");
    let sibling_secret = downloads_root.join("secret.txt");
    let roaming_root = owner_root.join("AppData").join("Roaming");
    let roaming_secret = roaming_root.join("roaming-secret.txt");
    let packages_root = owner_root.join("AppData").join("Local").join("Packages");
    let packages_secret = packages_root.join("packages-secret.txt");
    let local_root = owner_root.join("AppData").join("Local");
    let local_secret = local_root.join("local-secret.txt");
    let local_temp_root = owner_root.join("AppData").join("Local").join("Temp");
    let local_temp_secret = local_temp_root.join("temp-secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&downloads_root).unwrap();
    std::fs::create_dir_all(&roaming_root).unwrap();
    std::fs::create_dir_all(&packages_root).unwrap();
    std::fs::create_dir_all(&local_temp_root).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();
    std::fs::write(&roaming_secret, b"secret").unwrap();
    std::fs::write(&packages_secret, b"secret").unwrap();
    std::fs::write(&local_secret, b"secret").unwrap();
    std::fs::write(&local_temp_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );

    let roaming_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "ROAMING_SECRET",
            roaming_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ROAMING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !roaming_output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&roaming_output.stdout),
        String::from_utf8_lossy(&roaming_output.stderr)
    );
    let roaming_combined = format!(
        "{}{}",
        String::from_utf8_lossy(&roaming_output.stdout),
        String::from_utf8_lossy(&roaming_output.stderr)
    );
    assert!(
        roaming_combined
            .to_ascii_lowercase()
            .contains("access is denied"),
        "output was: {roaming_combined}"
    );

    let packages_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "PACKAGES_SECRET",
            packages_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%PACKAGES_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !packages_output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&packages_output.stdout),
        String::from_utf8_lossy(&packages_output.stderr)
    );
    let packages_combined = format!(
        "{}{}",
        String::from_utf8_lossy(&packages_output.stdout),
        String::from_utf8_lossy(&packages_output.stderr)
    );
    assert!(
        packages_combined
            .to_ascii_lowercase()
            .contains("access is denied"),
        "output was: {packages_combined}"
    );

    let local_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "LOCAL_SECRET",
            local_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%LOCAL_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !local_output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&local_output.stdout),
        String::from_utf8_lossy(&local_output.stderr)
    );
    let local_combined = format!(
        "{}{}",
        String::from_utf8_lossy(&local_output.stdout),
        String::from_utf8_lossy(&local_output.stderr)
    );
    assert!(
        local_combined
            .to_ascii_lowercase()
            .contains("access is denied"),
        "output was: {local_combined}"
    );

    let local_temp_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "LOCAL_TEMP_SECRET",
            local_temp_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%LOCAL_TEMP_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !local_temp_output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&local_temp_output.stdout),
        String::from_utf8_lossy(&local_temp_output.stderr)
    );
    let local_temp_combined = format!(
        "{}{}",
        String::from_utf8_lossy(&local_temp_output.stdout),
        String::from_utf8_lossy(&local_temp_output.stderr)
    );
    assert!(
        local_temp_combined
            .to_ascii_lowercase()
            .contains("access is denied"),
        "output was: {local_temp_combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_workspace_only_scope_keeps_user_profile_common_dirs_visible_for_external_active_root() {
    let base = TestDir::new("workspace-boundary-user-profile-common-dirs-workspace-only");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let downloads_root = owner_root.join("Downloads");
    let sibling_secret = downloads_root.join("secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&downloads_root).unwrap();
    std::fs::write(&sibling_secret, b"visible-from-workspace-scope").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "AI_IDE_BOUNDARY_SCOPE",
            "workspace-only",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.contains("visible-from-workspace-scope"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_workspace_only_scope_hides_workspace_internal_roots_but_keeps_host_surface_visible() {
    let base = TestDir::new("workspace-boundary-workspace-only-internal-and-host");
    if !helper_reports_read_boundary_enabled(&base) {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let downloads_root = owner_root.join("Downloads");
    let sibling_secret = downloads_root.join("secret.txt");
    let internal_root = workspace.join(".ai_ide_runtime");
    let batch_path = workspace.join("probe_workspace_only.cmd");

    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&downloads_root).unwrap();
    std::fs::create_dir_all(&internal_root).unwrap();
    std::fs::write(&sibling_secret, b"visible-from-workspace-scope").unwrap();
    std::fs::write(internal_root.join("secret.txt"), b"hidden").unwrap();
    std::fs::write(
        &batch_path,
        "@echo off\r\nif exist .ai_ide_runtime\\secret.txt (\r\n  echo internal-visible\r\n) else (\r\n  echo internal-hidden\r\n)\r\nfor /f \"usebackq delims=\" %%L in (`type \"%SIBLING_SECRET%\"`) do echo host=%%L\r\n",
    )
    .unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "AI_IDE_BOUNDARY_SCOPE",
            "workspace-only",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg(format!("--argv={}", batch_path.display()))
        .output()
        .expect("run helper");

    assert!(
        output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.contains("internal-hidden"),
        "output was: {combined}"
    );
    assert!(
        combined.contains("host=visible-from-workspace-scope"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
fn run_root_level_workspace_only_type_probe(
    base_name: &str,
    visible_secret_path: impl FnOnce(&Path, &Path) -> PathBuf,
    env_name: &str,
    secret_contents: &str,
) -> Option<String> {
    let base = TestDir::new(base_name);
    if !helper_reports_read_boundary_enabled(&base) {
        return None;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let tool_root = drive_root.join("Tools").join("Python311");
    let visible_secret = visible_secret_path(&drive_root, &owner_root);

    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&tool_root).unwrap();
    std::fs::create_dir_all(
        visible_secret
            .parent()
            .expect("visible secret parent directory"),
    )
    .unwrap();
    std::fs::write(&visible_secret, secret_contents).unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = tool_root.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "AI_IDE_BOUNDARY_SCOPE",
            "workspace-only",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            env_name,
            visible_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg(format!("--argv=type \"%{env_name}%\""))
        .output()
        .expect("run helper");

    assert!(
        output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Some(format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    ))
}

#[cfg(windows)]
#[test]
fn binary_workspace_only_scope_keeps_host_surface_visible_for_root_level_external_active_root() {
    let Some(combined) = run_root_level_workspace_only_type_probe(
        "workspace-boundary-root-level-workspace-only-host-visible",
        |_, owner_root| owner_root.join("Downloads").join("visible.txt"),
        "SIBLING_SECRET",
        "visible-from-root-level-workspace-scope",
    ) else {
        return;
    };
    assert!(
        combined.contains("visible-from-root-level-workspace-scope"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_workspace_only_scope_hides_workspace_internal_roots_for_root_level_external_active_root()
{
    let base = TestDir::new("workspace-boundary-root-level-workspace-only-internal-and-host");
    if !helper_reports_read_boundary_enabled(&base) {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let tool_root = drive_root.join("Tools").join("Python311");
    let downloads_root = owner_root.join("Downloads");
    let sibling_secret = downloads_root.join("visible.txt");
    let internal_root = workspace.join(".ai_ide_runtime");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&tool_root).unwrap();
    std::fs::create_dir_all(&downloads_root).unwrap();
    std::fs::create_dir_all(&internal_root).unwrap();
    std::fs::write(&sibling_secret, b"visible-from-root-level-workspace-scope").unwrap();
    std::fs::write(internal_root.join("secret.txt"), b"hidden").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = tool_root.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "AI_IDE_BOUNDARY_SCOPE",
            "workspace-only",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=if exist .ai_ide_runtime\\secret.txt (echo internal-visible) else (echo internal-hidden) & type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.contains("internal-hidden"),
        "output was: {combined}"
    );
    assert!(
        combined.contains("host=visible-from-root-level-workspace-scope"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_workspace_only_scope_keeps_programdata_visible_for_root_level_external_active_root() {
    let Some(combined) = run_root_level_workspace_only_type_probe(
        "workspace-boundary-root-level-workspace-only-programdata-visible",
        |drive_root, _| drive_root.join("ProgramData").join("visible.txt"),
        "PROGRAMDATA_SECRET",
        "visible-from-programdata-workspace-scope",
    ) else {
        return;
    };
    assert!(
        combined.contains("visible-from-programdata-workspace-scope"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_workspace_only_scope_keeps_program_files_visible_for_root_level_external_active_root() {
    let Some(combined) = run_root_level_workspace_only_type_probe(
        "workspace-boundary-root-level-workspace-only-program-files-visible",
        |drive_root, _| drive_root.join("Program Files").join("visible.txt"),
        "PROGRAM_FILES_SECRET",
        "visible-from-program-files-workspace-scope",
    ) else {
        return;
    };
    assert!(
        combined.contains("visible-from-program-files-workspace-scope"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_allowed_user_profile_local_appdata_child_siblings_for_external_active_root()
 {
    let base = TestDir::new("workspace-boundary-user-profile-local-appdata-child-scope-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let packages_dir = owner_root.join("AppData").join("Local").join("Packages");
    let sibling_secret = packages_dir.join("secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&packages_dir).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_user_profile_leaf_files_for_external_active_root() {
    let base = TestDir::new("workspace-boundary-user-profile-leaf-files-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let profile_secret = owner_root.join("profile-secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::write(&profile_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "PROFILE_SECRET",
            profile_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%PROFILE_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_user_profile_parent_leaf_files_for_external_active_root() {
    let base = TestDir::new("workspace-boundary-user-profile-parent-leaf-files-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("Users");
    let owner_root = users_root.join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let users_secret = users_root.join("users-secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::write(&users_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "USERS_SECRET",
            users_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%USERS_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_user_profile_parent_known_dirs_for_external_active_root() {
    let base = TestDir::new("workspace-boundary-user-profile-parent-known-dirs-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("Users");
    let owner_root = users_root.join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let public_root = users_root.join("Public");
    let public_secret = public_root.join("secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&public_root).unwrap();
    std::fs::write(&public_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "PUBLIC_SECRET",
            public_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%PUBLIC_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_programdata_for_external_active_root() {
    let base = TestDir::new("workspace-boundary-programdata-external-active-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let program_data_root = drive_root.join("ProgramData");
    let sibling_secret = program_data_root.join("secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&program_data_root).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "PROGRAMDATA_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%PROGRAMDATA_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_users_for_external_active_root_without_user_allowed_scope() {
    let base = TestDir::new("workspace-boundary-users-external-active-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let workspace = drive_root.join("workspace");
    let helper_root = drive_root.join("helper");
    let tool_root = drive_root.join("Tools").join("Python311");
    let active_bin = tool_root.join("Scripts");
    let public_root = drive_root.join("Users").join("Public");
    let sibling_secret = public_root.join("secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&public_root).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "PUBLIC_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%PUBLIC_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_user_profile_direct_subdirs_for_external_active_root() {
    let base = TestDir::new("workspace-boundary-user-profile-direct-subdirs-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let private_dir = owner_root.join("private-data");
    let sibling_secret = private_dir.join("secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&private_dir).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_allowed_user_profile_common_child_siblings_for_external_active_root()
 {
    let base = TestDir::new("workspace-boundary-user-profile-common-child-scope-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let owner_root = drive_root.join("Users").join("owner");
    let workspace = owner_root.join("Documents").join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let programs_root = owner_root.join("AppData").join("Local").join("Programs");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let private_dir = owner_root.join("Documents").join("private-data");
    let sibling_secret = private_dir.join("secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&private_dir).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_program_files_siblings_inside_external_active_root() {
    let base = TestDir::new("workspace-boundary-program-files-siblings-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let workspace = drive_root.join("workspace");
    let helper_root = drive_root
        .join("Users")
        .join("owner")
        .join("AppData")
        .join("helper");
    let programs_root = drive_root.join("Program Files");
    let tool_root = programs_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let sibling_root = programs_root.join("NodeJS");
    let sibling_secret = sibling_root.join("secret.txt");
    let programs_secret = programs_root.join("program-files-secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(&sibling_root).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();
    std::fs::write(&programs_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );

    let leaf_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "PROGRAMS_SECRET",
            programs_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%PROGRAMS_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !leaf_output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&leaf_output.stdout),
        String::from_utf8_lossy(&leaf_output.stderr)
    );
    let leaf_combined = format!(
        "{}{}",
        String::from_utf8_lossy(&leaf_output.stdout),
        String::from_utf8_lossy(&leaf_output.stderr)
    );
    assert!(
        leaf_combined
            .to_ascii_lowercase()
            .contains("access is denied"),
        "output was: {leaf_combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_custom_tool_siblings_for_scripts_active_root() {
    let base = TestDir::new("workspace-boundary-custom-tool-siblings-root");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let workspace = drive_root.join("workspace");
    let helper_root = drive_root
        .join("Users")
        .join("owner")
        .join("AppData")
        .join("helper");
    let tools_root = drive_root.join("Tools");
    let tool_root = tools_root.join("Python311");
    let active_bin = tool_root.join("Scripts");
    let sibling_root = tools_root.join("NodeJS");
    let sibling_secret = sibling_root.join("secret.txt");
    let tools_secret = tools_root.join("tools-secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
    std::fs::create_dir_all(&sibling_root).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();
    std::fs::write(&tools_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );

    let leaf_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "TOOLS_SECRET",
            tools_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%TOOLS_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !leaf_output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&leaf_output.stdout),
        String::from_utf8_lossy(&leaf_output.stderr)
    );
    let leaf_combined = format!(
        "{}{}",
        String::from_utf8_lossy(&leaf_output.stdout),
        String::from_utf8_lossy(&leaf_output.stderr)
    );
    assert!(
        leaf_combined
            .to_ascii_lowercase()
            .contains("access is denied"),
        "output was: {leaf_combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_custom_tool_siblings_for_root_level_active_root() {
    let base = TestDir::new("workspace-boundary-custom-tool-root-level-siblings");
    if !helper_reports_restricted_token_enabled(&base)
        || !helper_reports_write_boundary_enabled(&base)
        || !helper_reports_read_boundary_enabled(&base)
    {
        return;
    }

    let drive_root = base.path().join("drive");
    let workspace = drive_root.join("workspace");
    let helper_root = drive_root
        .join("Users")
        .join("owner")
        .join("AppData")
        .join("helper");
    let tools_root = drive_root.join("Tools");
    let active_tool = tools_root.join("Python311");
    let sibling_root = tools_root.join("NodeJS");
    let sibling_secret = sibling_root.join("secret.txt");
    let tools_secret = tools_root.join("tools-secret.txt");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(helper_root.join("home")).unwrap();
    std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
    std::fs::create_dir_all(helper_root.join("cache")).unwrap();
    std::fs::create_dir_all(&active_tool).unwrap();
    std::fs::create_dir_all(active_tool.join("Lib")).unwrap();
    std::fs::create_dir_all(&sibling_root).unwrap();
    std::fs::write(&sibling_secret, b"secret").unwrap();
    std::fs::write(&tools_secret, b"secret").unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_tool.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "SIBLING_SECRET",
            sibling_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%SIBLING_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );

    let leaf_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "TOOLS_SECRET",
            tools_secret.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%TOOLS_SECRET%\"")
        .output()
        .expect("run helper");

    assert!(
        !leaf_output.status.success(),
        "stdout: {}, stderr: {}",
        String::from_utf8_lossy(&leaf_output.stdout),
        String::from_utf8_lossy(&leaf_output.stderr)
    );
    let leaf_combined = format!(
        "{}{}",
        String::from_utf8_lossy(&leaf_output.stdout),
        String::from_utf8_lossy(&leaf_output.stderr)
    );
    assert!(
        leaf_combined
            .to_ascii_lowercase()
            .contains("access is denied"),
        "output was: {leaf_combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_parent_subdirs_inside_external_active_root() {
    let root = TestDir::new("workspace-boundary-active-root-parent-subdir-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-parent-subdir-tool");
    let active_bin = tool_root.path().join("active").join("bin");
    let private_dir = active_bin
        .parent()
        .expect("active bin parent")
        .join("private");
    std::fs::create_dir_all(&active_bin).unwrap();
    std::fs::create_dir_all(&private_dir).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    std::fs::write(private_dir.join("secret.txt"), b"secret").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            private_dir.parent().unwrap().to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=dir /b \"%ACTIVE_TOOL_ROOT%\\\\private\"")
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_keeps_virtualenv_metadata_visible_inside_external_active_root() {
    let root = TestDir::new("workspace-boundary-active-root-pyvenv-metadata-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-pyvenv-metadata-tool");
    let active_bin = tool_root.path().join("active").join("Scripts");
    std::fs::create_dir_all(&active_bin).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    let python_zip = active_bin
        .parent()
        .expect("active scripts parent")
        .join("python311.zip");
    let python_pth = active_bin
        .parent()
        .expect("active scripts parent")
        .join("python311._pth");
    let pyvenv_cfg = active_bin
        .parent()
        .expect("active scripts parent")
        .join("pyvenv.cfg");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    std::fs::write(&python_zip, b"zip-runtime").unwrap();
    std::fs::write(&python_pth, b".\\Lib\n").unwrap();
    std::fs::write(&pyvenv_cfg, b"include-system-site-packages = false\r\n").unwrap();

    let base_args = [
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
        "--setenv",
        "AI_IDE_SANDBOX_ROOT",
        "/workspace",
        "--setenv",
        "HOME",
        helper_home.to_str().unwrap(),
        "--setenv",
        "XDG_CACHE_HOME",
        helper_cache.to_str().unwrap(),
        "--setenv",
        "TMPDIR",
        helper_tmp.to_str().unwrap(),
        "--setenv",
        "ACTIVE_TOOL_ROOT",
        pyvenv_cfg.parent().unwrap().to_str().unwrap(),
    ];

    for (relative_name, expected) in [
        ("python311.zip", "zip-runtime"),
        ("python311._pth", ".\\Lib"),
        ("pyvenv.cfg", "include-system-site-packages = false"),
    ] {
        let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
            .args(base_args)
            .arg(format!("--argv={}", copied_cmd.display()))
            .arg("--argv=/D")
            .arg("--argv=/C")
            .arg(format!(
                "--argv=type \"%ACTIVE_TOOL_ROOT%\\\\{relative_name}\""
            ))
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "stdout was: {}, stderr was: {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains(expected),
            "stdout was: {}",
            String::from_utf8_lossy(&output.stdout)
        );
    }
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_non_native_subdirs_inside_external_active_root() {
    let root = TestDir::new("workspace-boundary-active-root-subdir-read-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-subdir-read-tool");
    let active_bin = tool_root.path().join("active").join("bin");
    let private_dir = active_bin.join("private");
    std::fs::create_dir_all(&private_dir).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_bin.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    std::fs::write(private_dir.join("secret.txt"), b"secret").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_BIN",
            active_bin.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=dir /b \"%ACTIVE_BIN%\\\\private\"")
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_keeps_common_runtime_subdirs_visible_inside_external_active_root() {
    let root = TestDir::new("workspace-boundary-active-root-common-runtime-subdir-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-common-runtime-subdir-tool");
    let active_tool = tool_root.path().join("python-runtime");
    let runtime_dirs = [
        active_tool.join("Lib"),
        active_tool.join("DLLs"),
        active_tool.join("share"),
        active_tool.join("libexec"),
    ];
    let private_dir = active_tool.join("private");
    for runtime_dir in &runtime_dirs {
        std::fs::create_dir_all(runtime_dir).unwrap();
    }
    std::fs::create_dir_all(&private_dir).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_tool.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    for runtime_dir in &runtime_dirs {
        std::fs::write(runtime_dir.join("runtime.txt"), b"runtime").unwrap();
    }
    std::fs::write(private_dir.join("secret.txt"), b"secret").unwrap();

    for runtime_dir in ["Lib", "DLLs", "share", "libexec"] {
        let allowed = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
            .args([
                "--workspace",
                root.path().to_str().unwrap(),
                "--cwd",
                "/workspace",
                "--setenv",
                "AI_IDE_SANDBOX_ROOT",
                "/workspace",
                "--setenv",
                "HOME",
                helper_home.to_str().unwrap(),
                "--setenv",
                "XDG_CACHE_HOME",
                helper_cache.to_str().unwrap(),
                "--setenv",
                "TMPDIR",
                helper_tmp.to_str().unwrap(),
                "--setenv",
                "ACTIVE_TOOL_ROOT",
                active_tool.to_str().unwrap(),
            ])
            .arg(format!("--argv={}", copied_cmd.display()))
            .arg("--argv=/D")
            .arg("--argv=/C")
            .arg(format!(
                "--argv=type \"%ACTIVE_TOOL_ROOT%\\\\{runtime_dir}\\\\runtime.txt\""
            ))
            .output()
            .unwrap();

        assert!(
            allowed.status.success(),
            "stdout was: {}, stderr was: {}",
            String::from_utf8_lossy(&allowed.stdout),
            String::from_utf8_lossy(&allowed.stderr)
        );
        assert_eq!("runtime", String::from_utf8_lossy(&allowed.stdout).trim());
    }

    let blocked = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            active_tool.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=dir /b \"%ACTIVE_TOOL_ROOT%\\\\private\"")
        .output()
        .unwrap();

    assert!(
        !blocked.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&blocked.stdout),
        String::from_utf8_lossy(&blocked.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&blocked.stdout),
        String::from_utf8_lossy(&blocked.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_keeps_root_level_runtime_metadata_visible_inside_external_active_root()
 {
    let root = TestDir::new("workspace-boundary-active-root-root-level-runtime-metadata-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-root-level-runtime-metadata-tool");
    let active_tool = tool_root.path().join("python-runtime");
    std::fs::create_dir_all(&active_tool).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_tool.join("cmd.exe");
    let python_pth = active_tool.join("python311._pth");
    let python_zip = active_tool.join("python311.zip");
    let pyvenv_cfg = active_tool.join("pyvenv.cfg");
    let private_file = active_tool.join("private.txt");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    std::fs::write(&python_pth, b".\\Lib\n").unwrap();
    std::fs::write(&python_zip, b"zip-runtime").unwrap();
    std::fs::write(&pyvenv_cfg, b"home = python\r\n").unwrap();
    std::fs::write(&private_file, b"secret").unwrap();

    let pth_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            active_tool.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ACTIVE_TOOL_ROOT%\\\\python311._pth\"")
        .output()
        .unwrap();

    assert!(
        pth_output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&pth_output.stdout),
        String::from_utf8_lossy(&pth_output.stderr)
    );
    assert_eq!(".\\Lib", String::from_utf8_lossy(&pth_output.stdout).trim());

    let zip_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            active_tool.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ACTIVE_TOOL_ROOT%\\\\python311.zip\"")
        .output()
        .unwrap();

    assert!(
        zip_output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&zip_output.stdout),
        String::from_utf8_lossy(&zip_output.stderr)
    );
    assert_eq!(
        "zip-runtime",
        String::from_utf8_lossy(&zip_output.stdout).trim()
    );

    let pyvenv_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            active_tool.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ACTIVE_TOOL_ROOT%\\\\pyvenv.cfg\"")
        .output()
        .unwrap();

    assert!(
        pyvenv_output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&pyvenv_output.stdout),
        String::from_utf8_lossy(&pyvenv_output.stderr)
    );
    assert_eq!(
        "home = python",
        String::from_utf8_lossy(&pyvenv_output.stdout).trim()
    );

    let blocked_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            active_tool.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ACTIVE_TOOL_ROOT%\\\\private.txt\"")
        .output()
        .unwrap();

    assert!(
        !blocked_output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&blocked_output.stdout),
        String::from_utf8_lossy(&blocked_output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&blocked_output.stdout),
        String::from_utf8_lossy(&blocked_output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_keeps_root_level_runtime_subtrees_visible_inside_external_active_root()
 {
    let root = TestDir::new("workspace-boundary-active-root-root-level-runtime-subtrees-root");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let tool_root = TestDir::new("workspace-boundary-active-root-root-level-runtime-subtrees-tool");
    let active_tool = tool_root.path().join("python-runtime");
    std::fs::create_dir_all(&active_tool).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let copied_cmd = active_tool.join("cmd.exe");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    let runtime_dirs = [
        active_tool.join("Lib"),
        active_tool.join("DLLs"),
        active_tool.join("share"),
        active_tool.join("libexec"),
    ];
    let private_dir = active_tool.join("private");
    for runtime_dir in &runtime_dirs {
        std::fs::create_dir_all(runtime_dir).unwrap();
        std::fs::write(runtime_dir.join("runtime.txt"), b"runtime-subtree").unwrap();
    }
    std::fs::create_dir_all(&private_dir).unwrap();
    std::fs::write(private_dir.join("secret.txt"), b"secret").unwrap();

    for runtime_dir in ["Lib", "DLLs", "share", "libexec"] {
        let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
            .args([
                "--workspace",
                root.path().to_str().unwrap(),
                "--cwd",
                "/workspace",
                "--setenv",
                "AI_IDE_SANDBOX_ROOT",
                "/workspace",
                "--setenv",
                "HOME",
                helper_home.to_str().unwrap(),
                "--setenv",
                "XDG_CACHE_HOME",
                helper_cache.to_str().unwrap(),
                "--setenv",
                "TMPDIR",
                helper_tmp.to_str().unwrap(),
                "--setenv",
                "ACTIVE_TOOL_ROOT",
                active_tool.to_str().unwrap(),
            ])
            .arg(format!("--argv={}", copied_cmd.display()))
            .arg("--argv=/D")
            .arg("--argv=/C")
            .arg(format!(
                "--argv=type \"%ACTIVE_TOOL_ROOT%\\\\{runtime_dir}\\\\runtime.txt\""
            ))
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "stdout was: {}, stderr was: {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(
            "runtime-subtree",
            String::from_utf8_lossy(&output.stdout).trim()
        );
    }

    let blocked_output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "ACTIVE_TOOL_ROOT",
            active_tool.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=type \"%ACTIVE_TOOL_ROOT%\\\\private\\\\secret.txt\"")
        .output()
        .unwrap();

    assert!(
        !blocked_output.status.success(),
        "stdout was: {}, stderr was: {}",
        String::from_utf8_lossy(&blocked_output.stdout),
        String::from_utf8_lossy(&blocked_output.stderr)
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&blocked_output.stdout),
        String::from_utf8_lossy(&blocked_output.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("access is denied"),
        "output was: {combined}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_node_fs_reads_of_external_roots() {
    let root = TestDir::new("workspace-boundary-node-fs-read-root");
    let blocked_host = TestDir::new("workspace-boundary-node-fs-read-blocked");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();
    let blocked_file = blocked_host.path().join("secret.txt");
    std::fs::write(&blocked_file, b"blocked").unwrap();

    let script_path = root.path().join("probe_external_node_fs_boundary.js");
    std::fs::write(
        &script_path,
        r#"const fs = require("fs");
const path = require("path");

const workspaceAllowed = path.join(process.cwd(), "allowed-read.txt");
const blockedFile = process.argv[2];
const blockedDir = path.dirname(blockedFile);

console.log(`allowed=${fs.readFileSync(workspaceAllowed, "utf8") === "allowed" ? "yes" : "no"}`);
try {
  fs.readFileSync(blockedFile, "utf8");
  console.log("read=allowed");
} catch (error) {
  console.log(`read=denied:${error && error.code}`);
}

try {
  fs.readdirSync(blockedDir);
  console.log("readdir=allowed");
} catch (error) {
  console.log(`readdir=denied:${error && error.code}`);
}
"#,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_host.path().to_str().unwrap(),
            "--argv=node",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .arg(format!("--argv={}", blocked_file.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("read=denied:EACCES") || stdout.contains("read=denied:EPERM"),
        "stdout was: {stdout}"
    );
    assert!(
        stdout.contains("readdir=denied:EACCES") || stdout.contains("readdir=denied:EPERM"),
        "stdout was: {stdout}"
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_rg_listing_of_external_roots() {
    let root = TestDir::new("workspace-boundary-rg-list-root");
    let blocked_host = TestDir::new("workspace-boundary-rg-list-blocked");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }
    if Command::new("rg")
        .arg("--version")
        .output()
        .map(|output| !output.status.success())
        .unwrap_or(true)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();
    std::fs::write(blocked_host.path().join("secret.txt"), b"blocked").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_host.path().to_str().unwrap(),
            "--argv=rg",
            "--argv=--files",
        ])
        .arg(format!("--argv={}", blocked_host.path().display()))
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout was: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("Permission denied")
            || stderr.contains("Access is denied")
            || stderr.contains("os error 5"),
        "stderr was: {stderr}"
    );
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_promotes_safe_parent_of_selected_external_read_root() {
    let root = TestDir::new("workspace-boundary-external-parent-read-slice");
    let blocked_host_parent = TestDir::new("workspace-boundary-external-parent-host");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();
    let blocked_child = blocked_host_parent.path().join("source-project");
    let sibling_host = blocked_host_parent.path().join("sibling-project");
    std::fs::create_dir_all(&blocked_child).unwrap();
    std::fs::create_dir_all(&sibling_host).unwrap();
    let sibling_file = sibling_host.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = root.path().join("probe_parent_promoted_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_child.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_safe_siblings_under_shared_parent() {
    let root = TestDir::new("workspace-boundary-external-sibling-read-slice");
    let shared_parent = TestDir::new("workspace-boundary-external-sibling-host");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let workspace_root = shared_parent.path().join("workspace");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let blocked_child = shared_parent.path().join("host-blocked");
    let sibling_host = shared_parent.path().join("host-sibling");
    std::fs::create_dir_all(&blocked_child).unwrap();
    std::fs::create_dir_all(&sibling_host).unwrap();
    let sibling_file = sibling_host.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_sibling_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_child.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_safe_siblings_under_workspace_owner_even_with_helper_child() {
    let users_root = TestDir::new("workspace-boundary-owner-sibling-users");
    let owner_root = users_root.path().join("owner");
    let workspace_root = owner_root.join("workspace");
    let blocked_child = owner_root.join("source-project");
    let sibling_host = owner_root.join("desktop");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&blocked_child).unwrap();
    std::fs::create_dir_all(&sibling_host).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_host.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_owner_sibling_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_child.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_drive_root_siblings_of_trusted_system_root_executable_scope() {
    let base = TestDir::new("workspace-boundary-executable-drive-root-system-root");
    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    let system_root = drive_root.join("Windows");
    let system32 = system_root.join("System32");
    let copied_cmd = system32.join("cmd.exe");
    let sibling_host = drive_root.join("ProgramData");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::create_dir_all(&system32).unwrap();
    std::fs::create_dir_all(&sibling_host).unwrap();
    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    std::fs::copy(
        Path::new(&real_system_root)
            .join("System32")
            .join("cmd.exe"),
        &copied_cmd,
    )
    .unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_host.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_trusted_system_drive_root_boundary.cmd");
    std::fs::write(
        &script_path,
        format!(
            "@echo off\r\nsetlocal\r\ntype \"%~dp0allowed-read.txt\" >nul 2>nul\r\nif errorlevel 1 (\r\n  echo allowed=no\r\n) else (\r\n  echo allowed=yes\r\n)\r\ntype \"{blocked}\" >nul 2>nul\r\nif errorlevel 1 (\r\n  echo blocked=denied\r\n) else (\r\n  echo blocked=allowed\r\n)\r\n",
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "SystemRoot",
            system_root.to_str().unwrap(),
            "--setenv",
            "WINDIR",
            system_root.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", copied_cmd.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=call")
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(stdout.contains("blocked=denied"), "stdout was: {stdout}");
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_real_programdata_for_trusted_system_root_active_scope() {
    let workspace_root = TestDir::new("workspace-boundary-real-programdata-scope");
    if !helper_reports_restricted_token_enabled_raw(workspace_root.path())
        || !helper_reports_write_boundary_enabled_raw(workspace_root.path())
        || !helper_reports_read_boundary_enabled_raw(workspace_root.path())
    {
        return;
    }

    let helper_root = helper_env_root(workspace_root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let real_system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
    let system_root = PathBuf::from(&real_system_root);
    let Some(system_drive_root) = system_root.ancestors().last().map(Path::to_path_buf) else {
        return;
    };
    let blocked_dir = system_drive_root.join("ProgramData");
    if !blocked_dir.is_dir() {
        return;
    }
    let cmd_path = system_root.join("System32").join("cmd.exe");
    let allowed_file = workspace_root.path().join("allowed-read.txt");
    std::fs::write(&allowed_file, b"allowed").unwrap();

    let script_path = workspace_root
        .path()
        .join("probe_real_programdata_boundary.cmd");
    std::fs::write(
        &script_path,
        format!(
            "@echo off\r\nsetlocal\r\ntype \"%~dp0allowed-read.txt\" >nul 2>nul\r\nif errorlevel 1 (\r\n  echo allowed=no\r\n) else (\r\n  echo allowed=yes\r\n)\r\ndir \"{blocked}\" >nul 2>nul\r\nif errorlevel 1 (\r\n  echo blocked=denied\r\n) else (\r\n  echo blocked=allowed\r\n)\r\n",
            blocked = blocked_dir.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "SystemRoot",
            system_root.to_str().unwrap(),
            "--setenv",
            "WINDIR",
            system_root.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", cmd_path.display()))
        .arg("--argv=/D")
        .arg("--argv=/C")
        .arg("--argv=call")
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(stdout.contains("blocked=denied"), "stdout was: {stdout}");
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_safe_siblings_from_allowed_scope_without_selected_external_root()
 {
    let users_root = TestDir::new("workspace-boundary-allowed-scope-owner-users");
    let owner_root = users_root.path().join("owner");
    let workspace_root = owner_root.join("workspace");
    let sibling_host = owner_root.join("desktop");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&sibling_host).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_host.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_allowed_scope_owner_sibling_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_user_profile_siblings_for_allowed_roots() {
    let users_root = TestDir::new("workspace-boundary-user-profile-siblings");
    let owner_root = users_root.path().join("owner");
    let workspace_root = owner_root.join("workspace");
    let sibling_profile = users_root.path().join("public");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&sibling_profile).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_profile.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_user_profile_sibling_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_safe_siblings_of_first_unsafe_ancestor() {
    let users_root = TestDir::new("workspace-boundary-unsafe-ancestor-users");
    let owner_root = users_root.path().join("owner");
    let workspace_root = owner_root.join("workspace");
    let blocked_child = owner_root.join("source-project");
    let sibling_profile = users_root.path().join("public");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&blocked_child).unwrap();
    std::fs::create_dir_all(&sibling_profile).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_profile.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_unsafe_ancestor_sibling_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_child.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_safe_siblings_of_higher_unsafe_ancestors() {
    let base = TestDir::new("workspace-boundary-higher-unsafe-ancestor-base");
    let users_root = base.path().join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let blocked_child = owner_root.join("source-project");
    let sibling_temp = base.path().join("temp");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&blocked_child).unwrap();
    std::fs::create_dir_all(&sibling_temp).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_temp.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_higher_unsafe_ancestor_sibling_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_child.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_safe_siblings_of_higher_unsafe_ancestors_even_with_system_root_child()
 {
    let base = TestDir::new("workspace-boundary-higher-unsafe-ancestor-system-root-base");
    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let blocked_child = owner_root.join("source-project");
    let sibling_temp = drive_root.join("temp");
    let system_root = drive_root.join("Windows");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&blocked_child).unwrap();
    std::fs::create_dir_all(&sibling_temp).unwrap();
    std::fs::create_dir_all(&system_root).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_temp.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path =
        workspace_root.join("probe_higher_unsafe_ancestor_system_root_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "SystemRoot",
            system_root.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_child.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_safe_siblings_of_higher_unsafe_ancestors_even_with_helper_path_child()
 {
    let base = TestDir::new("workspace-boundary-higher-unsafe-ancestor-path-child-base");
    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let blocked_child = owner_root.join("source-project");
    let sibling_temp = drive_root.join("temp");
    let path_tool = drive_root.join("tools");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&blocked_child).unwrap();
    std::fs::create_dir_all(&sibling_temp).unwrap();
    std::fs::create_dir_all(&path_tool).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_temp.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path =
        workspace_root.join("probe_higher_unsafe_ancestor_path_child_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let inherited_path = std::env::var_os("PATH").unwrap_or_default();
    let combined_path = std::env::join_paths(
        std::iter::once(path_tool.clone()).chain(std::env::split_paths(&inherited_path)),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATH", combined_path)
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_child.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_inactive_helper_path_scope_outside_active_launch() {
    let base = TestDir::new("workspace-boundary-inactive-helper-path-scope");
    let Some(active_python) = host_python_executable() else {
        return;
    };
    let Some(active_python_root) = active_python.parent().map(Path::to_path_buf) else {
        return;
    };
    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let helper_root = owner_root.join("AppData").join("helper");
    let inactive_tool = drive_root.join("inactive-tools").join("bin");
    let inactive_sibling = drive_root.join("inactive-tools").join("private");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&inactive_tool).unwrap();
    std::fs::create_dir_all(&inactive_sibling).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = inactive_sibling.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_inactive_helper_path_scope.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let inherited_path = std::env::var_os("PATH").unwrap_or_default();
    let combined_path = std::env::join_paths(
        std::iter::once(inactive_tool.clone())
            .chain(std::iter::once(active_python_root.clone()))
            .chain(std::env::split_paths(&inherited_path)),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATH", combined_path)
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
        ])
        .arg(format!("--argv={}", active_python.display()))
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_executable_root_parent_siblings_outside_allowed_scope() {
    let base = TestDir::new("workspace-boundary-executable-parent-sibling-base");
    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let tool_root = drive_root.join("system-root").join("sdk");
    let path_tool = tool_root.join("bin");
    let secret_root = tool_root.join("private");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&path_tool).unwrap();
    std::fs::create_dir_all(&secret_root).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = secret_root.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_executable_parent_sibling_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let inherited_path = std::env::var_os("PATH").unwrap_or_default();
    let combined_path = std::env::join_paths(
        std::iter::once(path_tool.clone()).chain(std::env::split_paths(&inherited_path)),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATH", combined_path)
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_higher_ancestor_siblings_of_executable_scope() {
    let base = TestDir::new("workspace-boundary-executable-higher-ancestor-base");
    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let tool_root = drive_root.join("system-root").join("sdk");
    let path_tool = tool_root.join("bin");
    let sibling_host = drive_root.join("temp");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&path_tool).unwrap();
    std::fs::create_dir_all(&sibling_host).unwrap();
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_host.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_executable_higher_ancestor_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let inherited_path = std::env::var_os("PATH").unwrap_or_default();
    let combined_path = std::env::join_paths(
        std::iter::once(path_tool.clone()).chain(std::env::split_paths(&inherited_path)),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATH", combined_path)
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_executable_scope_with_many_drive_children() {
    let base = TestDir::new("workspace-boundary-executable-many-drive-children-base");
    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let tool_root = drive_root.join("system-root").join("sdk");
    let path_tool = tool_root.join("bin");
    let sibling_host = drive_root.join("temp-19");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&path_tool).unwrap();
    for index in 0..20 {
        std::fs::create_dir_all(drive_root.join(format!("temp-{index}"))).unwrap();
    }
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_host.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_executable_many_drive_children_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let inherited_path = std::env::var_os("PATH").unwrap_or_default();
    let combined_path = std::env::join_paths(
        std::iter::once(path_tool.clone()).chain(std::env::split_paths(&inherited_path)),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATH", combined_path)
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_allowed_scope_with_many_drive_children() {
    let base = TestDir::new("workspace-boundary-allowed-scope-many-drive-children-base");
    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let sibling_host = drive_root.join("temp-19");
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_root = owner_root.join("AppData").join("helper");
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    for index in 0..20 {
        std::fs::create_dir_all(drive_root.join(format!("temp-{index}"))).unwrap();
    }
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_host.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_allowed_scope_many_drive_children_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host surfaces unmanaged"]
fn binary_restricted_boundary_blocks_safe_siblings_even_with_many_protected_children() {
    let base = TestDir::new("workspace-boundary-higher-unsafe-ancestor-many-protected-base");
    let drive_root = base.path().join("drive");
    let users_root = drive_root.join("users");
    let owner_root = users_root.join("owner");
    let workspace_root = owner_root.join("workspace");
    let blocked_child = owner_root.join("source-project");
    let sibling_temp = drive_root.join("temp");
    let helper_root = owner_root.join("AppData").join("helper");
    let protected_children = (0..20)
        .map(|index| drive_root.join(format!("tool-{index}")))
        .collect::<Vec<_>>();
    if !helper_reports_restricted_token_enabled_raw(&workspace_root)
        || !helper_reports_write_boundary_enabled_raw(&workspace_root)
        || !helper_reports_read_boundary_enabled_raw(&workspace_root)
    {
        return;
    }

    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&workspace_root).unwrap();
    std::fs::create_dir_all(&blocked_child).unwrap();
    std::fs::create_dir_all(&sibling_temp).unwrap();
    for tool in &protected_children {
        std::fs::create_dir_all(tool).unwrap();
    }
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(workspace_root.join("allowed-read.txt"), b"allowed").unwrap();
    let sibling_file = sibling_temp.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = workspace_root.join("probe_higher_unsafe_ancestor_many_protected.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let combined_path = std::env::join_paths(protected_children.iter()).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATH", combined_path)
        .args([
            "--workspace",
            workspace_root.to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_child.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
fn binary_restricted_boundary_promotes_highest_safe_ancestor_of_selected_external_read_root() {
    let root = TestDir::new("workspace-boundary-external-ancestor-read-slice");
    let blocked_host_parent = TestDir::new("workspace-boundary-external-ancestor-host");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();
    let blocked_child = blocked_host_parent
        .path()
        .join("outer")
        .join("nested")
        .join("source-project");
    let sibling_host = blocked_host_parent
        .path()
        .join("outer")
        .join("sibling-project");
    std::fs::create_dir_all(&blocked_child).unwrap();
    std::fs::create_dir_all(&sibling_host).unwrap();
    let sibling_file = sibling_host.join("secret.txt");
    std::fs::write(&sibling_file, b"blocked").unwrap();

    let script_path = root.path().join("probe_ancestor_promoted_read_boundary.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = sibling_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_child.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(sibling_file.exists());
}

#[cfg(windows)]
#[test]
fn binary_stdio_proxy_boundary_blocks_selected_external_read_root() {
    let root = TestDir::new("workspace-boundary-external-read-stdio");
    let blocked_host = TestDir::new("workspace-boundary-external-read-stdio-host");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();
    let blocked_file = blocked_host.path().join("secret.txt");
    std::fs::write(&blocked_file, b"blocked").unwrap();

    let script_path = root.path().join("probe_external_read_boundary_stdio.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except FileNotFoundError:
    print("blocked=missing")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = blocked_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_host.path().to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(blocked_file.exists());
}

#[cfg(windows)]
#[test]
fn binary_stdio_proxy_boundary_blocks_control_and_response_files() {
    let root = TestDir::new("workspace-boundary-stdio-control-response");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();

    let control_file = root.path().join("control.json");
    let response_file = root.path().join("status.json");
    let control_temp_dir = helper_protocol_temp_directory(&control_file);
    let response_temp_dir = helper_protocol_temp_directory(&response_file);
    let script_path = root.path().join("probe_stdio_boundary_control_response.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path

workspace_allowed = Path.cwd() / "allowed-read.txt"
control = Path(r"{control}")
response = Path(r"{response}")
control_temp = Path(r"{control_temp}")
response_temp = Path(r"{response_temp}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}")
for name, path in (("control", control), ("response", response)):
    try:
        path.read_text()
    except PermissionError:
        print(f"{{name}}=denied")
    except FileNotFoundError:
        print(f"{{name}}=missing")
    except OSError as error:
        print(f"{{name}}=oserror:{{getattr(error, 'winerror', None)}}")
    else:
        print(f"{{name}}=allowed")
for name, path in (("control_temp", control_temp), ("response_temp", response_temp)):
    try:
        list(path.iterdir())
    except PermissionError:
        print(f"{{name}}=denied")
    except FileNotFoundError:
        print(f"{{name}}=missing")
    except OSError as error:
        print(f"{{name}}=oserror:{{getattr(error, 'winerror', None)}}")
    else:
        print(f"{{name}}=allowed")
"#,
            control = control_file.display(),
            response = response_file.display(),
            control_temp = control_temp_dir.display(),
            response_temp = response_temp_dir.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--control-file",
            control_file.to_str().unwrap(),
            "--response-file",
            response_file.to_str().unwrap(),
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("control=denied") || stdout.contains("control=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(
        stdout.contains("response=denied") || stdout.contains("response=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(
        stdout.contains("control_temp=denied") || stdout.contains("control_temp=oserror:5"),
        "stdout was: {stdout}"
    );
    assert!(
        stdout.contains("response_temp=denied") || stdout.contains("response_temp=oserror:5"),
        "stdout was: {stdout}"
    );
}

#[cfg(windows)]
#[test]
fn binary_process_mode_boundary_blocks_siblings_of_external_control_response_root() {
    let root = TestDir::new("workspace-boundary-process-control-response-siblings");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    std::fs::write(root.path().join("allowed-read.txt"), b"allowed").unwrap();

    let external_control_root = TestDir::new("workspace-boundary-process-control-response-host");
    let control_dir = external_control_root.path().join("controls");
    let sibling_secret_dir = external_control_root.path().join("secret");
    std::fs::create_dir_all(&control_dir).unwrap();
    std::fs::create_dir_all(&sibling_secret_dir).unwrap();
    let control_file = control_dir.join("control.json");
    let response_file = control_dir.join("status.json");
    let sibling_secret = sibling_secret_dir.join("secret.txt");
    std::fs::write(&sibling_secret, b"blocked").unwrap();

    let script_path = root
        .path()
        .join("probe_process_control_response_siblings.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path
import time

workspace_allowed = Path.cwd() / "allowed-read.txt"
blocked = Path(r"{blocked}")

print(f"allowed={{'yes' if workspace_allowed.read_text() == 'allowed' else 'no'}}", flush=True)
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied", flush=True)
except FileNotFoundError:
    print("blocked=missing", flush=True)
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}", flush=True)
else:
    print("blocked=allowed", flush=True)
time.sleep(30)
"#,
            blocked = sibling_secret.display()
        ),
    )
    .unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--control-file",
            control_file.to_str().unwrap(),
            "--response-file",
            response_file.to_str().unwrap(),
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let helper = command.spawn().unwrap();

    write_helper_control_message(
        &control_file,
        &WindowsStrictHelperControlMessage {
            version: 1,
            command: HelperControlCommand::Status,
            request_id: Some("req-control".to_string()),
            run_id: Some("run-control".to_string()),
            backend: Some("restricted-host-helper".to_string()),
        },
    )
    .unwrap();

    let status = wait_for_status(&response_file).unwrap();
    assert_eq!(Some("req-control"), status.request_id.as_deref());
    assert_eq!(Some("run-control"), status.run_id.as_deref());
    assert_eq!(Some("restricted-host-helper"), status.backend.as_deref());

    write_helper_control_message(
        &control_file,
        &WindowsStrictHelperControlMessage {
            version: 1,
            command: HelperControlCommand::Kill,
            request_id: Some("req-kill".to_string()),
            run_id: Some("run-control".to_string()),
            backend: Some("restricted-host-helper".to_string()),
        },
    )
    .unwrap();

    let output = helper.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("allowed=yes"), "stdout was: {stdout}");
    assert!(
        stdout.contains("blocked=denied") || stdout.contains("blocked=oserror:5"),
        "stdout was: {stdout}"
    );
}

#[cfg(windows)]
#[test]
fn binary_one_shot_boundary_restores_selected_external_read_root_label_after_exit() {
    let root = TestDir::new("workspace-boundary-external-read-restore");
    let blocked_host = TestDir::new("workspace-boundary-external-read-restore-host");
    if !helper_reports_restricted_token_enabled(&root)
        || !helper_reports_write_boundary_enabled(&root)
        || !helper_reports_read_boundary_enabled(&root)
    {
        return;
    }

    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();
    let blocked_dir = blocked_host.path().join("nested");
    let blocked_file = blocked_dir.join("secret.txt");
    std::fs::create_dir_all(&blocked_dir).unwrap();
    std::fs::write(&blocked_file, b"blocked").unwrap();
    let before_root = capture_label_security_descriptor(blocked_host.path()).unwrap();
    let before_nested_dir = capture_label_security_descriptor(&blocked_dir).unwrap();
    let before_nested_file = capture_label_security_descriptor(&blocked_file).unwrap();

    let script_path = root.path().join("probe_external_read_boundary_restore.py");
    std::fs::write(
        &script_path,
        format!(
            r#"from pathlib import Path
blocked = Path(r"{blocked}")
try:
    blocked.read_text()
except PermissionError:
    print("blocked=denied")
except OSError as error:
    print(f"blocked=oserror:{{getattr(error, 'winerror', None)}}")
else:
    print("blocked=allowed")
"#,
            blocked = blocked_file.display()
        ),
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "AI_IDE_SANDBOX_ROOT",
            "/workspace",
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "AI_IDE_BLOCKED_READ_ROOTS",
            blocked_host.path().to_str().unwrap(),
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let after_root = capture_label_security_descriptor(blocked_host.path()).unwrap();
    let after_nested_dir = capture_label_security_descriptor(&blocked_dir).unwrap();
    let after_nested_file = capture_label_security_descriptor(&blocked_file).unwrap();
    assert_eq!(before_root, after_root);
    assert_eq!(before_nested_dir, after_nested_dir);
    assert_eq!(before_nested_file, after_nested_file);
}

#[cfg(windows)]
#[test]
fn binary_stdio_proxy_structured_batch_hides_internal_workspace_roots_during_launch() {
    let root = TestDir::new("workspace-boundary-hide-internal-stdio-batch");
    let internal_root = root.path().join(".ai_ide_runtime");
    std::fs::create_dir_all(&internal_root).unwrap();
    std::fs::write(internal_root.join("secret.txt"), b"hidden").unwrap();
    let batch_script = root.path().join("probe_internal_root.cmd");
    std::fs::write(
        &batch_script,
        "@echo off\r\nif exist .ai_ide_runtime\\secret.txt (\r\n  echo internal-root-visible\r\n) else (\r\n  echo internal-root-hidden\r\n)\r\n",
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--command",
            r"call .\probe_internal_root.cmd",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!(
        "internal-root-hidden",
        String::from_utf8_lossy(&output.stdout).trim()
    );
    assert!(internal_root.join("secret.txt").exists());
}

#[cfg(windows)]
#[test]
fn binary_stdio_proxy_hides_internal_workspace_roots_from_subdir_cwd() {
    let root = TestDir::new("workspace-boundary-hide-internal-stdio-subdir");
    let subdir = root.path().join("subdir");
    std::fs::create_dir_all(&subdir).unwrap();
    let internal_root = root.path().join(".ai_ide_runtime");
    std::fs::create_dir_all(&internal_root).unwrap();
    std::fs::write(internal_root.join("secret.txt"), b"hidden").unwrap();
    let script_path = subdir.join("probe_internal_root.py");
    std::fs::write(
        &script_path,
        r#"from pathlib import Path
target = Path("..") / ".ai_ide_runtime" / "secret.txt"
try:
    print(target.read_text())
except FileNotFoundError:
    print("internal-root-hidden")
"#,
    )
    .unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace/subdir",
            "--stdio-proxy",
            "--argv=python",
        ])
        .arg(format!("--argv={}", script_path.display()))
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!(
        "internal-root-hidden",
        String::from_utf8_lossy(&output.stdout).trim()
    );
    assert!(internal_root.join("secret.txt").exists());
}

#[test]
fn binary_one_shot_child_environment_does_not_leak_parent_secret_env() {
    let root = TestDir::new("env-scrub");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("AI_IDE_SECRET_TOKEN", "top-secret")
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            "--argv=-c",
            "--argv=import os; print(os.environ.get('AI_IDE_SECRET_TOKEN', '(missing)'))",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!("(missing)", String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_explicit_program_path_outside_allowed_roots() {
    let root = TestDir::new("program-path-reject");
    let outside = TestDir::new("program-path-outside");
    let script_path = outside.path().join("outside-tool.cmd");
    std::fs::write(&script_path, "@echo off\r\necho outside-program\r\n").unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command.args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
    ]);
    command.arg(format!("--argv={}", script_path.display()));
    let output = command.output().unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("helper executable path must stay under workspace"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_drive_relative_program_path() {
    let root = TestDir::new("program-drive-relative-reject");

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=C:outside-tool.cmd",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("drive-relative form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_root_relative_program_path() {
    let root = TestDir::new("program-root-relative-reject");

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            r"--argv=\Users\Public\outside-tool.exe",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("root-relative form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_reserved_device_program_path() {
    let root = TestDir::new("program-reserved-device-reject");

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=NUL",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("Windows reserved device names"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_explicit_script_argument_outside_allowed_roots() {
    let root = TestDir::new("script-arg-reject");
    let outside = TestDir::new("script-arg-outside");
    let script_path = outside.path().join("outside_script.py");
    std::fs::write(&script_path, "print('outside-script')\n").unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command.args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
        "--argv=python",
    ]);
    command.arg(format!("--argv={}", script_path.display()));
    let output = command.output().unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("helper argv path must stay under"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_attached_option_value_script_argument_outside_workspace() {
    let root = TestDir::new("script-arg-option-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            r"--argv=--config=C:\Users\Public\outside.py",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("helper argv path must stay under"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_response_file_script_argument_outside_workspace() {
    let root = TestDir::new("script-arg-response-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            r"--argv=@C:\Users\Public\outside.rsp",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("helper argv path must stay under"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_quoted_attached_option_value_script_argument_outside_workspace() {
    let root = TestDir::new("script-arg-option-quoted-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            r#"--argv=--config="C:\Users\Public\outside.py""#,
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("helper argv path must stay under"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_alternate_data_stream_script_argument() {
    let root = TestDir::new("script-arg-ads-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            r"--argv=.\script.py:secret",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("alternate data streams"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_reserved_device_script_argument() {
    let root = TestDir::new("script-arg-reserved-device-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            "--argv=NUL.txt",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("Windows reserved device names"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_bare_hardlink_script_argument_in_workspace() {
    let root = TestDir::new("script-arg-hardlink-bare-reject");
    let outside = TestDir::new("script-arg-hardlink-bare-outside");
    let target = outside.path().join("linked.py");
    let alias = root.path().join("linked.py");
    std::fs::write(&target, "print('alias')\n").unwrap();
    std::fs::hard_link(&target, &alias).unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            "--argv=linked.py",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("hardlink aliases"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_unc_script_argument() {
    let root = TestDir::new("unc-script-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            "--argv=\\\\server\\share\\outside.py",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("UNC or device form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_drive_relative_script_argument() {
    let root = TestDir::new("script-drive-relative-reject");

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            "--argv=C:outside_script.py",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("drive-relative form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_root_relative_script_argument() {
    let root = TestDir::new("script-root-relative-reject");

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            r"--argv=\Users\Public\outside_script.py",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("root-relative form"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_helper_env_root_outside_temp_helper_base() {
    let root = TestDir::new("env-invalid");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "HOME",
            "C:\\Users\\Public\\home",
            "--setenv",
            "TMPDIR",
            "C:\\Users\\Public\\tmp",
            "--setenv",
            "XDG_CACHE_HOME",
            "C:\\Users\\Public\\cache",
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("helper environment root must stay under"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_helper_env_root_through_reparse_point() {
    let root = TestDir::new("env-root-reparse");
    let expected_root_base = std::env::temp_dir().join("ai_ide_strict_helper");
    let helper_root =
        expected_root_base.join(format!("binary-junction-root-{}", std::process::id()));
    let outside =
        expected_root_base.join(format!("binary-junction-outside-{}", std::process::id()));
    std::fs::create_dir_all(&expected_root_base).unwrap();
    std::fs::create_dir_all(&outside).unwrap();
    if !create_junction(&helper_root, &outside) {
        return;
    }

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("reparse points"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_path_override_in_helper_env() {
    let root = TestDir::new("env-path-override-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PATH",
            "C:\\Users\\Public",
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("PATH must not be overridden"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_pythonpath_override_outside_allowed_roots() {
    let root = TestDir::new("env-pythonpath-override-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            r"C:\Users\Public\outside",
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("PYTHONPATH must stay under"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_pythonpath_override_under_helper_root() {
    let root = TestDir::new("env-pythonpath-helper-root-reject");
    let helper_root = helper_env_root("env-pythonpath-helper-root-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--setenv",
            "PYTHONPATH",
            helper_root.join("cache").join("libs").to_str().unwrap(),
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("helper-owned roots"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
#[ignore = "workspace-only leaves outside-host paths unmanaged"]
fn binary_rejects_pythonpath_override_under_system_root() {
    let root = TestDir::new("env-pythonpath-system-root-reject");
    let system_root = std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows"));
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            system_root.join("System32").to_str().unwrap(),
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("PYTHONPATH must stay under workspace"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_pythonpath_override_using_root_relative_path() {
    let root = TestDir::new("env-pythonpath-root-relative-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            r"\Users\Public\outside",
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("root-relative paths"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_pythonpath_override_using_forward_slash_root_relative_path() {
    let root = TestDir::new("env-pythonpath-root-relative-forward-slash-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            "/Users/Public/outside",
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("root-relative paths"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_workspace_hardlink_script_argument() {
    let root = TestDir::new("argv-hardlink-script-reject");
    let outside = root.path().join("outside");
    let alias_dir = root.path().join("src");
    std::fs::create_dir_all(&outside).unwrap();
    std::fs::create_dir_all(&alias_dir).unwrap();
    let target = outside.join("linked.py");
    let alias = alias_dir.join("linked.py");
    std::fs::write(&target, b"print('hardlink')\n").unwrap();
    std::fs::hard_link(&target, &alias).unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            &format!("--argv={}", alias.display()),
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("hardlink aliases"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_pythonpath_override_through_workspace_hardlink() {
    let root = TestDir::new("env-hardlink-reject");
    let outside = root.path().join("outside");
    let alias_dir = root.path().join("src");
    std::fs::create_dir_all(&outside).unwrap();
    std::fs::create_dir_all(&alias_dir).unwrap();
    let target = outside.join("linked.py");
    let alias = alias_dir.join("linked.py");
    std::fs::write(&target, b"print('hardlink')\n").unwrap();
    std::fs::hard_link(&target, &alias).unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            alias.to_str().unwrap(),
            "--command",
            "echo should-not-run",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("hardlink aliases"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_rejects_pythonpath_override_into_internal_workspace_root() {
    let root = TestDir::new("env-pythonpath-internal-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            r".ai_ide_runtime\hidden",
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("internal workspace metadata"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_allows_workspace_relative_pythonpath_override() {
    let root = TestDir::new("env-pythonpath-workspace");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "PYTHONPATH",
            "src;tests",
            "--argv=python",
            "--argv=-c",
            "--argv=import os; print(os.environ.get('PYTHONPATH', ''))",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    let expected = format!(
        "{};{}",
        root.path().join("src").display(),
        root.path().join("tests").display()
    );
    assert_eq!(expected, String::from_utf8_lossy(&output.stdout).trim());
}

#[cfg(windows)]
#[test]
fn binary_rejects_userprofile_override_in_helper_env() {
    let root = TestDir::new("env-userprofile-override-reject");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "USERPROFILE",
            "C:\\Users\\Public",
            "--argv=python",
            "--argv=-c",
            "--argv=print('should-not-run')",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("USERPROFILE must not be overridden"),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
#[test]
fn binary_one_shot_child_environment_derives_windows_home_and_temp_variables() {
    let root = TestDir::new("env-derived");
    let helper_root = helper_env_root("env-derived");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--setenv",
            "HOME",
            helper_root.join("home").to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_root.join("tmp").to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_root.join("cache").to_str().unwrap(),
            "--argv=python",
            "--argv=-c",
            "--argv=import os; print(os.environ.get('USERPROFILE', '')); print(os.environ.get('TEMP', '')); print(os.environ.get('TMP', '')); print(os.environ.get('NoDefaultCurrentDirectoryInExePath', ''))",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    let lines = String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(|line| line.trim().to_string())
        .collect::<Vec<_>>();
    assert_eq!(
        vec![
            helper_root.join("home").display().to_string(),
            helper_root.join("tmp").display().to_string(),
            helper_root.join("tmp").display().to_string(),
            "1".to_string(),
        ],
        lines
    );
}

#[cfg(windows)]
#[test]
fn binary_sanitizes_inherited_path_before_child_launch() {
    let root = TestDir::new("env-path-sanitize");
    let inherited_path = std::env::var("PATH").unwrap_or_default();
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATH", format!(r".;\\server\share;{inherited_path}"))
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            "--argv=-c",
            "--argv=import os; print(os.environ.get('PATH', '')); print(os.environ.get('NoDefaultCurrentDirectoryInExePath', ''))",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    let lines = String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(|line| line.trim().to_string())
        .collect::<Vec<_>>();
    assert_eq!(
        2,
        lines.len(),
        "stdout was: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    let path_value = &lines[0];
    assert!(
        !path_value.contains(r"\\server\share"),
        "PATH was: {path_value}"
    );
    assert!(
        !path_value.to_ascii_lowercase().starts_with(".;"),
        "PATH was: {path_value}"
    );
    assert_eq!("1", lines[1]);
}

#[cfg(windows)]
#[test]
fn binary_sanitizes_inherited_pathext_before_child_launch() {
    let root = TestDir::new("env-pathext-sanitize");
    let output = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"))
        .env("PATHEXT", ".JS;.EXE;.BAT;.VBS;.CMD;.EXE")
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--argv=python",
            "--argv=-c",
            "--argv=import os; print(os.environ.get('PATHEXT', ''))",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!(
        ".EXE;.BAT;.CMD",
        String::from_utf8_lossy(&output.stdout).trim()
    );
}

#[cfg(windows)]
#[test]
fn binary_one_shot_argv_blocks_grandchild_spawn_under_single_process_job() {
    let root = TestDir::new("active-process-limit");
    if !helper_reports_restricted_token_enabled(&root) {
        return;
    }
    let script_path = root.path().join("attempt_spawn.py");
    std::fs::write(
        &script_path,
        concat!(
            "import subprocess, sys, time\n",
            "time.sleep(0.25)\n",
            "try:\n",
            "    subprocess.Popen(['python', '-c', 'import time; time.sleep(0.5)'])\n",
            "except Exception:\n",
            "    print('spawn-blocked')\n",
            "    sys.exit(0)\n",
            "print('spawn-allowed')\n",
            "sys.exit(1)\n",
        ),
    )
    .unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command.args([
        "--workspace",
        root.path().to_str().unwrap(),
        "--cwd",
        "/workspace",
        "--argv=python",
    ]);
    command.arg(format!("--argv={}", script_path.display()));
    let output = command.output().unwrap();

    assert!(
        output.status.success(),
        "stderr was: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        "spawn-blocked",
        String::from_utf8_lossy(&output.stdout).trim()
    );
}

#[cfg(windows)]
#[test]
fn binary_process_mode_kills_child_when_helper_exits() {
    let root = TestDir::new("job-close");
    if !helper_reports_restricted_token_enabled(&root) {
        return;
    }
    let helper_root = helper_env_root(root.path().file_name().unwrap().to_str().unwrap());
    let helper_home = helper_root.join("home");
    let helper_tmp = helper_root.join("tmp");
    let helper_cache = helper_root.join("cache");
    let control_file = root.path().join("control.json");
    let response_file = root.path().join("status.json");
    std::fs::create_dir_all(&helper_home).unwrap();
    std::fs::create_dir_all(&helper_tmp).unwrap();
    std::fs::create_dir_all(&helper_cache).unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_ai-ide-windows-helper"));
    command
        .args([
            "--workspace",
            root.path().to_str().unwrap(),
            "--cwd",
            "/workspace",
            "--stdio-proxy",
            "--control-file",
            control_file.to_str().unwrap(),
            "--response-file",
            response_file.to_str().unwrap(),
            "--setenv",
            "HOME",
            helper_home.to_str().unwrap(),
            "--setenv",
            "TMPDIR",
            helper_tmp.to_str().unwrap(),
            "--setenv",
            "XDG_CACHE_HOME",
            helper_cache.to_str().unwrap(),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    for arg in blocking_child_argv() {
        command.arg(format!("--argv={arg}"));
    }
    let mut helper = command.spawn().unwrap();

    write_helper_control_message(
        &control_file,
        &WindowsStrictHelperControlMessage {
            version: 1,
            command: HelperControlCommand::Status,
            request_id: Some("req-job".to_string()),
            run_id: Some("run-job".to_string()),
            backend: Some("restricted-host-helper".to_string()),
        },
    )
    .unwrap();
    let status = wait_for_status(&response_file).unwrap();
    let child_pid = status.pid.expect("expected helper to report child pid");

    helper.kill().unwrap();
    let exited = helper.wait_timeout(Duration::from_secs(5)).unwrap();
    assert!(exited, "helper process did not exit after kill");
    assert!(
        wait_for_process_exit(child_pid, Duration::from_secs(5)).unwrap(),
        "child process was still alive after helper exit"
    );
}

fn blocking_child_argv() -> Vec<String> {
    vec![
        "python".to_string(),
        "-c".to_string(),
        "import time; time.sleep(5)".to_string(),
    ]
}

fn wait_for_status(
    path: &Path,
) -> io::Result<ai_ide_windows_helper::WindowsStrictHelperStatusMessage> {
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        if let Some(status) = read_helper_status_message(path) {
            return Ok(status);
        }
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                format!("timed out waiting for status file: {}", path.display()),
            ));
        }
        thread::sleep(Duration::from_millis(50));
    }
}

trait WaitTimeout {
    fn wait_timeout(&mut self, timeout: Duration) -> io::Result<bool>;
}

impl WaitTimeout for std::process::Child {
    fn wait_timeout(&mut self, timeout: Duration) -> io::Result<bool> {
        let deadline = Instant::now() + timeout;
        loop {
            if self.try_wait()?.is_some() {
                return Ok(true);
            }
            if Instant::now() >= deadline {
                return Ok(false);
            }
            thread::sleep(Duration::from_millis(25));
        }
    }
}

fn helper_env_root(name: &str) -> PathBuf {
    std::env::temp_dir()
        .join("ai_ide_strict_helper")
        .join(format!("helper-binary-{name}-{}", std::process::id()))
}

#[cfg(windows)]
fn wait_for_process_exit(pid: u32, timeout: Duration) -> io::Result<bool> {
    use windows_sys::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0, WAIT_TIMEOUT};
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, WaitForSingleObject,
    };

    const SYNCHRONIZE: u32 = 0x0010_0000;

    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, 0, pid) };
    if handle.is_null() {
        return Ok(true);
    }
    let wait_result =
        unsafe { WaitForSingleObject(handle, timeout.as_millis().min(u32::MAX as u128) as u32) };
    unsafe { CloseHandle(handle) };
    if wait_result == WAIT_OBJECT_0 {
        return Ok(true);
    }
    if wait_result == WAIT_TIMEOUT {
        return Ok(false);
    }
    Err(io::Error::last_os_error())
}
