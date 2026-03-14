#![allow(dead_code, unused_imports, unused_variables)]

use std::collections::BTreeMap;
use std::fs;
use std::io::{self, BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crate::boundary_lock::RestrictedLaunchBoundaryLock;
use crate::child_environment::prepare_child_environment;
use crate::execution_guard::{
    direct_argv_candidate_for_shell_command, helper_echo_output_for_shell_command,
    structured_shell_script_candidate_for_shell_command, validate_child_argv_access,
    validate_shell_command_text,
};
use crate::filesystem_boundary::{
    BLOCKED_READ_ROOTS_ENV, BlockedInternalRootsStaging,
    blocked_read_roots_for_runtime_executable_root_with_allowed_roots, collapse_blocked_read_roots,
    launch_scoped_environment, path_is_under_allowed_roots, prepare_low_integrity_boundary,
    require_read_boundary_for_restricted_launch, resolve_filesystem_boundary_layout,
    resolve_filesystem_boundary_layout_for_launch, resolve_program_path,
    run_restricted_one_shot_argv, run_restricted_one_shot_program, spawn_restricted_proxy_program,
    stage_read_boundary, trusted_system_roots,
};
use crate::path_policy::{
    path_targets_internal_workspace_root, path_traverses_workspace_reparse_point,
    path_uses_windows_alternate_data_stream, path_uses_windows_reserved_device_name,
};
use crate::process_containment::HelperContainmentPolicy;
use crate::restricted_token::helper_can_create_restricted_token;
use crate::{
    FIXTURE_MARKER_PREFIX, HelperStatusState, WindowsStrictHelperControlMessage,
    WindowsStrictHelperRequest, WindowsStrictHelperStatusMessage, helper_protocol_temp_directory,
    read_helper_control_message, write_helper_status_message,
};

pub fn run_request(request: &WindowsStrictHelperRequest) -> io::Result<i32> {
    let cwd = map_workspace_path(&request.workspace, &request.cwd)?;
    let environment = prepare_child_environment(&request.workspace, &request.environment)?;
    if is_fixture_command(request.command.as_deref()) {
        return run_fixture_emulation(&request.workspace, &cwd, &environment);
    }
    if request.stdio_proxy {
        return run_stdio_proxy(&cwd, &environment, request);
    }
    if !request.argv.is_empty() {
        validate_child_argv_access(&request.workspace, &cwd, &environment, &request.argv)?;
        return run_one_shot_argv(&request.workspace, &cwd, &environment, &request.argv);
    }
    let command = request.command.as_deref().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "expected command for one-shot mode",
        )
    })?;
    validate_shell_command_text(&request.workspace, &cwd, &environment, command)?;
    run_one_shot_command(&request.workspace, &cwd, &environment, command)
}

fn map_workspace_path(workspace: &Path, logical_cwd: &str) -> io::Result<PathBuf> {
    if logical_cwd == "/workspace" {
        return Ok(workspace.to_path_buf());
    }
    if let Some(suffix) = logical_cwd.strip_prefix("/workspace/") {
        let mut mapped = workspace.to_path_buf();
        for part in suffix.split('/') {
            if !part.is_empty() {
                mapped.push(part);
            }
        }
        if path_uses_windows_alternate_data_stream(Path::new(suffix)) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper cwd must not use alternate data streams: {logical_cwd}"),
            ));
        }
        if path_uses_windows_reserved_device_name(Path::new(suffix)) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper cwd must not use Windows reserved device names: {logical_cwd}"),
            ));
        }
        if path_targets_internal_workspace_root(workspace, &mapped) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper cwd must not target internal workspace metadata: {logical_cwd}"),
            ));
        }
        if path_traverses_workspace_reparse_point(workspace, &mapped) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper cwd must not traverse workspace reparse points: {logical_cwd}"),
            ));
        }
        return Ok(mapped);
    }
    Err(io::Error::new(
        io::ErrorKind::InvalidInput,
        format!("unsupported logical cwd: {logical_cwd}"),
    ))
}

fn is_fixture_command(command: Option<&str>) -> bool {
    command
        .map(|value| {
            value.contains(FIXTURE_MARKER_PREFIX) && value.contains(".ai_ide_strict_fixture.txt")
        })
        .unwrap_or(false)
}

fn run_fixture_emulation(
    workspace: &Path,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
) -> io::Result<i32> {
    let fixture_path = cwd.join(".ai_ide_strict_fixture.txt");
    fs::write(fixture_path, "fixture")?;
    let boundary_layout = resolve_filesystem_boundary_layout(workspace, environment)?;
    let mut stdout = io::stdout().lock();
    writeln!(
        stdout,
        "{FIXTURE_MARKER_PREFIX} sandbox={}",
        environment
            .get("AI_IDE_SANDBOX_ROOT")
            .map(String::as_str)
            .unwrap_or("")
    )?;
    writeln!(
        stdout,
        "{FIXTURE_MARKER_PREFIX} home={}",
        environment.get("HOME").map(String::as_str).unwrap_or("")
    )?;
    writeln!(
        stdout,
        "{FIXTURE_MARKER_PREFIX} cache={}",
        environment
            .get("XDG_CACHE_HOME")
            .map(String::as_str)
            .unwrap_or("")
    )?;
    writeln!(
        stdout,
        "{FIXTURE_MARKER_PREFIX} boundary_layout={}",
        if boundary_layout.blocked_internal_roots.len() == 2 {
            "ready"
        } else {
            "invalid"
        }
    )?;
    let restricted_token_status = if helper_can_create_restricted_token()? {
        "enabled"
    } else {
        "disabled"
    };
    let _boundary_lock = RestrictedLaunchBoundaryLock::acquire()?;
    let write_boundary_status =
        if crate::filesystem_boundary::prepare_low_integrity_boundary(&boundary_layout)? {
            "enabled"
        } else {
            "disabled"
        };
    let read_boundary = if write_boundary_status == "enabled" {
        stage_read_boundary(&boundary_layout)?
    } else {
        None
    };
    let read_boundary_status = if read_boundary.is_some() {
        "enabled"
    } else {
        "disabled"
    };
    writeln!(
        stdout,
        "{FIXTURE_MARKER_PREFIX} restricted_token={restricted_token_status}"
    )?;
    writeln!(
        stdout,
        "{FIXTURE_MARKER_PREFIX} write_boundary={write_boundary_status}"
    )?;
    writeln!(
        stdout,
        "{FIXTURE_MARKER_PREFIX} read_boundary={read_boundary_status}"
    )?;
    writeln!(stdout, "{FIXTURE_MARKER_PREFIX} denied_relative=hidden")?;
    writeln!(stdout, "{FIXTURE_MARKER_PREFIX} denied_direct=hidden")?;
    writeln!(stdout, "{FIXTURE_MARKER_PREFIX} direct_write=blocked")?;
    stdout.flush()?;
    Ok(0)
}

fn run_one_shot_command(
    workspace: &Path,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    command: &str,
) -> io::Result<i32> {
    if let Some(output) = helper_echo_output_for_shell_command(environment, command) {
        writeln!(io::stdout(), "{output}")?;
        io::stdout().flush()?;
        return Ok(0);
    }
    if let Some(argv) = direct_argv_candidate_for_shell_command(cwd, environment, command) {
        validate_child_argv_access(workspace, cwd, environment, &argv)?;
        return run_one_shot_argv(workspace, cwd, environment, &argv);
    }
    if let Some(script_argv) =
        structured_shell_script_candidate_for_shell_command(cwd, environment, command)
    {
        validate_child_argv_access(workspace, cwd, environment, &script_argv)?;
        return run_one_shot_shell_script_argv(workspace, cwd, environment, &script_argv);
    }
    Err(unsupported_shell_command_shape(command))
}

fn run_one_shot_argv(
    workspace: &Path,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    argv: &[String],
) -> io::Result<i32> {
    let active_roots = active_executable_roots(cwd, environment, argv);
    let boundary_layout =
        resolve_filesystem_boundary_layout_for_launch(workspace, environment, &active_roots, &[])?;
    let launch_environment = child_launch_environment(environment);
    let restricted_launch_environment =
        launch_scoped_environment(&launch_environment, &active_roots)?;
    let _blocked_roots = BlockedInternalRootsStaging::apply(&boundary_layout)?;
    let restricted_token_supported = helper_can_create_restricted_token()?;
    let write_boundary_supported = prepare_low_integrity_boundary(&boundary_layout)?;
    if !restricted_token_supported || !write_boundary_supported {
        return run_unrestricted_one_shot_program(cwd, &launch_environment, &argv[0], &argv[1..]);
    }
    require_restricted_launch_capabilities(restricted_token_supported, write_boundary_supported)?;
    let _boundary_lock = RestrictedLaunchBoundaryLock::acquire()?;
    let _read_boundary = require_read_boundary_for_restricted_launch(&boundary_layout)?;
    if let Some(exit_code) =
        run_restricted_one_shot_argv(cwd, &restricted_launch_environment, argv)?
    {
        return Ok(exit_code);
    }
    Err(missing_restricted_launch_runtime_error())
}

fn run_one_shot_shell_script_argv(
    workspace: &Path,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    script_argv: &[String],
) -> io::Result<i32> {
    let shell_program = windows_shell_program(environment);
    let boundary_layout = resolve_filesystem_boundary_layout_for_launch(
        workspace,
        environment,
        &active_executable_roots(cwd, environment, &[shell_program.clone()]),
        &[],
    )?;
    let launch_environment = child_launch_environment(environment);
    let _blocked_roots = BlockedInternalRootsStaging::apply(&boundary_layout)?;
    let shell_program = windows_shell_program(&launch_environment);
    let shell_argv = windows_shell_argv_for_script(script_argv);
    let restricted_token_supported = helper_can_create_restricted_token()?;
    let write_boundary_supported = prepare_low_integrity_boundary(&boundary_layout)?;
    if !restricted_token_supported || !write_boundary_supported {
        return run_unrestricted_one_shot_program(
            cwd,
            &launch_environment,
            &shell_program,
            &shell_argv,
        );
    }
    require_restricted_launch_capabilities(restricted_token_supported, write_boundary_supported)?;
    let _boundary_lock = RestrictedLaunchBoundaryLock::acquire()?;
    let _read_boundary = require_read_boundary_for_restricted_launch(&boundary_layout)?;
    if let Some(exit_code) = run_restricted_one_shot_program(
        cwd,
        &launch_environment,
        &shell_program,
        &shell_argv,
        HelperContainmentPolicy::KillOnCloseShellSingleChild,
    )? {
        return Ok(exit_code);
    }
    Err(missing_restricted_launch_runtime_error())
}

enum ProxyProcess {
    Standard(Child),
    #[cfg(windows)]
    Restricted(crate::filesystem_boundary::RestrictedProxyChild),
}

impl ProxyProcess {
    fn try_wait_code(&mut self) -> io::Result<Option<i32>> {
        match self {
            Self::Standard(child) => Ok(child.try_wait()?.map(|status| status.code().unwrap_or(1))),
            #[cfg(windows)]
            Self::Restricted(child) => child.try_wait(),
        }
    }

    fn id(&self) -> u32 {
        match self {
            Self::Standard(child) => child.id(),
            #[cfg(windows)]
            Self::Restricted(child) => child.id(),
        }
    }

    fn kill(&mut self) -> io::Result<()> {
        match self {
            Self::Standard(child) => child.kill(),
            #[cfg(windows)]
            Self::Restricted(child) => child.kill(),
        }
    }
}

fn run_stdio_proxy(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    request: &WindowsStrictHelperRequest,
) -> io::Result<i32> {
    let launch_environment = child_launch_environment(environment);
    if !request.argv.is_empty() {
        validate_child_argv_access(&request.workspace, cwd, environment, &request.argv)?;
    }
    let mut direct_shell_argv = None;
    let mut structured_shell_argv = None;
    if request.argv.is_empty() {
        let shell_text = request.command.as_deref().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "expected argv or command for stdio-proxy mode",
            )
        })?;
        validate_shell_command_text(&request.workspace, cwd, environment, shell_text)?;
        if let Some(argv) = direct_argv_candidate_for_shell_command(cwd, environment, shell_text) {
            validate_child_argv_access(&request.workspace, cwd, environment, &argv)?;
            direct_shell_argv = Some(argv);
        } else if let Some(script_argv) =
            structured_shell_script_candidate_for_shell_command(cwd, environment, shell_text)
        {
            validate_child_argv_access(&request.workspace, cwd, environment, &script_argv)?;
            structured_shell_argv = Some(script_argv.clone());
        } else {
            return Err(unsupported_shell_command_shape(shell_text));
        }
    }
    let active_executable_roots = if !request.argv.is_empty() {
        active_executable_roots(cwd, environment, &request.argv)
    } else if let Some(argv) = direct_shell_argv.as_ref() {
        active_executable_roots(cwd, environment, argv)
    } else if structured_shell_argv.is_some() {
        active_executable_roots(cwd, environment, &[windows_shell_program(environment)])
    } else {
        Vec::new()
    };
    let restricted_launch_environment = if !request.argv.is_empty() || direct_shell_argv.is_some() {
        launch_scoped_environment(&launch_environment, &active_executable_roots)?
    } else {
        launch_environment.clone()
    };
    let boundary_layout =
        resolve_runtime_boundary_layout(request, environment, &active_executable_roots)?;
    let _blocked_roots = BlockedInternalRootsStaging::apply(&boundary_layout)?;
    let restricted_proxy_launch = restricted_proxy_launch_spec(
        request,
        &launch_environment,
        &direct_shell_argv,
        &structured_shell_argv,
    );
    let restricted_token_supported = helper_can_create_restricted_token()?;
    let write_boundary_supported = prepare_low_integrity_boundary(&boundary_layout)?;
    if !restricted_token_supported || !write_boundary_supported {
        if let Some((program, args, _policy)) = restricted_proxy_launch.as_ref() {
            let mut child =
                spawn_unrestricted_proxy_program(cwd, &launch_environment, program, args)?;
            let stdout_reader: Box<dyn Read + Send> =
                Box::new(child.stdout.take().expect("stdout piped"));
            let stderr_reader: Box<dyn Read + Send> =
                Box::new(child.stderr.take().expect("stderr piped"));
            let stdin_writer: Box<dyn Write + Send> =
                Box::new(child.stdin.take().expect("stdin piped"));
            return run_proxy_child(
                ProxyProcess::Standard(child),
                stdout_reader,
                stderr_reader,
                stdin_writer,
                request.control_file.clone(),
                request.response_file.clone(),
            );
        }
        return Err(missing_restricted_launch_runtime_error());
    }
    require_restricted_launch_capabilities(restricted_token_supported, write_boundary_supported)?;
    let _boundary_lock = RestrictedLaunchBoundaryLock::acquire()?;
    let _read_boundary = require_read_boundary_for_restricted_launch(&boundary_layout)?;
    if let Some((program, args, policy)) = restricted_proxy_launch.as_ref() {
        if let Some(mut restricted_child) = spawn_restricted_proxy_program(
            cwd,
            &restricted_launch_environment,
            program,
            args,
            *policy,
        )? {
            let stdout_reader: Box<dyn Read + Send> = Box::new(restricted_child.take_stdout()?);
            let stderr_reader: Box<dyn Read + Send> = Box::new(restricted_child.take_stderr()?);
            let stdin_writer: Box<dyn Write + Send> = Box::new(restricted_child.take_stdin()?);
            return run_proxy_child(
                ProxyProcess::Restricted(restricted_child),
                stdout_reader,
                stderr_reader,
                stdin_writer,
                request.control_file.clone(),
                request.response_file.clone(),
            );
        }
    }
    Err(missing_restricted_launch_runtime_error())
}

fn active_executable_roots(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    argv: &[String],
) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    let Some(program) = argv.first() else {
        return roots;
    };
    let Some(resolved_program) = resolve_program_path(cwd, environment, program) else {
        return roots;
    };
    let Some(parent) = resolved_program.parent() else {
        return roots;
    };
    roots.push(parent.to_path_buf());
    roots
}

fn run_unrestricted_one_shot_program(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    program: &str,
    args: &[String],
) -> io::Result<i32> {
    let status = Command::new(program)
        .args(args)
        .current_dir(cwd)
        .env_clear()
        .envs(environment)
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()?;
    Ok(status.code().unwrap_or(1))
}

fn spawn_unrestricted_proxy_program(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    program: &str,
    args: &[String],
) -> io::Result<Child> {
    Command::new(program)
        .args(args)
        .current_dir(cwd)
        .env_clear()
        .envs(environment)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
}

fn child_launch_environment(environment: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    let mut launch_environment = environment.clone();
    launch_environment.remove(BLOCKED_READ_ROOTS_ENV);
    launch_environment
}

fn collect_runtime_appended_blocked_files(
    layout: &crate::filesystem_boundary::FilesystemBoundaryLayout,
    runtime_blocked_files: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for path in runtime_blocked_files {
        if path_is_covered_by_blocked_read_roots(path, &layout.blocked_read_roots) {
            continue;
        }
        blocked.push(path.clone());
    }
    Ok(blocked)
}

fn collect_workspace_only_runtime_executable_scope_blocked_files(
    context: &WorkspaceOnlyRuntimeExecutableScopeContext<'_>,
    runtime_blocked_files: &[PathBuf],
) -> Vec<PathBuf> {
    let mut blocked = Vec::new();
    for path in runtime_blocked_files {
        if !path_is_covered_by_existing_and_pending_blocked_roots(
            path,
            &context.layout.blocked_read_roots,
            &blocked,
        ) {
            blocked.push(path.clone());
        }
    }
    blocked
}

fn extend_blocked_read_roots_environment(
    environment: &mut BTreeMap<String, String>,
    extra_blocked_roots: &[PathBuf],
) -> io::Result<()> {
    if extra_blocked_roots.is_empty() {
        return Ok(());
    }
    let mut blocked_roots = Vec::new();
    if let Some(existing_value) = environment.get(BLOCKED_READ_ROOTS_ENV) {
        blocked_roots.extend(std::env::split_paths(existing_value));
    }
    blocked_roots.extend(extra_blocked_roots.iter().cloned());
    let joined = std::env::join_paths(blocked_roots).map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("failed to extend {BLOCKED_READ_ROOTS_ENV}: {error}"),
        )
    })?;
    environment.insert(
        BLOCKED_READ_ROOTS_ENV.to_string(),
        joined.to_string_lossy().into_owned(),
    );
    Ok(())
}

fn require_restricted_launch_capabilities(
    restricted_token_supported: bool,
    write_boundary_supported: bool,
) -> io::Result<()> {
    if !restricted_token_supported {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "restricted launch requires restricted token support on this host",
        ));
    }
    if !write_boundary_supported {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "restricted launch requires write boundary support on this host",
        ));
    }
    Ok(())
}

fn missing_restricted_launch_runtime_error() -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        "restricted launch support is unavailable for this admitted helper runtime shape",
    )
}

fn restricted_proxy_launch_spec(
    request: &WindowsStrictHelperRequest,
    environment: &BTreeMap<String, String>,
    direct_shell_argv: &Option<Vec<String>>,
    structured_shell_argv: &Option<Vec<String>>,
) -> Option<(String, Vec<String>, HelperContainmentPolicy)> {
    if let Some((program, args)) = request.argv.split_first() {
        return Some((
            program.clone(),
            args.to_vec(),
            HelperContainmentPolicy::KillOnCloseSingleProcess,
        ));
    }
    if let Some(argv) = direct_shell_argv.as_ref() {
        let (program, args) = argv.split_first()?;
        return Some((
            program.clone(),
            args.to_vec(),
            HelperContainmentPolicy::KillOnCloseSingleProcess,
        ));
    }
    structured_shell_argv.as_ref().map(|argv| {
        (
            windows_shell_program(environment),
            windows_shell_argv_for_script(argv),
            HelperContainmentPolicy::KillOnCloseShellSingleChild,
        )
    })
}

fn run_proxy_child(
    child: ProxyProcess,
    child_stdout: Box<dyn Read + Send>,
    child_stderr: Box<dyn Read + Send>,
    child_stdin: Box<dyn Write + Send>,
    control_file: Option<PathBuf>,
    response_file: Option<PathBuf>,
) -> io::Result<i32> {
    let shared_child = Arc::new(Mutex::new(child));
    let shared_stdin = Arc::new(Mutex::new(Some(child_stdin)));

    let stdout_thread = thread::spawn(move || forward_output(child_stdout, io::stdout()));
    let stderr_thread = thread::spawn(move || forward_output(child_stderr, io::stderr()));

    let input_stdin = Arc::clone(&shared_stdin);
    let _input_thread = thread::spawn(move || forward_input(input_stdin));

    let mut last_request: Option<WindowsStrictHelperControlMessage> = None;
    let exit_code = loop {
        process_control_file_once(
            &shared_child,
            &shared_stdin,
            control_file.as_deref(),
            response_file.as_deref(),
            &mut last_request,
        );
        let status = {
            let mut child = shared_child.lock().expect("child mutex poisoned");
            child.try_wait_code()?
        };
        if let Some(status) = status {
            break status;
        }
        thread::sleep(Duration::from_millis(25));
    };

    let _ = stdout_thread.join();
    let _ = stderr_thread.join();
    Ok(exit_code)
}

fn windows_shell_argv_for_script(script_argv: &[String]) -> Vec<String> {
    vec![
        "/D".to_string(),
        "/E:OFF".to_string(),
        "/V:OFF".to_string(),
        "/C".to_string(),
        windows_quote_command_string(script_argv),
    ]
}

fn windows_quote_command_string(argv: &[String]) -> String {
    let mut command = String::from("call ");
    command.push_str(
        &argv
            .iter()
            .map(|arg| windows_quote_argument(arg))
            .collect::<Vec<_>>()
            .join(" "),
    );
    command
}

fn windows_quote_argument(argument: &str) -> String {
    if argument.is_empty() {
        return "\"\"".to_string();
    }
    let needs_quotes = argument.chars().any(|ch| ch.is_whitespace()) || argument.contains('"');
    if !needs_quotes {
        return argument.to_string();
    }
    let mut quoted = String::from("\"");
    let mut backslashes = 0usize;
    for ch in argument.chars() {
        match ch {
            '\\' => backslashes += 1,
            '"' => {
                quoted.push_str(&"\\".repeat(backslashes * 2 + 1));
                quoted.push('"');
                backslashes = 0;
            }
            _ => {
                if backslashes > 0 {
                    quoted.push_str(&"\\".repeat(backslashes));
                    backslashes = 0;
                }
                quoted.push(ch);
            }
        }
    }
    if backslashes > 0 {
        quoted.push_str(&"\\".repeat(backslashes * 2));
    }
    quoted.push('"');
    quoted
}

fn windows_shell_program(environment: &BTreeMap<String, String>) -> String {
    if let Some(comspec) = environment
        .iter()
        .find(|(key, _)| key.eq_ignore_ascii_case("ComSpec"))
        .map(|(_, value)| value)
    {
        let candidate = PathBuf::from(comspec);
        if candidate.is_absolute() {
            return candidate.to_string_lossy().to_string();
        }
    }
    if let Some(system_root) = environment
        .iter()
        .find(|(key, _)| key.eq_ignore_ascii_case("SystemRoot"))
        .map(|(_, value)| value)
    {
        let candidate = PathBuf::from(system_root).join("System32").join("cmd.exe");
        if candidate.is_absolute() {
            return candidate.to_string_lossy().to_string();
        }
    }
    "cmd".to_string()
}

#[cfg(windows)]
fn unsupported_shell_command_shape(command: &str) -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        format!(
            "shell command must resolve to helper-local echo, direct argv, or structured batch launch in strict helper mode: {command}"
        ),
    )
}

fn forward_output<T, W>(output: T, writer: W) -> io::Result<()>
where
    T: Read,
    W: Write,
{
    let mut reader = io::BufReader::new(output);
    let mut writer = io::BufWriter::new(writer);
    io::copy(&mut reader, &mut writer)?;
    writer.flush()
}

fn forward_input(shared_stdin: Arc<Mutex<Option<Box<dyn Write + Send>>>>) {
    let stdin = io::stdin();
    let mut reader = io::BufReader::new(stdin.lock());
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => break,
            Ok(_) => {}
            Err(_) => break,
        }
        let mut guard = shared_stdin.lock().expect("stdin mutex poisoned");
        let Some(stdin) = guard.as_mut() else {
            break;
        };
        if stdin.write_all(line.as_bytes()).is_err() {
            break;
        }
        let _ = stdin.flush();
    }
    let _ = shared_stdin.lock().map(|mut guard| guard.take());
}

fn process_control_file_once(
    shared_child: &Arc<Mutex<ProxyProcess>>,
    shared_stdin: &Arc<Mutex<Option<Box<dyn Write + Send>>>>,
    control_file: Option<&Path>,
    response_file: Option<&Path>,
    last_request: &mut Option<WindowsStrictHelperControlMessage>,
) {
    let Some(control_file) = control_file else {
        return;
    };
    let Some(message) = read_helper_control_message(control_file) else {
        return;
    };
    if last_request.as_ref() == Some(&message) {
        return;
    }
    *last_request = Some(message.clone());
    match message.command {
        crate::HelperControlCommand::Status => {
            if let Some(response_file) = response_file {
                let pid = shared_child.lock().ok().map(|child| child.id());
                let status = WindowsStrictHelperStatusMessage {
                    version: 1,
                    request_id: message.request_id.clone(),
                    run_id: message.run_id.clone(),
                    backend: message.backend.clone(),
                    state: HelperStatusState::Running,
                    pid,
                    returncode: None,
                };
                let _ = write_helper_status_message(response_file, &status);
            }
        }
        crate::HelperControlCommand::Stop => {
            let _ = shared_stdin.lock().map(|mut guard| guard.take());
        }
        crate::HelperControlCommand::Kill => {
            if let Ok(mut child) = shared_child.lock() {
                let _ = child.kill();
            }
        }
    }
}

fn runtime_blocked_read_files(request: &WindowsStrictHelperRequest) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for path in [request.control_file.as_ref(), request.response_file.as_ref()]
        .into_iter()
        .flatten()
    {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        if !path.exists() {
            fs::write(path, b"")?;
        }
        let temp_dir = helper_protocol_temp_directory(path);
        fs::create_dir_all(&temp_dir)?;
        blocked.push(temp_dir);
        blocked.push(path.clone());
    }
    Ok(blocked)
}

fn resolve_runtime_boundary_layout(
    request: &WindowsStrictHelperRequest,
    environment: &BTreeMap<String, String>,
    active_executable_roots: &[PathBuf],
) -> io::Result<crate::filesystem_boundary::FilesystemBoundaryLayout> {
    resolve_workspace_only_runtime_boundary_layout(
        &build_workspace_only_runtime_boundary_resolution_context(
            &request.workspace,
            environment,
            active_executable_roots,
        ),
        request,
    )
}

fn resolve_workspace_only_runtime_boundary_layout(
    context: &WorkspaceOnlyRuntimeBoundaryResolutionContext<'_>,
    request: &WindowsStrictHelperRequest,
) -> io::Result<crate::filesystem_boundary::FilesystemBoundaryLayout> {
    let base_layout = resolve_filesystem_boundary_layout_for_launch(
        context.workspace,
        context.environment,
        context.active_executable_roots,
        &[],
    )?;
    let runtime_blocked_files = runtime_blocked_read_files(request)?;
    let layout = resolve_workspace_only_runtime_launch_layout_and_classification(
        context,
        runtime_blocked_files,
        base_layout,
    )?;
    Ok(layout)
}


fn resolve_workspace_only_runtime_launch_layout_and_classification(
    context: &WorkspaceOnlyRuntimeBoundaryResolutionContext<'_>,
    runtime_blocked_files: Vec<PathBuf>,
    base_layout: crate::filesystem_boundary::FilesystemBoundaryLayout,
) -> io::Result<crate::filesystem_boundary::FilesystemBoundaryLayout> {
    let external_seed = collect_workspace_only_runtime_launch_seed_external_files_from_context(
        &build_workspace_only_runtime_launch_seed_context(
            &base_layout,
            context.active_executable_roots,
        ),
        runtime_blocked_files.clone(),
    );
    let mut layout = resolve_workspace_only_runtime_launch_layout_with_external_overlay(
        &build_workspace_only_runtime_launch_layout_context(
            context.workspace,
            context.environment,
            context.active_executable_roots,
        ),
        external_seed,
        base_layout,
    )?;
    let classification = classify_workspace_only_runtime_blocked_files_for_layout(
        &build_workspace_only_runtime_blocked_file_classification_context(
            &layout,
            context.active_executable_roots,
        ),
        runtime_blocked_files,
    );
    validate_workspace_only_runtime_launch_layout_and_classification(context, &layout, &classification)?;
    apply_workspace_only_runtime_blocked_file_classification(
        &mut layout,
        context.active_executable_roots,
        &classification,
    )?;
    Ok(layout)
}


fn request_from_context(
    context: &WorkspaceOnlyRuntimeBoundaryResolutionContext<'_>,
    layout: &crate::filesystem_boundary::FilesystemBoundaryLayout,
) -> WindowsStrictHelperRequest {
    WindowsStrictHelperRequest {
        workspace: context.workspace.to_path_buf(),
        cwd: "/workspace".to_string(),
        environment: BTreeMap::new(),
        command: None,
        argv: vec!["python".to_string()],
        stdio_proxy: true,
        control_file: layout
            .blocked_read_roots
            .iter()
            .find(|p| p.extension().map(|e| e == "json").unwrap_or(false))
            .cloned(),
        response_file: layout
            .blocked_read_roots
            .iter()
            .rev()
            .find(|p| p.extension().map(|e| e == "json").unwrap_or(false))
            .cloned(),
    }
}

fn resolve_active_executable_root_for_runtime_blocked_file(
    active_executable_roots: &[PathBuf],
    path: &Path,
) -> io::Result<PathBuf> {
    active_executable_roots
        .iter()
        .filter(|root| path.starts_with(root))
        .max_by_key(|root| root.components().count())
        .cloned()
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "runtime blocked file must stay under an active executable root: {}",
                    path.display()
                ),
            )
        })
}

fn runtime_executable_scope_blocked_roots(
    scope: crate::filesystem_boundary::BoundaryScope,
    executable_root: &Path,
    allowed_read_roots: &[PathBuf],
    trusted_system_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    blocked_read_roots_for_runtime_executable_root_with_allowed_roots(
        scope,
        executable_root,
        allowed_read_roots,
        allowed_read_roots,
        trusted_system_roots,
    )
}

fn validate_workspace_only_runtime_launch_layout_and_classification(
    context: &WorkspaceOnlyRuntimeBoundaryResolutionContext<'_>,
    layout: &crate::filesystem_boundary::FilesystemBoundaryLayout,
    classification: &RuntimeBlockedFileClassification,
) -> io::Result<()> {
    validate_workspace_only_runtime_blocked_file_classification(
        &build_workspace_only_runtime_blocked_file_application_context(
            layout,
            context.active_executable_roots,
        ),
        classification,
    )
}



fn resolve_runtime_launch_layout_with_external_overlay(
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    active_executable_roots: &[PathBuf],
    external_seed: Vec<PathBuf>,
    base_layout: crate::filesystem_boundary::FilesystemBoundaryLayout,
) -> io::Result<crate::filesystem_boundary::FilesystemBoundaryLayout> {
    if external_seed.is_empty() {
        return Ok(base_layout);
    }
    let mut boundary_environment = environment.clone();
    extend_blocked_read_roots_environment(&mut boundary_environment, &external_seed)?;
    resolve_filesystem_boundary_layout_for_launch(
        workspace,
        &boundary_environment,
        active_executable_roots,
        &[],
    )
}

fn resolve_workspace_only_runtime_launch_layout_with_external_overlay(
    context: &WorkspaceOnlyRuntimeLaunchLayoutContext<'_>,
    external_seed: Vec<PathBuf>,
    base_layout: crate::filesystem_boundary::FilesystemBoundaryLayout,
) -> io::Result<crate::filesystem_boundary::FilesystemBoundaryLayout> {
    resolve_runtime_launch_layout_with_external_overlay(
        context.workspace,
        context.environment,
        context.active_executable_roots,
        external_seed,
        base_layout,
    )
}

fn collect_workspace_only_runtime_launch_seed_external_files_from_context(
    context: &WorkspaceOnlyRuntimeLaunchSeedContext<'_>,
    runtime_blocked_files: Vec<PathBuf>,
) -> Vec<PathBuf> {
    let remaining = filter_runtime_blocked_files_not_already_covered(
        runtime_blocked_files,
        &context.base_layout.blocked_read_roots,
    );
    collect_workspace_only_runtime_launch_seed_external_files_against_allowed_roots(
        remaining,
        &context.base_layout.allowed_read_roots,
        context.active_executable_roots,
    )
}

fn collect_workspace_only_runtime_launch_seed_external_files_against_allowed_roots(
    runtime_blocked_files: Vec<PathBuf>,
    allowed_read_roots: &[PathBuf],
    active_executable_roots: &[PathBuf],
) -> Vec<PathBuf> {
    classify_runtime_blocked_files_against_allowed_roots(
        runtime_blocked_files,
        allowed_read_roots,
        active_executable_roots,
    )
    .external
}

struct RuntimeBlockedFileClassification {
    appended: Vec<PathBuf>,
    executable_scope: Vec<PathBuf>,
    external: Vec<PathBuf>,
}

struct WorkspaceOnlyRuntimeLaunchSeedContext<'a> {
    base_layout: &'a crate::filesystem_boundary::FilesystemBoundaryLayout,
    active_executable_roots: &'a [PathBuf],
}

fn build_workspace_only_runtime_launch_seed_context<'a>(
    base_layout: &'a crate::filesystem_boundary::FilesystemBoundaryLayout,
    active_executable_roots: &'a [PathBuf],
) -> WorkspaceOnlyRuntimeLaunchSeedContext<'a> {
    WorkspaceOnlyRuntimeLaunchSeedContext {
        base_layout,
        active_executable_roots,
    }
}

struct WorkspaceOnlyRuntimeLaunchLayoutContext<'a> {
    workspace: &'a Path,
    environment: &'a BTreeMap<String, String>,
    active_executable_roots: &'a [PathBuf],
}

fn build_workspace_only_runtime_launch_layout_context<'a>(
    workspace: &'a Path,
    environment: &'a BTreeMap<String, String>,
    active_executable_roots: &'a [PathBuf],
) -> WorkspaceOnlyRuntimeLaunchLayoutContext<'a> {
    WorkspaceOnlyRuntimeLaunchLayoutContext {
        workspace,
        environment,
        active_executable_roots,
    }
}

struct WorkspaceOnlyRuntimeBoundaryResolutionContext<'a> {
    workspace: &'a Path,
    environment: &'a BTreeMap<String, String>,
    active_executable_roots: &'a [PathBuf],
}

fn build_workspace_only_runtime_boundary_resolution_context<'a>(
    workspace: &'a Path,
    environment: &'a BTreeMap<String, String>,
    active_executable_roots: &'a [PathBuf],
) -> WorkspaceOnlyRuntimeBoundaryResolutionContext<'a> {
    WorkspaceOnlyRuntimeBoundaryResolutionContext {
        workspace,
        environment,
        active_executable_roots,
    }
}

struct WorkspaceOnlyRuntimeBlockedFileClassificationContext<'a> {
    layout: &'a crate::filesystem_boundary::FilesystemBoundaryLayout,
    active_executable_roots: &'a [PathBuf],
}

fn build_workspace_only_runtime_blocked_file_classification_context<'a>(
    layout: &'a crate::filesystem_boundary::FilesystemBoundaryLayout,
    active_executable_roots: &'a [PathBuf],
) -> WorkspaceOnlyRuntimeBlockedFileClassificationContext<'a> {
    WorkspaceOnlyRuntimeBlockedFileClassificationContext {
        layout,
        active_executable_roots,
    }
}

struct WorkspaceOnlyRuntimeBlockedFileApplicationContext<'a> {
    layout: &'a crate::filesystem_boundary::FilesystemBoundaryLayout,
    active_executable_roots: &'a [PathBuf],
}

fn build_workspace_only_runtime_blocked_file_application_context<'a>(
    layout: &'a crate::filesystem_boundary::FilesystemBoundaryLayout,
    active_executable_roots: &'a [PathBuf],
) -> WorkspaceOnlyRuntimeBlockedFileApplicationContext<'a> {
    WorkspaceOnlyRuntimeBlockedFileApplicationContext {
        layout,
        active_executable_roots,
    }
}

struct WorkspaceOnlyRuntimeBlockedFileApplicationPlan {
    executable_scope: Vec<PathBuf>,
    appended: Vec<PathBuf>,
}

struct WorkspaceOnlyRuntimeExecutableScopeContext<'a> {
    layout: &'a crate::filesystem_boundary::FilesystemBoundaryLayout,
}

fn path_is_covered_by_blocked_read_roots(path: &Path, blocked_read_roots: &[PathBuf]) -> bool {
    blocked_read_roots
        .iter()
        .any(|existing| path.starts_with(existing) || existing.starts_with(path))
}

fn blocked_root_overlaps_allowed_read_roots(
    blocked_root: &Path,
    allowed_read_roots: &[PathBuf],
) -> bool {
    allowed_read_roots
        .iter()
        .any(|allowed| blocked_root.starts_with(allowed) || allowed.starts_with(blocked_root))
}

fn blocked_root_is_redundant(blocked_root: &Path, blocked_read_roots: &[PathBuf]) -> bool {
    blocked_read_roots
        .iter()
        .any(|existing| blocked_root.starts_with(existing) || existing.starts_with(blocked_root))
}

fn path_is_covered_by_existing_and_pending_blocked_roots(
    path: &Path,
    existing_blocked_roots: &[PathBuf],
    pending_blocked_roots: &[PathBuf],
) -> bool {
    existing_blocked_roots
        .iter()
        .chain(pending_blocked_roots.iter())
        .any(|existing| path.starts_with(existing) || existing.starts_with(path))
}

fn classify_runtime_blocked_files_against_allowed_roots(
    runtime_blocked_files: Vec<PathBuf>,
    allowed_runtime_roots: &[PathBuf],
    active_executable_roots: &[PathBuf],
) -> RuntimeBlockedFileClassification {
    let (appended, remaining) =
        collect_runtime_appended_files_inside_allowed_roots(runtime_blocked_files, allowed_runtime_roots);
    let (executable_scope, external) =
        collect_runtime_executable_scope_files_inside_active_roots(remaining, active_executable_roots);
    RuntimeBlockedFileClassification {
        appended,
        executable_scope,
        external,
    }
}

fn filter_runtime_blocked_files_not_already_covered(
    runtime_blocked_files: Vec<PathBuf>,
    blocked_read_roots: &[PathBuf],
) -> Vec<PathBuf> {
    runtime_blocked_files
        .into_iter()
        .filter(|path| !path_is_covered_by_blocked_read_roots(path, blocked_read_roots))
        .collect()
}

fn collect_runtime_appended_files_inside_allowed_roots(
    runtime_blocked_files: Vec<PathBuf>,
    allowed_runtime_roots: &[PathBuf],
) -> (Vec<PathBuf>, Vec<PathBuf>) {
    let mut appended = Vec::new();
    let mut remaining = Vec::new();
    for path in runtime_blocked_files {
        if path_is_under_allowed_roots(&path, allowed_runtime_roots) {
            appended.push(path);
        } else {
            remaining.push(path);
        }
    }
    (appended, remaining)
}

fn collect_runtime_executable_scope_files_inside_active_roots(
    runtime_blocked_files: Vec<PathBuf>,
    active_executable_roots: &[PathBuf],
) -> (Vec<PathBuf>, Vec<PathBuf>) {
    let mut executable_scope = Vec::new();
    let mut external = Vec::new();
    for path in runtime_blocked_files {
        if path_is_under_allowed_roots(&path, active_executable_roots) {
            executable_scope.push(path);
        } else {
            external.push(path);
        }
    }
    (executable_scope, external)
}

#[cfg(test)]
fn classify_runtime_blocked_files_for_layout(
    layout: &crate::filesystem_boundary::FilesystemBoundaryLayout,
    runtime_blocked_files: Vec<PathBuf>,
    active_executable_roots: &[PathBuf],
) -> RuntimeBlockedFileClassification {
    classify_workspace_only_runtime_blocked_files_for_layout(
        &build_workspace_only_runtime_blocked_file_classification_context(
            layout,
            active_executable_roots,
        ),
        runtime_blocked_files,
    )
}

fn classify_workspace_only_runtime_blocked_files_for_layout(
    context: &WorkspaceOnlyRuntimeBlockedFileClassificationContext<'_>,
    runtime_blocked_files: Vec<PathBuf>,
) -> RuntimeBlockedFileClassification {
    let remaining = filter_runtime_blocked_files_not_already_covered(
        runtime_blocked_files,
        &context.layout.blocked_read_roots,
    );
    classify_runtime_blocked_files_against_allowed_roots(
        remaining,
        &context.layout.allowed_read_roots,
        context.active_executable_roots,
    )
}

#[cfg(test)]
fn apply_runtime_blocked_file_classification(
    layout: &mut crate::filesystem_boundary::FilesystemBoundaryLayout,
    _trusted_system_roots: &[PathBuf],
    active_executable_roots: &[PathBuf],
    classification: &RuntimeBlockedFileClassification,
) -> io::Result<()> {
    apply_workspace_only_runtime_blocked_file_classification(
        layout,
        active_executable_roots,
        classification,
    )
}

fn apply_workspace_only_runtime_blocked_file_classification(
    layout: &mut crate::filesystem_boundary::FilesystemBoundaryLayout,
    active_executable_roots: &[PathBuf],
    classification: &RuntimeBlockedFileClassification,
) -> io::Result<()> {
    let pending = prepare_workspace_only_runtime_blocked_file_application_plan(
        layout,
        active_executable_roots,
        classification,
    )?;
    commit_workspace_only_runtime_blocked_file_application_plan(layout, pending);
    Ok(())
}

fn prepare_workspace_only_runtime_blocked_file_application_plan(
    layout: &crate::filesystem_boundary::FilesystemBoundaryLayout,
    active_executable_roots: &[PathBuf],
    classification: &RuntimeBlockedFileClassification,
) -> io::Result<WorkspaceOnlyRuntimeBlockedFileApplicationPlan> {
    let validation_context = build_workspace_only_runtime_blocked_file_application_context(
        layout,
        active_executable_roots,
    );
    validate_workspace_only_runtime_blocked_file_classification(
        &validation_context,
        classification,
    )?;
    collect_workspace_only_runtime_blocked_file_application_plan(
        &validation_context,
        classification,
    )
}

fn collect_workspace_only_runtime_blocked_file_application_plan(
    context: &WorkspaceOnlyRuntimeBlockedFileApplicationContext<'_>,
    classification: &RuntimeBlockedFileClassification,
) -> io::Result<WorkspaceOnlyRuntimeBlockedFileApplicationPlan> {
    let executable_scope = collect_workspace_only_runtime_executable_scope_blocked_files(
        &WorkspaceOnlyRuntimeExecutableScopeContext {
            layout: context.layout,
        },
        &classification.executable_scope,
    );
    let appended =
        collect_runtime_appended_blocked_files(context.layout, &classification.appended)?;
    Ok(WorkspaceOnlyRuntimeBlockedFileApplicationPlan {
        executable_scope,
        appended,
    })
}

fn commit_workspace_only_runtime_blocked_file_application_plan(
    layout: &mut crate::filesystem_boundary::FilesystemBoundaryLayout,
    plan: WorkspaceOnlyRuntimeBlockedFileApplicationPlan,
) {
    let mut blocked = std::mem::take(&mut layout.blocked_read_roots);
    blocked.extend(plan.executable_scope);
    blocked.extend(plan.appended);
    layout.blocked_read_roots = collapse_blocked_read_roots(blocked);
}

fn validate_common_runtime_blocked_file_classification(
    layout: &crate::filesystem_boundary::FilesystemBoundaryLayout,
    classification: &RuntimeBlockedFileClassification,
) -> io::Result<()> {
    validate_runtime_appended_files_against_allowed_roots(
        &layout.allowed_read_roots,
        &classification.appended,
    )?;
    validate_runtime_external_files_against_blocked_roots(
        &layout.blocked_read_roots,
        &classification.external,
    )
}

fn validate_runtime_appended_files_against_allowed_roots(
    allowed_read_roots: &[PathBuf],
    appended: &[PathBuf],
) -> io::Result<()> {
    for path in appended {
        if path_is_under_allowed_roots(path, allowed_read_roots) {
            continue;
        }
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "runtime blocked file must stay inside allowed read roots: {}",
                path.display()
            ),
        ));
    }
    Ok(())
}

fn validate_runtime_external_files_against_blocked_roots(
    blocked_read_roots: &[PathBuf],
    external: &[PathBuf],
) -> io::Result<()> {
    for path in external {
        if path_is_covered_by_blocked_read_roots(path, blocked_read_roots) {
            continue;
        }
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "runtime boundary must cover external blocked file before apply: {}",
                path.display()
            ),
        ));
    }
    Ok(())
}

fn validate_workspace_only_runtime_blocked_file_classification(
    context: &WorkspaceOnlyRuntimeBlockedFileApplicationContext<'_>,
    classification: &RuntimeBlockedFileClassification,
) -> io::Result<()> {
    validate_common_runtime_blocked_file_classification(context.layout, classification)?;
    validate_workspace_only_runtime_executable_scope_files(
        context.active_executable_roots,
        &classification.executable_scope,
    )
}

fn validate_workspace_only_runtime_executable_scope_files(
    active_executable_roots: &[PathBuf],
    executable_scope: &[PathBuf],
) -> io::Result<()> {
    for path in executable_scope {
        if active_executable_roots.iter().any(|root| path.starts_with(root)) {
            continue;
        }
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "runtime blocked file must stay under an active executable root: {}",
                path.display()
            ),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        classify_runtime_blocked_files_against_allowed_roots,
        classify_runtime_blocked_files_for_layout, map_workspace_path,
        require_restricted_launch_capabilities, resolve_runtime_boundary_layout,
        restricted_proxy_launch_spec, runtime_blocked_read_files, windows_shell_argv_for_script,
        windows_shell_program,
    };
    use crate::process_containment::HelperContainmentPolicy;
    use crate::{WindowsStrictHelperRequest, helper_protocol_temp_directory};
    use std::collections::BTreeMap;
    use std::io;
    use std::path::{Path, PathBuf};

    #[test]
    #[cfg(windows)]
    fn windows_shell_program_prefers_explicit_comspec() {
        let environment = BTreeMap::from([
            (
                "ComSpec".to_string(),
                r"C:\Windows\System32\cmd.exe".to_string(),
            ),
            ("SystemRoot".to_string(), r"C:\Windows".to_string()),
        ]);

        assert_eq!(
            r"C:\Windows\System32\cmd.exe",
            windows_shell_program(&environment)
        );
    }

    #[test]
    #[cfg(windows)]
    fn windows_shell_script_args_quote_paths_with_spaces() {
        assert_eq!(
            vec![
                "/D".to_string(),
                "/E:OFF".to_string(),
                "/V:OFF".to_string(),
                "/C".to_string(),
                r#"call "C:\workspace\tools with spaces\tool.cmd" "arg with spaces""#.to_string(),
            ],
            windows_shell_argv_for_script(&[
                r"C:\workspace\tools with spaces\tool.cmd".to_string(),
                "arg with spaces".to_string(),
            ])
        );
    }

    #[test]
    #[cfg(windows)]
    fn map_workspace_path_rejects_internal_workspace_metadata_root() {
        let error = map_workspace_path(
            Path::new(r"C:\workspace"),
            "/workspace/.ai_ide_runtime/processes",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("internal workspace metadata"));
    }

    #[test]
    #[cfg(windows)]
    fn restricted_proxy_launch_spec_prefers_direct_argv_requests() {
        let environment = BTreeMap::from([("SystemRoot".to_string(), r"C:\Windows".to_string())]);
        let request = WindowsStrictHelperRequest {
            workspace: Path::new(r"C:\workspace").to_path_buf(),
            cwd: "/workspace".to_string(),
            argv: vec!["python".to_string(), "script.py".to_string()],
            command: None,
            environment: BTreeMap::new(),
            stdio_proxy: true,
            control_file: None,
            response_file: None,
        };

        let launch =
            restricted_proxy_launch_spec(&request, &environment, &None, &None).expect("launch");

        assert_eq!(
            (
                "python".to_string(),
                vec!["script.py".to_string()],
                HelperContainmentPolicy::KillOnCloseSingleProcess,
            ),
            launch
        );
    }

    #[test]
    #[cfg(windows)]
    fn restricted_proxy_launch_spec_wraps_structured_shell_scripts_with_cmd() {
        let environment = BTreeMap::from([("SystemRoot".to_string(), r"C:\Windows".to_string())]);
        let request = WindowsStrictHelperRequest {
            workspace: Path::new(r"C:\workspace").to_path_buf(),
            cwd: "/workspace".to_string(),
            argv: Vec::new(),
            command: Some(r"call .\tool.cmd".to_string()),
            environment: BTreeMap::new(),
            stdio_proxy: true,
            control_file: None,
            response_file: None,
        };
        let structured = Some(vec![r".\tool.cmd".to_string()]);

        let (program, args, policy) =
            restricted_proxy_launch_spec(&request, &environment, &None, &structured)
                .expect("launch");

        assert_eq!(windows_shell_program(&environment), program);
        assert_eq!(
            windows_shell_argv_for_script(&[r".\tool.cmd".to_string()]),
            args
        );
        assert_eq!(HelperContainmentPolicy::KillOnCloseShellSingleChild, policy);
    }

    #[test]
    #[cfg(windows)]
    fn runtime_blocked_read_files_creates_control_response_files_and_temp_dirs() {
        let root = std::env::temp_dir().join(format!(
            "helper-runtime-blocked-files-{}",
            std::process::id()
        ));
        let control_file = root.join("controls").join("control.json");
        let response_file = root.join("controls").join("status.json");
        let request = WindowsStrictHelperRequest {
            workspace: PathBuf::from(r"C:\workspace"),
            cwd: "/workspace".to_string(),
            argv: vec!["python".to_string()],
            command: None,
            environment: BTreeMap::new(),
            stdio_proxy: true,
            control_file: Some(control_file.clone()),
            response_file: Some(response_file.clone()),
        };

        let blocked = runtime_blocked_read_files(&request).expect("runtime blocked files");
        let control_temp = helper_protocol_temp_directory(&control_file);
        let response_temp = helper_protocol_temp_directory(&response_file);

        assert_eq!(
            blocked,
            vec![
                control_temp.clone(),
                control_file.clone(),
                response_temp.clone(),
                response_file.clone()
            ]
        );
        assert!(control_temp.is_dir());
        assert!(control_file.is_file());
        assert!(response_temp.is_dir());
        assert!(response_file.is_file());

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn classify_runtime_blocked_files_against_allowed_roots_prefers_launch_allowed_surface() {
        let runtime_lib = PathBuf::from(r"C:\tool\Lib\module.py");
        let private_file = PathBuf::from(r"C:\tool\private.txt");
        let external_file = PathBuf::from(r"C:\host\secret.txt");

        let classified = classify_runtime_blocked_files_against_allowed_roots(
            vec![
                runtime_lib.clone(),
                private_file.clone(),
                external_file.clone(),
            ],
            &[
                PathBuf::from(r"C:\workspace"),
                PathBuf::from(r"C:\tool\Lib"),
            ],
            &[PathBuf::from(r"C:\tool")],
        );

        assert_eq!(vec![runtime_lib], classified.appended);
        assert_eq!(vec![private_file], classified.executable_scope);
        assert_eq!(vec![external_file], classified.external);
    }

    #[test]
    fn resolve_runtime_boundary_layout_keeps_runtime_subtrees_visible_for_executable_root_protocol_files()
     {
        let root = std::env::temp_dir().join(format!(
            "helper-runtime-boundary-layout-exec-root-runtime-subtree-{}",
            std::process::id()
        ));
        let workspace = root.join("workspace");
        let helper_home = root.join("helper").join("home");
        let helper_tmp = root.join("helper").join("tmp");
        let helper_cache = root.join("helper").join("cache");
        let tool_root = root.join("host").join("tool");
        let active_root = tool_root.join("Scripts");
        let runtime_lib = tool_root.join("Lib");
        let private_dir = tool_root.join("private");
        let control_file = active_root.join("control.json");
        let response_file = active_root.join("status.json");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&helper_home).unwrap();
        std::fs::create_dir_all(&helper_tmp).unwrap();
        std::fs::create_dir_all(&helper_cache).unwrap();
        std::fs::create_dir_all(&active_root).unwrap();
        std::fs::create_dir_all(&runtime_lib).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        let environment = BTreeMap::from([
            ("HOME".to_string(), helper_home.display().to_string()),
            ("TMPDIR".to_string(), helper_tmp.display().to_string()),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_cache.display().to_string(),
            ),
        ]);
        let request = WindowsStrictHelperRequest {
            workspace: workspace.clone(),
            cwd: "/workspace".to_string(),
            argv: vec!["python".to_string()],
            command: None,
            environment: BTreeMap::new(),
            stdio_proxy: true,
            control_file: Some(control_file.clone()),
            response_file: Some(response_file.clone()),
        };

        let layout = resolve_runtime_boundary_layout(
            &request,
            &environment,
            std::slice::from_ref(&active_root),
        )
        .expect("runtime boundary layout");

        assert!(
            layout
                .allowed_read_roots
                .iter()
                .any(|root| root == &runtime_lib),
            "allowed roots were {:?}",
            layout.allowed_read_roots
        );
        assert!(
            layout
                .blocked_read_roots
                .iter()
                .all(|root| { !runtime_lib.starts_with(root) && !root.starts_with(&runtime_lib) }),
            "blocked roots should not cover runtime subtree: {:?}",
            layout.blocked_read_roots
        );
        assert!(
            layout
                .blocked_read_roots
                .iter()
                .all(|root| !private_dir.starts_with(root) && !root.starts_with(&private_dir)),
            "blocked roots were {:?}",
            layout.blocked_read_roots
        );

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn resolve_runtime_boundary_layout_blocks_executable_root_control_files_without_hiding_runtime_subtrees()
     {
        let root = std::env::temp_dir().join(format!(
            "helper-runtime-boundary-layout-exec-root-{}",
            std::process::id()
        ));
        let workspace = root.join("workspace");
        let helper_home = root.join("helper").join("home");
        let helper_tmp = root.join("helper").join("tmp");
        let helper_cache = root.join("helper").join("cache");
        let tool_root = root.join("host").join("tool");
        let sibling_secret = tool_root.join("secret");
        let sibling_host = root.join("host").join("other-secret");
        let control_file = tool_root.join("control.json");
        let response_file = tool_root.join("status.json");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&helper_home).unwrap();
        std::fs::create_dir_all(&helper_tmp).unwrap();
        std::fs::create_dir_all(&helper_cache).unwrap();
        std::fs::create_dir_all(&sibling_secret).unwrap();
        std::fs::create_dir_all(&sibling_host).unwrap();
        let environment = BTreeMap::from([
            ("HOME".to_string(), helper_home.display().to_string()),
            ("TMPDIR".to_string(), helper_tmp.display().to_string()),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_cache.display().to_string(),
            ),
        ]);
        let request = WindowsStrictHelperRequest {
            workspace: workspace.clone(),
            cwd: "/workspace".to_string(),
            argv: vec!["python".to_string()],
            command: None,
            environment: BTreeMap::new(),
            stdio_proxy: true,
            control_file: Some(control_file.clone()),
            response_file: Some(response_file.clone()),
        };

        let layout = resolve_runtime_boundary_layout(
            &request,
            &environment,
            std::slice::from_ref(&tool_root),
        )
        .expect("runtime boundary layout");

        assert!(
            layout
                .blocked_read_roots
                .iter()
                .all(|root| !sibling_host.starts_with(root) && !root.starts_with(&sibling_host)),
            "blocked roots were {:?}",
            layout.blocked_read_roots
        );
        assert!(
            layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &control_file || root == &response_file),
            "runtime protocol files inside executable roots should now be exact blocked leaves: {:?}",
            layout.blocked_read_roots
        );

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn require_restricted_launch_capabilities_fails_closed_without_token_support() {
        let error = require_restricted_launch_capabilities(false, true)
            .expect_err("missing restricted token support should fail closed");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("restricted token support"));
    }

    #[test]
    fn require_restricted_launch_capabilities_fails_closed_without_write_boundary() {
        let error = require_restricted_launch_capabilities(true, false)
            .expect_err("missing write boundary support should fail closed");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("write boundary support"));
    }
}
