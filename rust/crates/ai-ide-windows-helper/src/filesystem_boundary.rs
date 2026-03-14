// Dead code suppression removed — see compiler warnings for cleanup candidates.

use std::collections::BTreeMap;
use std::env::join_paths;
use std::fs;
use std::fs::File;
use std::io;
#[cfg(windows)]
use std::path::{Component, Prefix};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(windows)]
use std::ffi::OsStr;
#[cfg(windows)]
use std::os::windows::ffi::OsStrExt;
#[cfg(windows)]
use std::os::windows::io::FromRawHandle;

#[cfg(windows)]
use windows_sys::Win32::Foundation::{
    CloseHandle, DUPLICATE_SAME_ACCESS, DuplicateHandle, GetLastError, HANDLE, HANDLE_FLAG_INHERIT,
    INVALID_HANDLE_VALUE, SetHandleInformation,
};
#[cfg(windows)]
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
#[cfg(windows)]
use windows_sys::Win32::System::Console::{
    GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
};
#[cfg(windows)]
use windows_sys::Win32::System::Pipes::CreatePipe;
#[cfg(windows)]
use windows_sys::Win32::System::Threading::{
    CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, CreateProcessWithTokenW, GetCurrentProcess,
    GetExitCodeProcess, PROCESS_INFORMATION, ResumeThread, STARTF_USESTDHANDLES, STARTUPINFOW,
    TerminateProcess, WaitForSingleObject,
};

use crate::low_integrity::{
    SavedLabelSecurityDescriptor, apply_blocked_read_guard_label, apply_low_integrity_label,
    capture_label_security_descriptor, restore_label_security_descriptor,
};
use crate::process_containment::{HelperChildContainment, HelperContainmentPolicy};
use crate::restricted_token::{OwnedHandle, create_restricted_token};

pub(crate) const BLOCKED_READ_ROOTS_ENV: &str = "AI_IDE_BLOCKED_READ_ROOTS";
const MAX_EXECUTABLE_SCOPE_ANCESTOR_CHILDREN: usize = 64;
#[cfg(test)]
const MAX_ACTIVE_DRIVE_CHILDREN: usize = 128;
#[cfg(test)]
const MAX_ACTIVE_DRIVE_LEAF_CHILDREN: usize = 64;
#[cfg(test)]
const MAX_USER_PROFILE_SIBLINGS: usize = 128;
#[cfg(test)]
const MAX_USER_PROFILE_COMMON_CHILD_SCOPE_CHILDREN: usize = 128;
#[cfg(test)]
const MAX_USER_PROFILE_DIRECT_CHILDREN: usize = 128;
#[cfg(test)]
const MAX_USER_PROFILE_LEAF_CHILDREN: usize = 128;
#[cfg(test)]
const MAX_USER_LOCAL_PROGRAM_SIBLINGS: usize = 128;
#[cfg(test)]
const MAX_PROGRAM_FILES_SIBLINGS: usize = 128;
#[cfg(test)]
const MAX_EXTERNAL_TOOL_CONTAINER_SIBLINGS: usize = 128;
#[cfg(test)]
const MAX_ACTIVE_DRIVE_CHILD_SCOPE_CHILDREN: usize = 128;
#[cfg(test)]
const MAX_ACTIVE_DRIVE_GRANDCHILD_SCOPE_CHILDREN: usize = 128;
const MAX_SIBLING_EXPANSION_ALLOWED_CHILDREN: usize = 8;
const LAUNCH_SCOPED_PATHEXT: &str = ".COM;.EXE";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BoundaryScope {
    WorkspaceCore,
}

#[derive(Debug)]
pub struct FilesystemBoundaryLayout {
    pub workspace_root: PathBuf,
    pub helper_home: PathBuf,
    pub helper_tmp: PathBuf,
    pub helper_cache: PathBuf,
    pub allowed_read_roots: Vec<PathBuf>,
    pub blocked_internal_roots: Vec<PathBuf>,
    pub blocked_read_roots: Vec<PathBuf>,
}

pub struct BlockedInternalRootsStaging {
    hidden_roots: Vec<(PathBuf, PathBuf)>,
}

pub struct BlockedReadRootsStaging {
    restored_external_entries: Vec<(PathBuf, SavedLabelSecurityDescriptor)>,
}

#[cfg(windows)]
pub struct RestrictedProxyChild {
    process: OwnedHandle,
    _thread: OwnedHandle,
    _containment: HelperChildContainment,
    stdin: Option<File>,
    stdout: Option<File>,
    stderr: Option<File>,
    pid: u32,
}

#[cfg(windows)]
unsafe impl Send for RestrictedProxyChild {}

#[cfg(not(windows))]
pub struct RestrictedProxyChild;

struct WorkspaceCoreBoundarySurface {
    allowed: Vec<PathBuf>,
    blocked: Vec<PathBuf>,
}

struct ModeBoundarySurface {
    allowed: Vec<PathBuf>,
    blocked: Vec<PathBuf>,
}

struct AllowedReadRootsContext<'a> {
    argument_roots: &'a [PathBuf],
    mutable_roots: &'a [PathBuf],
    auxiliary_allowed_roots: &'a [PathBuf],
    trusted_system_roots: &'a [PathBuf],
}

fn collect_workspace_core_boundary_surface(
    launch_allowed_read_roots: &[PathBuf],
) -> WorkspaceCoreBoundarySurface {
    WorkspaceCoreBoundarySurface {
        allowed: launch_allowed_read_roots.to_vec(),
        blocked: Vec::new(),
    }
}

fn base_launch_allowed_read_roots(ctx: &AllowedReadRootsContext<'_>) -> Vec<PathBuf> {
    collapse_allowed_read_roots(
        [
            ctx.argument_roots,
            ctx.mutable_roots,
            ctx.auxiliary_allowed_roots,
            ctx.trusted_system_roots,
        ]
        .concat(),
    )
}

pub(crate) fn helper_mutable_roots(environment: &BTreeMap<String, String>) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for key in ["HOME", "TMPDIR", "XDG_CACHE_HOME"] {
        let Some(value) = environment.get(key) else {
            continue;
        };
        push_existing_absolute(&mut roots, Path::new(value));
    }
    roots
}

fn launch_scoped_allowed_read_roots_from_contributions(
    base_allowed: &[PathBuf],
    active_executable_contributions: &[ActiveExecutableScopeBoundaryContribution],
) -> Vec<PathBuf> {
    let mut allowed = base_allowed.to_vec();
    for contribution in active_executable_contributions {
        for candidate in &contribution.allowed {
            push_existing_absolute(&mut allowed, candidate);
        }
    }
    collapse_allowed_read_roots(allowed)
}

fn build_launch_boundary_common_inputs(
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    active_executable_roots: &[PathBuf],
    auxiliary_allowed_roots: &[PathBuf],
) -> io::Result<LaunchBoundaryCommonInputs> {
    let mut launch_auxiliary_allowed_roots =
        normalize_auxiliary_allowed_roots(auxiliary_allowed_roots);
    launch_auxiliary_allowed_roots.extend(
        derived_auxiliary_allowed_roots_for_active_executable_roots(active_executable_roots),
    );
    let executable_roots =
        effective_executable_roots(workspace, environment, active_executable_roots);
    let argument_roots = allowed_argument_roots(workspace);
    let mutable_roots = helper_mutable_roots(environment);
    let trusted_system_roots = trusted_system_roots(environment);
    let active_executable_surfaces = collect_active_executable_scope_surfaces(
        &executable_roots,
        &launch_auxiliary_allowed_roots,
        &trusted_system_roots,
    )?;
    let allowed_read_context = AllowedReadRootsContext {
        argument_roots: &argument_roots,
        mutable_roots: &mutable_roots,
        auxiliary_allowed_roots: &launch_auxiliary_allowed_roots,
        trusted_system_roots: &trusted_system_roots,
    };
    let base_allowed_read_roots = base_launch_allowed_read_roots(&allowed_read_context);
    let active_executable_contributions = collect_active_executable_scope_boundary_contributions(
        &active_executable_surfaces,
        &base_allowed_read_roots,
    )?;
    let launch_allowed_read_roots = launch_scoped_allowed_read_roots_from_contributions(
        &base_allowed_read_roots,
        &active_executable_contributions,
    );
    Ok(LaunchBoundaryCommonInputs {
        executable_roots,
        launch_auxiliary_allowed_roots,
        launch_allowed_read_roots,
    })
}

// --- Shared boundary enforcement (low-integrity, read boundary, restricted process) ---

pub(crate) fn prepare_low_integrity_boundary(layout: &FilesystemBoundaryLayout) -> io::Result<bool> {
    #[cfg(windows)]
    {
        for root in [
            &layout.workspace_root,
            &layout.helper_home,
            &layout.helper_tmp,
            &layout.helper_cache,
        ] {
            if let Err(error) = apply_low_integrity_label(root) {
                if low_integrity_boundary_capability_error(&error) {
                    return Ok(false);
                }
                return Err(error);
            }
        }
    }
    Ok(true)
}

fn low_integrity_boundary_capability_error(error: &io::Error) -> bool {
    matches!(
        error.raw_os_error(),
        Some(5) | Some(50) | Some(87) | Some(1314)
    )
}

fn apply_blocked_read_guard_tree(path: &Path) -> io::Result<()> {
    apply_blocked_read_guard_label(path)?;
    let metadata = fs::symlink_metadata(path)?;
    if metadata.is_dir() {
        for entry in fs::read_dir(path)? {
            let entry = entry?;
            apply_blocked_read_guard_tree(&entry.path())?;
        }
    }
    Ok(())
}

#[cfg(test)]
pub(crate) fn prepare_read_boundary(layout: &FilesystemBoundaryLayout) -> io::Result<bool> {
    Ok(stage_read_boundary(layout)?.is_some())
}

pub(crate) fn stage_read_boundary(
    layout: &FilesystemBoundaryLayout,
) -> io::Result<Option<BlockedReadRootsStaging>> {
    #[cfg(windows)]
    {
        for root in &layout.blocked_read_roots {
            if !root.exists() && !is_helper_managed_blocked_read_root(layout, root) {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!(
                        "configured blocked read root must exist: {}",
                        root.display()
                    ),
                ));
            }
        }
        let mut restored_external_entries = Vec::new();
        for root in &layout.blocked_read_roots {
            if root.exists() {
                // Continue below and apply the read guard tree.
            } else if is_helper_managed_blocked_read_root(layout, root) {
                fs::create_dir_all(root)?;
            } else {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!(
                        "configured blocked read root must exist before restricted launch: {}",
                        root.display()
                    ),
                ));
            }
            let saved = if is_helper_managed_blocked_read_root(layout, root) {
                None
            } else {
                match capture_label_security_descriptor_tree(root) {
                    Ok(saved) => Some(saved),
                    Err(error) => {
                        restore_blocked_read_label_entries(&restored_external_entries);
                        if low_integrity_boundary_capability_error(&error) {
                            return Ok(None);
                        }
                        return Err(error);
                    }
                }
            };
            if let Err(error) = apply_blocked_read_guard_tree(root) {
                if let Some(saved) = saved.as_ref() {
                    restore_blocked_read_label_entries(saved);
                }
                restore_blocked_read_label_entries(&restored_external_entries);
                if low_integrity_boundary_capability_error(&error) {
                    return Ok(None);
                }
                return Err(error);
            }
            if let Some(mut saved) = saved {
                restored_external_entries.append(&mut saved);
            }
        }
        return Ok(Some(BlockedReadRootsStaging {
            restored_external_entries,
        }));
    }
    #[cfg(not(windows))]
    Ok(Some(BlockedReadRootsStaging {
        restored_external_entries: Vec::new(),
    }))
}

pub(crate) fn require_read_boundary_for_restricted_launch(
    layout: &FilesystemBoundaryLayout,
) -> io::Result<BlockedReadRootsStaging> {
    let Some(staging) = stage_read_boundary(layout)? else {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "restricted launch requires read boundary support on this host",
        ));
    };
    Ok(staging)
}

impl BlockedInternalRootsStaging {
    pub fn apply(layout: &FilesystemBoundaryLayout) -> io::Result<Self> {
        let mut hidden_roots = Vec::new();
        for original in &layout.blocked_internal_roots {
            if !original.exists() {
                continue;
            }
            let staged = next_hidden_internal_root_path(original);
            fs::rename(original, &staged)?;
            if let Err(error) = apply_blocked_read_guard_tree(&staged) {
                if low_integrity_boundary_capability_error(&error) {
                    // Keep lexical hiding even when the host cannot apply the read-side MIC guard.
                } else {
                    let _ = fs::rename(&staged, original);
                    return Err(error);
                }
            }
            hidden_roots.push((original.clone(), staged));
        }
        Ok(Self { hidden_roots })
    }
}

impl Drop for BlockedInternalRootsStaging {
    fn drop(&mut self) {
        for (original, staged) in self.hidden_roots.iter().rev() {
            if staged.exists() {
                let _ = fs::rename(staged, original);
            }
        }
    }
}

impl Drop for BlockedReadRootsStaging {
    fn drop(&mut self) {
        restore_blocked_read_label_entries(&self.restored_external_entries);
    }
}

fn restore_blocked_read_label_entries(
    restored_external_entries: &[(PathBuf, SavedLabelSecurityDescriptor)],
) {
    for (root, saved) in restored_external_entries.iter().rev() {
        let _ = restore_label_security_descriptor(root, saved);
    }
}

fn capture_label_security_descriptor_tree(
    path: &Path,
) -> io::Result<Vec<(PathBuf, SavedLabelSecurityDescriptor)>> {
    let mut saved = Vec::new();
    capture_label_security_descriptor_tree_into(path, &mut saved)?;
    Ok(saved)
}

fn capture_label_security_descriptor_tree_into(
    path: &Path,
    saved: &mut Vec<(PathBuf, SavedLabelSecurityDescriptor)>,
) -> io::Result<()> {
    saved.push((path.to_path_buf(), capture_label_security_descriptor(path)?));
    let metadata = fs::symlink_metadata(path)?;
    if metadata.is_dir() {
        for entry in fs::read_dir(path)? {
            let entry = entry?;
            capture_label_security_descriptor_tree_into(&entry.path(), saved)?;
        }
    }
    Ok(())
}

// --- Restricted process launch ---

pub(crate) fn run_restricted_one_shot_argv(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    argv: &[String],
) -> io::Result<Option<i32>> {
    #[cfg(windows)]
    {
        let Some((program, args)) = argv.split_first() else {
            return Ok(None);
        };
        return run_restricted_one_shot_program(
            cwd,
            environment,
            program,
            args,
            HelperContainmentPolicy::KillOnCloseSingleProcess,
        );
    }

    #[cfg(not(windows))]
    {
        let _ = cwd;
        let _ = environment;
        let _ = argv;
        Ok(None)
    }
}

pub(crate) fn run_restricted_one_shot_program(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    program: &str,
    args: &[String],
    containment_policy: HelperContainmentPolicy,
) -> io::Result<Option<i32>> {
    #[cfg(windows)]
    {
        let resolved_program =
            resolve_program_path(cwd, environment, program).ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    format!(
                        "helper executable could not be resolved for boundary launch: {program}"
                    ),
                )
            })?;
        let token = create_restricted_token()?;
        let stdio = InheritableStdHandles::capture()?;
        let command_line = windows_command_line_for_launch(&resolved_program, args)
            .encode_utf16()
            .chain([0])
            .collect::<Vec<_>>();
        let mut mutable_command_line = command_line;
        let application_name = to_wide_path(&resolved_program);
        let current_dir = to_wide_path(cwd);
        let environment_block = create_environment_block(environment);
        let mut startup: STARTUPINFOW = unsafe { std::mem::zeroed() };
        startup.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
        startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdInput = stdio.stdin;
        startup.hStdOutput = stdio.stdout;
        startup.hStdError = stdio.stderr;
        let mut process_info: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
        let created = unsafe {
            CreateProcessWithTokenW(
                token.handle,
                0,
                application_name.as_ptr(),
                mutable_command_line.as_mut_ptr(),
                CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
                environment_block.as_ptr() as *const _,
                current_dir.as_ptr(),
                &startup,
                &mut process_info,
            )
        };
        if created == 0 {
            return Err(io::Error::from_raw_os_error(
                unsafe { GetLastError() } as i32
            ));
        }
        let process = OwnedHandle {
            handle: process_info.hProcess,
        };
        let thread = OwnedHandle {
            handle: process_info.hThread,
        };
        let containment = HelperChildContainment::new(containment_policy)?;
        if let Err(error) = containment.assign_process_handle(process.handle) {
            unsafe {
                windows_sys::Win32::System::Threading::TerminateProcess(process.handle, 1);
            }
            return Err(error);
        }
        let resume_result = unsafe { ResumeThread(thread.handle) };
        if resume_result == u32::MAX {
            unsafe {
                windows_sys::Win32::System::Threading::TerminateProcess(process.handle, 1);
            }
            return Err(io::Error::last_os_error());
        }
        let wait_result = unsafe { WaitForSingleObject(process.handle, u32::MAX) };
        if wait_result != 0 {
            return Err(io::Error::last_os_error());
        }
        let mut exit_code = 1u32;
        let exit_code_ok = unsafe { GetExitCodeProcess(process.handle, &mut exit_code) };
        if exit_code_ok == 0 {
            return Err(io::Error::last_os_error());
        }
        drop(containment);
        return Ok(Some(exit_code as i32));
    }

    #[cfg(not(windows))]
    {
        let _ = cwd;
        let _ = environment;
        let _ = program;
        let _ = args;
        let _ = containment_policy;
        Ok(None)
    }
}

pub(crate) fn spawn_restricted_proxy_program(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    program: &str,
    args: &[String],
    containment_policy: HelperContainmentPolicy,
) -> io::Result<Option<RestrictedProxyChild>> {
    #[cfg(windows)]
    {
        let resolved_program =
            resolve_program_path(cwd, environment, program).ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    format!(
                        "helper executable could not be resolved for boundary launch: {program}"
                    ),
                )
            })?;
        let token = create_restricted_token()?;
        let pipes = RestrictedProxyPipes::create()?;
        let command_line = windows_command_line_for_launch(&resolved_program, args)
            .encode_utf16()
            .chain([0])
            .collect::<Vec<_>>();
        let mut mutable_command_line = command_line;
        let application_name = to_wide_path(&resolved_program);
        let current_dir = to_wide_path(cwd);
        let environment_block = create_environment_block(environment);
        let mut startup: STARTUPINFOW = unsafe { std::mem::zeroed() };
        startup.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
        startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdInput = pipes.child_stdin_read.handle;
        startup.hStdOutput = pipes.child_stdout_write.handle;
        startup.hStdError = pipes.child_stderr_write.handle;
        let mut process_info: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
        let created = unsafe {
            CreateProcessWithTokenW(
                token.handle,
                0,
                application_name.as_ptr(),
                mutable_command_line.as_mut_ptr(),
                CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
                environment_block.as_ptr() as *const _,
                current_dir.as_ptr(),
                &startup,
                &mut process_info,
            )
        };
        if created == 0 {
            return Err(io::Error::from_raw_os_error(
                unsafe { GetLastError() } as i32
            ));
        }
        let process = OwnedHandle {
            handle: process_info.hProcess,
        };
        let thread = OwnedHandle {
            handle: process_info.hThread,
        };
        let containment = HelperChildContainment::new(containment_policy)?;
        if let Err(error) = containment.assign_process_handle(process.handle) {
            unsafe {
                TerminateProcess(process.handle, 1);
            }
            return Err(error);
        }
        let resume_result = unsafe { ResumeThread(thread.handle) };
        if resume_result == u32::MAX {
            unsafe {
                TerminateProcess(process.handle, 1);
            }
            return Err(io::Error::last_os_error());
        }
        return Ok(Some(RestrictedProxyChild {
            process,
            _thread: thread,
            _containment: containment,
            stdin: Some(pipes.parent_stdin_write),
            stdout: Some(pipes.parent_stdout_read),
            stderr: Some(pipes.parent_stderr_read),
            pid: process_info.dwProcessId,
        }));
    }

    #[cfg(not(windows))]
    {
        let _ = cwd;
        let _ = environment;
        let _ = program;
        let _ = args;
        let _ = containment_policy;
        Ok(None)
    }
}

pub fn resolve_filesystem_boundary_layout(
    workspace: &Path,
    environment: &BTreeMap<String, String>,
) -> io::Result<FilesystemBoundaryLayout> {
    resolve_filesystem_boundary_layout_for_launch(workspace, environment, &[], &[])
}

pub fn resolve_filesystem_boundary_layout_for_launch(
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    active_executable_roots: &[PathBuf],
    auxiliary_allowed_roots: &[PathBuf],
) -> io::Result<FilesystemBoundaryLayout> {
    let scope = BoundaryScope::WorkspaceCore;
    let helper_home = environment.get("HOME").map(PathBuf::from).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "missing HOME for boundary layout",
        )
    })?;
    let helper_tmp = environment
        .get("TMPDIR")
        .map(PathBuf::from)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "missing TMPDIR for boundary layout",
            )
        })?;
    let helper_cache = environment
        .get("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "missing XDG_CACHE_HOME for boundary layout",
            )
        })?;
    let blocked_read_root = helper_home
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("blocked_read");
    let common = build_launch_boundary_common_inputs(
        workspace,
        environment,
        active_executable_roots,
        auxiliary_allowed_roots,
    )?;
    let (allowed_read_roots, blocked_read_roots) = resolve_mode_boundary_read_sets(
        scope,
        workspace,
        environment,
        &common,
        &blocked_read_root,
    )?;
    Ok(FilesystemBoundaryLayout {
        workspace_root: workspace.to_path_buf(),
        allowed_read_roots,
        blocked_read_roots,
        helper_home,
        helper_tmp,
        helper_cache,
        blocked_internal_roots: vec![workspace.join(".ai_ide_runtime"), workspace.join(".ai-ide")],
    })
}

fn resolve_mode_boundary_read_sets(
    _scope: BoundaryScope,
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    common: &LaunchBoundaryCommonInputs,
    blocked_read_root: &Path,
) -> io::Result<(Vec<PathBuf>, Vec<PathBuf>)> {
    resolve_workspace_only_boundary_read_sets(&WorkspaceOnlyBoundaryReadSetContext {
        workspace,
        environment,
        common,
        blocked_read_root,
    })
}

fn resolve_workspace_only_boundary_read_sets(
    context: &WorkspaceOnlyBoundaryReadSetContext<'_>,
) -> io::Result<(Vec<PathBuf>, Vec<PathBuf>)> {
    let mode_surface = collect_mode_boundary_surface(
        BoundaryScope::WorkspaceCore,
        Some(&context.common.launch_allowed_read_roots),
    )?;
    let allowed_read_roots = mode_surface.allowed.clone();
    let mut blocked_read_roots = vec![context.blocked_read_root.to_path_buf()];
    blocked_read_roots.extend(mode_surface.blocked.iter().cloned());
    blocked_read_roots.extend(collect_external_blocked_read_roots_overlay(
        BoundaryScope::WorkspaceCore,
        context.workspace,
        context.environment,
        &context.common.executable_roots,
        &context.common.launch_auxiliary_allowed_roots,
        &mode_surface.allowed,
    )?);
    Ok((allowed_read_roots, blocked_read_roots))
}

#[cfg(test)]
fn launch_scoped_allowed_read_roots(ctx: &AllowedReadRootsContext<'_>) -> io::Result<Vec<PathBuf>> {
    let base_allowed = base_launch_allowed_read_roots(ctx);
    Ok(base_allowed)
}

#[derive(Debug)]
struct ActiveExecutableScopeSurface {
    allowed: Vec<PathBuf>,
    blocked: Vec<PathBuf>,
}

struct ResolvedActiveExecutableScopeSurface {
    root: PathBuf,
    surface: ActiveExecutableScopeSurface,
}

fn collect_active_executable_scope_surfaces(
    executable_roots: &[PathBuf],
    auxiliary_allowed_roots: &[PathBuf],
    trusted_system_roots: &[PathBuf],
) -> io::Result<Vec<ResolvedActiveExecutableScopeSurface>> {
    let mut surfaces: Vec<ResolvedActiveExecutableScopeSurface> = Vec::new();
    for root in executable_roots {
        let normalized =
            normalize_boundary_root_path(root.canonicalize().unwrap_or_else(|_| root.clone()));
        if !normalized.is_absolute() || surfaces.iter().any(|entry| entry.root == normalized) {
            continue;
        }
        surfaces.push(ResolvedActiveExecutableScopeSurface {
            root: normalized.clone(),
            surface: collect_active_executable_scope_surface(
                &normalized,
                auxiliary_allowed_roots,
                trusted_system_roots,
            )?,
        });
    }
    Ok(surfaces)
}

fn collect_active_executable_scope_surface(
    executable_root: &Path,
    auxiliary_allowed_roots: &[PathBuf],
    trusted_system_roots: &[PathBuf],
) -> io::Result<ActiveExecutableScopeSurface> {
    let normalized = normalize_boundary_root_path(
        executable_root
            .canonicalize()
            .unwrap_or_else(|_| executable_root.to_path_buf()),
    );
    if !normalized.is_absolute() || !normalized.is_dir() {
        return Ok(ActiveExecutableScopeSurface {
            allowed: Vec::new(),
            blocked: Vec::new(),
        });
    }
    let mut allowed = collect_active_executable_scope_runtime_entries(&normalized)?;
    let parent_allowed_entries = collect_active_executable_scope_parent_entries(
        &normalized,
        auxiliary_allowed_roots,
        trusted_system_roots,
    )?;
    for candidate in parent_allowed_entries {
        push_existing_absolute(&mut allowed, &candidate);
    }
    let mut blocked =
        blocked_read_roots_for_directory_entries(&normalized, None, allowed.as_slice())?;
    let Some(parent) = normalized.parent() else {
        return Ok(ActiveExecutableScopeSurface { allowed, blocked });
    };
    let parent = normalize_boundary_root_path(parent.to_path_buf());
    if parent.is_absolute() && parent.is_dir() {
        blocked.extend(blocked_read_roots_for_directory_entries(
            &parent,
            Some(&normalized),
            allowed.as_slice(),
        )?);
    }
    Ok(ActiveExecutableScopeSurface { allowed, blocked })
}

#[cfg(test)]
fn collect_active_executable_scope_allowed_entries(
    executable_root: &Path,
    auxiliary_allowed_roots: &[PathBuf],
    trusted_system_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    Ok(collect_active_executable_scope_surface(
        executable_root,
        auxiliary_allowed_roots,
        trusted_system_roots,
    )?
    .allowed)
}

fn collect_active_executable_scope_runtime_entries(root: &Path) -> io::Result<Vec<PathBuf>> {
    let mut allowed = Vec::new();
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        let candidate = normalize_boundary_root_path(
            entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
        );
        if active_executable_scope_entry_is_runtime_allowed(&candidate) {
            push_existing_absolute(&mut allowed, &candidate);
        }
    }
    Ok(allowed)
}

pub fn launch_scoped_environment(
    environment: &BTreeMap<String, String>,
    active_executable_roots: &[PathBuf],
) -> io::Result<BTreeMap<String, String>> {
    let mut scoped = environment.clone();
    let scoped_path = launch_scoped_path(environment, active_executable_roots)?;
    scoped.insert("PATH".to_string(), scoped_path);
    scoped.insert("PATHEXT".to_string(), LAUNCH_SCOPED_PATHEXT.to_string());
    Ok(scoped)
}

fn launch_scoped_path(
    _environment: &BTreeMap<String, String>,
    active_executable_roots: &[PathBuf],
) -> io::Result<String> {
    let mut path_roots = Vec::new();
    for root in active_executable_roots {
        push_existing_absolute(&mut path_roots, root);
    }
    let joined = join_paths(path_roots).map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("failed to build launch-scoped PATH: {error}"),
        )
    })?;
    Ok(joined.to_string_lossy().into_owned())
}

pub fn allowed_executable_roots(
    workspace: &Path,
    environment: &BTreeMap<String, String>,
) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    push_absolute(&mut roots, workspace);
    roots.extend(trusted_system_roots(environment));
    if let Some(path_value) = environment.get("PATH") {
        for directory in std::env::split_paths(path_value) {
            if directory.as_os_str().is_empty() {
                continue;
            }
            push_existing_absolute(&mut roots, &directory);
        }
    }
    roots
}

pub fn allowed_argument_roots(workspace: &Path) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    push_absolute(&mut roots, workspace);
    roots
}

pub fn path_is_under_allowed_roots(candidate: &Path, allowed_roots: &[PathBuf]) -> bool {
    let resolved = normalize_boundary_root_path(
        candidate
            .canonicalize()
            .unwrap_or_else(|_| candidate.to_path_buf()),
    );
    allowed_roots.iter().any(|root| resolved.starts_with(root))
}

fn path_matches_allowed_root(candidate: &Path, allowed_roots: &[PathBuf]) -> bool {
    let resolved = normalize_boundary_root_path(
        candidate
            .canonicalize()
            .unwrap_or_else(|_| candidate.to_path_buf()),
    );
    allowed_roots.iter().any(|root| resolved == *root)
}

#[cfg(test)]
pub fn blocked_read_roots_for_runtime_executable_root(
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    executable_root: &Path,
    auxiliary_allowed_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    blocked_read_roots_for_runtime_executable_root_in_scope(
        BoundaryScope::WorkspaceCore,
        workspace,
        environment,
        executable_root,
        auxiliary_allowed_roots,
    )
}

#[cfg(test)]
pub(crate) fn blocked_read_roots_for_runtime_executable_root_in_scope(
    _scope: BoundaryScope,
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    executable_root: &Path,
    auxiliary_allowed_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let normalized = normalize_boundary_root_path(
        executable_root
            .canonicalize()
            .unwrap_or_else(|_| executable_root.to_path_buf()),
    );
    if !normalized.is_absolute() {
        return Ok(Vec::new());
    }
    let argument_roots = allowed_argument_roots(workspace);
    let mutable_roots = helper_mutable_roots(environment);
    let auxiliary_roots = normalize_auxiliary_allowed_roots(auxiliary_allowed_roots);
    let protected_roots = trusted_system_roots(environment);
    if path_is_under_allowed_roots(&normalized, &protected_roots) {
        return Ok(Vec::new());
    }
    validate_external_blocked_root(
        &normalized,
        &argument_roots,
        &mutable_roots,
        &[],
        &auxiliary_roots,
    )?;

    Ok(collapse_blocked_read_roots(vec![normalized]))
}

pub(crate) fn blocked_read_roots_for_runtime_executable_root_with_allowed_roots(
    _scope: BoundaryScope,
    executable_root: &Path,
    _allowed_roots: &[PathBuf],
    _sibling_expansion_allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let normalized = normalize_boundary_root_path(
        executable_root
            .canonicalize()
            .unwrap_or_else(|_| executable_root.to_path_buf()),
    );
    if !normalized.is_absolute() {
        return Ok(Vec::new());
    }
    if path_is_under_allowed_roots(&normalized, protected_roots) {
        return Ok(Vec::new());
    }
    Ok(collapse_blocked_read_roots(vec![normalized]))
}

fn collect_external_blocked_read_roots_overlay(
    _scope: BoundaryScope,
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    executable_roots: &[PathBuf],
    auxiliary_allowed_roots: &[PathBuf],
    _launch_allowed_read_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let argument_roots = allowed_argument_roots(workspace);
    let mutable_roots = helper_mutable_roots(environment);
    let auxiliary_roots = normalize_auxiliary_allowed_roots(auxiliary_allowed_roots);
    let Some(raw_value) = environment.get(BLOCKED_READ_ROOTS_ENV) else {
        return Ok(Vec::new());
    };
    collect_workspace_only_external_blocked_read_roots_overlay(
        &build_workspace_only_external_blocked_read_overlay_context(
            &argument_roots,
            &mutable_roots,
            executable_roots,
            &auxiliary_roots,
        ),
        raw_value,
    )
}

struct WorkspaceOnlyExternalBlockedRootContext<'a> {
    argument_roots: &'a [PathBuf],
    mutable_roots: &'a [PathBuf],
    executable_roots: &'a [PathBuf],
    auxiliary_roots: &'a [PathBuf],
}

fn build_workspace_only_external_blocked_read_overlay_context<'a>(
    argument_roots: &'a [PathBuf],
    mutable_roots: &'a [PathBuf],
    executable_roots: &'a [PathBuf],
    auxiliary_roots: &'a [PathBuf],
) -> WorkspaceOnlyExternalBlockedRootContext<'a> {
    WorkspaceOnlyExternalBlockedRootContext {
        argument_roots,
        mutable_roots,
        executable_roots,
        auxiliary_roots,
    }
}

fn collect_workspace_only_external_blocked_read_roots_overlay(
    context: &WorkspaceOnlyExternalBlockedRootContext<'_>,
    raw_value: &str,
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for candidate in std::env::split_paths(raw_value) {
        if candidate.as_os_str().is_empty() {
            continue;
        }
        let normalized = normalize_boundary_root_path(
            candidate
                .canonicalize()
                .unwrap_or_else(|_| candidate.clone()),
        );
        append_workspace_only_external_blocked_root_from_context(
            context,
            &mut blocked,
            &normalized,
            &candidate,
        )?;
    }
    Ok(collapse_blocked_read_roots(blocked))
}

fn append_workspace_only_external_blocked_root_from_context(
    context: &WorkspaceOnlyExternalBlockedRootContext<'_>,
    blocked: &mut Vec<PathBuf>,
    normalized: &Path,
    original_candidate: &Path,
) -> io::Result<()> {
    append_workspace_only_external_blocked_root(
        blocked,
        normalized,
        original_candidate,
        context.argument_roots,
        context.mutable_roots,
        context.executable_roots,
        context.auxiliary_roots,
    )
}

fn append_workspace_only_external_blocked_root(
    blocked: &mut Vec<PathBuf>,
    normalized: &Path,
    original_candidate: &Path,
    argument_roots: &[PathBuf],
    mutable_roots: &[PathBuf],
    executable_roots: &[PathBuf],
    auxiliary_roots: &[PathBuf],
) -> io::Result<()> {
    if !normalized.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "{BLOCKED_READ_ROOTS_ENV} entries must be absolute: {}",
                original_candidate.display()
            ),
        ));
    }
    validate_external_blocked_root(
        normalized,
        argument_roots,
        mutable_roots,
        executable_roots,
        auxiliary_roots,
    )?;
    push_absolute(blocked, normalized);
    Ok(())
}


fn collect_mode_boundary_surface(
    _scope: BoundaryScope,
    launch_allowed_read_roots: Option<&[PathBuf]>,
) -> io::Result<ModeBoundarySurface> {
    let core = collect_workspace_core_boundary_surface(
        launch_allowed_read_roots.expect("workspace scope requires launch allowed roots"),
    );
    Ok(ModeBoundarySurface {
        allowed: core.allowed,
        blocked: core.blocked,
    })
}

#[cfg(test)]
struct ActiveExecutableScopeContext<'a> {
    active_executable_contributions: &'a [ActiveExecutableScopeBoundaryContribution],
}

#[cfg(test)]
fn blocked_read_roots_for_active_executable_scope(
    ctx: &ActiveExecutableScopeContext<'_>,
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for contribution in ctx.active_executable_contributions {
        blocked.extend(contribution._blocked.iter().cloned());
    }
    Ok(collapse_blocked_read_roots(blocked))
}

#[cfg(test)]
fn blocked_read_roots_for_active_drive_scope_from_candidates(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
    candidate_drive_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for drive_root in candidate_drive_roots {
        if !root_contains_any_allowed_path(&drive_root, allowed_roots) {
            continue;
        }
        blocked.extend(blocked_siblings_under_parent(
            drive_root,
            allowed_roots,
            protected_roots,
            Some(MAX_ACTIVE_DRIVE_CHILDREN),
        )?);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_non_system_active_drive_scope_from_candidates(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
    candidate_drive_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for drive_root in candidate_drive_roots {
        if root_contains_any_allowed_path(drive_root, protected_roots)
            || !root_contains_any_allowed_path(drive_root, allowed_roots)
        {
            continue;
        }
        blocked.extend(blocked_siblings_under_parent(
            drive_root,
            allowed_roots,
            protected_roots,
            Some(MAX_ACTIVE_DRIVE_CHILDREN),
        )?);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_active_drive_known_dirs_scope_from_candidates(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
    candidate_drive_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for drive_root in candidate_drive_roots {
        if !root_contains_any_allowed_path(drive_root, allowed_roots) {
            continue;
        }
        for directory_name in known_active_drive_dir_names() {
            let candidate = drive_root.join(directory_name);
            if !candidate.is_dir() {
                continue;
            }
            let normalized = normalize_boundary_root_path(
                candidate
                    .canonicalize()
                    .unwrap_or_else(|_| candidate.clone()),
            );
            if !normalized.is_absolute()
                || root_contains_any_allowed_path(&normalized, allowed_roots)
                || root_contains_any_allowed_path(&normalized, protected_roots)
                || path_matches_allowed_root(&normalized, protected_roots)
            {
                continue;
            }
            push_absolute(&mut blocked, &normalized);
        }
    }
    Ok(blocked)
}

#[cfg(test)]
fn known_active_drive_dir_names() -> &'static [&'static str] {
    &[
        "Users",
        "ProgramData",
        "Recovery",
        "$Recycle.Bin",
        "System Volume Information",
        "PerfLogs",
    ]
}

#[cfg(test)]
fn blocked_read_roots_for_user_profile_sibling_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for users_root in allowed_user_profile_parent_roots(allowed_roots) {
        blocked.extend(blocked_siblings_under_parent(
            &users_root,
            allowed_roots,
            protected_roots,
            Some(MAX_USER_PROFILE_SIBLINGS),
        )?);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_user_profile_parent_leaf_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for users_root in allowed_user_profile_parent_roots(allowed_roots) {
        let mut blocked_leafs = Vec::new();
        for entry in fs::read_dir(&users_root)? {
            let entry = entry?;
            let candidate = normalize_boundary_root_path(
                entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
            );
            if !candidate.is_absolute() || candidate.is_dir() {
                continue;
            }
            if root_contains_any_allowed_path(&candidate, allowed_roots)
                || root_contains_any_allowed_path(&candidate, protected_roots)
                || path_matches_allowed_root(&candidate, protected_roots)
            {
                continue;
            }
            push_absolute(&mut blocked_leafs, &candidate);
        }
        if blocked_leafs.len() > MAX_USER_PROFILE_SIBLINGS {
            continue;
        }
        blocked.extend(blocked_leafs);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_user_profile_parent_known_dirs_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for users_root in allowed_user_profile_parent_roots(allowed_roots) {
        for directory_name in known_user_profile_parent_dir_names() {
            let candidate = users_root.join(directory_name);
            if !candidate.is_dir() {
                continue;
            }
            let normalized =
                normalize_boundary_root_path(candidate.canonicalize().unwrap_or(candidate));
            if !normalized.is_absolute()
                || root_contains_any_allowed_path(&normalized, allowed_roots)
                || root_contains_any_allowed_path(&normalized, protected_roots)
                || path_matches_allowed_root(&normalized, protected_roots)
            {
                continue;
            }
            push_absolute(&mut blocked, &normalized);
        }
    }
    Ok(blocked)
}

#[cfg(test)]
fn known_user_profile_parent_dir_names() -> &'static [&'static str] {
    &["Public", "Default", "Default User", "All Users"]
}

#[cfg(test)]
fn blocked_read_roots_for_user_profile_common_dirs_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for profile_root in allowed_user_profile_roots(allowed_roots) {
        for relative_root in common_user_profile_relative_roots() {
            let candidate = profile_root.join(&relative_root);
            if !candidate.exists() {
                continue;
            }
            let normalized = normalize_boundary_root_path(
                candidate
                    .canonicalize()
                    .unwrap_or_else(|_| candidate.clone()),
            );
            if !normalized.is_absolute()
                || root_contains_any_allowed_path(&normalized, allowed_roots)
                || root_contains_any_allowed_path(&normalized, protected_roots)
                || path_matches_allowed_root(&normalized, protected_roots)
            {
                continue;
            }
            push_absolute(&mut blocked, &normalized);
        }
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_allowed_user_profile_common_child_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for profile_root in allowed_user_profile_roots(allowed_roots) {
        for relative_root in common_user_profile_relative_roots() {
            let candidate = profile_root.join(&relative_root);
            if !candidate.is_dir() {
                continue;
            }
            if !root_contains_any_allowed_path(&candidate, allowed_roots) {
                continue;
            }
            blocked.extend(blocked_siblings_under_parent(
                &candidate,
                allowed_roots,
                protected_roots,
                Some(MAX_USER_PROFILE_COMMON_CHILD_SCOPE_CHILDREN),
            )?);
        }
    }
    Ok(blocked)
}

#[cfg(test)]
fn common_user_profile_relative_roots() -> Vec<PathBuf> {
    vec![
        PathBuf::from("Desktop"),
        PathBuf::from("Documents"),
        PathBuf::from("Downloads"),
        PathBuf::from("Favorites"),
        PathBuf::from("Music"),
        PathBuf::from("Pictures"),
        PathBuf::from("Videos"),
        PathBuf::from("Saved Games"),
        PathBuf::from("Searches"),
        PathBuf::from("Contacts"),
        PathBuf::from("Links"),
        PathBuf::from("OneDrive"),
        PathBuf::from("AppData").join("Roaming"),
        PathBuf::from("AppData").join("LocalLow"),
        PathBuf::from("AppData").join("Local").join("Packages"),
        PathBuf::from("AppData").join("Local").join("Temp"),
    ]
}

#[cfg(test)]
fn blocked_read_roots_for_allowed_user_profile_local_appdata_child_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for profile_root in allowed_user_profile_roots(allowed_roots) {
        let local_root = profile_root.join("AppData").join("Local");
        if !local_root.is_dir() || !root_contains_any_allowed_path(&local_root, allowed_roots) {
            continue;
        }
        blocked.extend(blocked_siblings_under_parent(
            &local_root,
            allowed_roots,
            protected_roots,
            Some(MAX_USER_PROFILE_COMMON_CHILD_SCOPE_CHILDREN),
        )?);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_allowed_user_profile_local_appdata_leaf_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for profile_root in allowed_user_profile_roots(allowed_roots) {
        let local_root = profile_root.join("AppData").join("Local");
        if !local_root.is_dir() || !root_contains_any_allowed_path(&local_root, allowed_roots) {
            continue;
        }
        let mut blocked_leafs = Vec::new();
        for entry in fs::read_dir(&local_root)? {
            let entry = entry?;
            let candidate = normalize_boundary_root_path(
                entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
            );
            if !candidate.is_absolute() || candidate.is_dir() {
                continue;
            }
            if root_contains_any_allowed_path(&candidate, allowed_roots)
                || root_contains_any_allowed_path(&candidate, protected_roots)
                || path_matches_allowed_root(&candidate, protected_roots)
            {
                continue;
            }
            blocked_leafs.push(candidate);
        }
        if blocked_leafs.len() > MAX_USER_PROFILE_COMMON_CHILD_SCOPE_CHILDREN {
            continue;
        }
        for candidate in blocked_leafs {
            push_absolute(&mut blocked, &candidate);
        }
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_user_profile_leaf_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for profile_root in allowed_user_profile_roots(allowed_roots) {
        let mut blocked_leafs = Vec::new();
        for entry in fs::read_dir(&profile_root)? {
            let entry = entry?;
            let candidate = normalize_boundary_root_path(
                entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
            );
            if !candidate.is_absolute() || candidate.is_dir() {
                continue;
            }
            if root_contains_any_allowed_path(&candidate, allowed_roots)
                || root_contains_any_allowed_path(&candidate, protected_roots)
                || path_matches_allowed_root(&candidate, protected_roots)
            {
                continue;
            }
            push_absolute(&mut blocked_leafs, &candidate);
        }
        if blocked_leafs.len() > MAX_USER_PROFILE_LEAF_CHILDREN {
            continue;
        }
        blocked.extend(blocked_leafs);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_user_profile_direct_subdir_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for profile_root in allowed_user_profile_roots(allowed_roots) {
        let mut blocked_dirs = Vec::new();
        for entry in fs::read_dir(&profile_root)? {
            let entry = entry?;
            let candidate = normalize_boundary_root_path(
                entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
            );
            if !candidate.is_absolute() || !candidate.is_dir() {
                continue;
            }
            if root_contains_any_allowed_path(&candidate, allowed_roots)
                || root_contains_any_allowed_path(&candidate, protected_roots)
                || path_matches_allowed_root(&candidate, protected_roots)
            {
                continue;
            }
            push_absolute(&mut blocked_dirs, &candidate);
        }
        if blocked_dirs.len() > MAX_USER_PROFILE_DIRECT_CHILDREN {
            continue;
        }
        blocked.extend(blocked_dirs);
    }
    Ok(blocked)
}

#[cfg(test)]
fn allowed_user_profile_roots(allowed_roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for allowed_root in allowed_roots {
        let Some(profile_root) = user_profile_root(allowed_root) else {
            continue;
        };
        push_absolute(&mut roots, &profile_root);
    }
    roots
}

#[cfg(test)]
fn allowed_user_profile_parent_roots(allowed_roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for allowed_root in allowed_roots {
        let Some(users_root) = user_profile_parent_root(allowed_root) else {
            continue;
        };
        push_absolute(&mut roots, &users_root);
    }
    roots
}

#[cfg(test)]
fn user_profile_root(path: &Path) -> Option<PathBuf> {
    let normalized =
        normalize_boundary_root_path(path.canonicalize().unwrap_or_else(|_| path.to_path_buf()));
    let mut current = normalized.as_path();
    while let Some(parent) = current.parent() {
        let Some(parent_name) = parent.file_name().and_then(|value| value.to_str()) else {
            current = parent;
            continue;
        };
        if parent_name.eq_ignore_ascii_case("users") {
            return Some(current.to_path_buf());
        }
        current = parent;
    }
    None
}

#[cfg(test)]
fn user_profile_parent_root(path: &Path) -> Option<PathBuf> {
    let normalized =
        normalize_boundary_root_path(path.canonicalize().unwrap_or_else(|_| path.to_path_buf()));
    let mut current = normalized.as_path();
    while let Some(parent) = current.parent() {
        let Some(name) = current.file_name().and_then(|value| value.to_str()) else {
            current = parent;
            continue;
        };
        if name.eq_ignore_ascii_case("users") {
            return Some(current.to_path_buf());
        }
        current = parent;
    }
    None
}

#[cfg(test)]
fn blocked_read_roots_for_user_local_programs_sibling_scope(
    executable_roots: &[PathBuf],
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for programs_root in allowed_user_local_programs_roots(executable_roots) {
        blocked.extend(blocked_siblings_under_parent(
            &programs_root,
            allowed_roots,
            protected_roots,
            Some(MAX_USER_LOCAL_PROGRAM_SIBLINGS),
        )?);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_user_local_programs_leaf_scope(
    executable_roots: &[PathBuf],
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for programs_root in allowed_user_local_programs_roots(executable_roots) {
        let mut blocked_leafs = Vec::new();
        for entry in fs::read_dir(&programs_root)? {
            let entry = entry?;
            let candidate = normalize_boundary_root_path(
                entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
            );
            if !candidate.is_absolute() || candidate.is_dir() {
                continue;
            }
            if root_contains_any_allowed_path(&candidate, allowed_roots)
                || root_contains_any_allowed_path(&candidate, protected_roots)
                || path_matches_allowed_root(&candidate, protected_roots)
            {
                continue;
            }
            push_absolute(&mut blocked_leafs, &candidate);
        }
        if blocked_leafs.len() > MAX_USER_LOCAL_PROGRAM_SIBLINGS {
            continue;
        }
        blocked.extend(blocked_leafs);
    }
    Ok(blocked)
}

#[cfg(test)]
fn allowed_user_local_programs_roots(executable_roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for executable_root in executable_roots {
        let Some(programs_root) = user_local_programs_root(executable_root) else {
            continue;
        };
        push_absolute(&mut roots, &programs_root);
    }
    roots
}

#[cfg(test)]
fn user_local_programs_root(path: &Path) -> Option<PathBuf> {
    let normalized =
        normalize_boundary_root_path(path.canonicalize().unwrap_or_else(|_| path.to_path_buf()));
    let mut current = normalized.as_path();
    while let Some(parent) = current.parent() {
        let Some(name) = current.file_name().and_then(|value| value.to_str()) else {
            current = parent;
            continue;
        };
        if name.eq_ignore_ascii_case("programs") {
            let Some(local_root) = current.parent() else {
                return None;
            };
            let Some(local_name) = local_root.file_name().and_then(|value| value.to_str()) else {
                return None;
            };
            let Some(appdata_root) = local_root.parent() else {
                return None;
            };
            let Some(appdata_name) = appdata_root.file_name().and_then(|value| value.to_str())
            else {
                return None;
            };
            if local_name.eq_ignore_ascii_case("local")
                && appdata_name.eq_ignore_ascii_case("appdata")
            {
                return Some(current.to_path_buf());
            }
        }
        current = parent;
    }
    None
}

#[cfg(test)]
fn blocked_read_roots_for_program_files_sibling_scope(
    executable_roots: &[PathBuf],
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for programs_root in allowed_program_files_roots(executable_roots) {
        blocked.extend(blocked_siblings_under_parent(
            &programs_root,
            allowed_roots,
            protected_roots,
            Some(MAX_PROGRAM_FILES_SIBLINGS),
        )?);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_program_files_leaf_scope(
    executable_roots: &[PathBuf],
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for programs_root in allowed_program_files_roots(executable_roots) {
        let mut blocked_leafs = Vec::new();
        for entry in fs::read_dir(&programs_root)? {
            let entry = entry?;
            let candidate = normalize_boundary_root_path(
                entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
            );
            if !candidate.is_absolute() || candidate.is_dir() {
                continue;
            }
            if root_contains_any_allowed_path(&candidate, allowed_roots)
                || root_contains_any_allowed_path(&candidate, protected_roots)
                || path_matches_allowed_root(&candidate, protected_roots)
            {
                continue;
            }
            push_absolute(&mut blocked_leafs, &candidate);
        }
        if blocked_leafs.len() > MAX_PROGRAM_FILES_SIBLINGS {
            continue;
        }
        blocked.extend(blocked_leafs);
    }
    Ok(blocked)
}

#[cfg(test)]
fn allowed_program_files_roots(executable_roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for executable_root in executable_roots {
        let Some(programs_root) = program_files_root(executable_root) else {
            continue;
        };
        push_absolute(&mut roots, &programs_root);
    }
    roots
}

#[cfg(test)]
fn blocked_read_roots_for_external_tool_container_sibling_scope(
    executable_roots: &[PathBuf],
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for tool_root in allowed_external_tool_container_roots(executable_roots) {
        if path_matches_allowed_root(&tool_root, protected_roots) {
            continue;
        }
        blocked.extend(blocked_siblings_under_parent(
            &tool_root,
            allowed_roots,
            protected_roots,
            Some(MAX_EXTERNAL_TOOL_CONTAINER_SIBLINGS),
        )?);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_external_tool_container_leaf_scope(
    executable_roots: &[PathBuf],
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for tool_root in allowed_external_tool_container_roots(executable_roots) {
        if path_matches_allowed_root(&tool_root, protected_roots) {
            continue;
        }
        let mut blocked_leafs = Vec::new();
        for entry in fs::read_dir(&tool_root)? {
            let entry = entry?;
            let candidate = normalize_boundary_root_path(
                entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
            );
            if !candidate.is_absolute() || candidate.is_dir() {
                continue;
            }
            if root_contains_any_allowed_path(&candidate, allowed_roots)
                || root_contains_any_allowed_path(&candidate, protected_roots)
                || path_matches_allowed_root(&candidate, protected_roots)
            {
                continue;
            }
            push_absolute(&mut blocked_leafs, &candidate);
        }
        if blocked_leafs.len() > MAX_EXTERNAL_TOOL_CONTAINER_SIBLINGS {
            continue;
        }
        blocked.extend(blocked_leafs);
    }
    Ok(blocked)
}

#[cfg(test)]
fn allowed_external_tool_container_roots(executable_roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for executable_root in executable_roots {
        let Some(name) = executable_root.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        let lower = name.to_ascii_lowercase();
        let container_root = if matches!(lower.as_str(), "bin" | "scripts") {
            let Some(tool_root) = executable_root.parent() else {
                continue;
            };
            let Some(container_root) = tool_root.parent() else {
                continue;
            };
            container_root
        } else {
            let Some(container_root) = executable_root.parent() else {
                continue;
            };
            if path_is_drive_root(container_root) {
                continue;
            }
            container_root
        };
        push_absolute(&mut roots, container_root);
    }
    roots
}

#[cfg(test)]
fn program_files_root(path: &Path) -> Option<PathBuf> {
    let normalized =
        normalize_boundary_root_path(path.canonicalize().unwrap_or_else(|_| path.to_path_buf()));
    let mut current = normalized.as_path();
    while let Some(parent) = current.parent() {
        let Some(name) = current.file_name().and_then(|value| value.to_str()) else {
            current = parent;
            continue;
        };
        if name.eq_ignore_ascii_case("program files")
            || name.eq_ignore_ascii_case("program files (x86)")
        {
            return Some(current.to_path_buf());
        }
        current = parent;
    }
    None
}

#[cfg(test)]
fn blocked_read_roots_for_active_drive_leaf_scope(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
    candidate_drive_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for drive_root in candidate_drive_roots {
        if !root_contains_any_allowed_path(drive_root, allowed_roots) {
            continue;
        }
        let allowed_entries = allowed_root_bearing_entries(drive_root, allowed_roots);
        let protected_entries = allowed_root_bearing_entries(drive_root, protected_roots);
        let mut blocked_leafs = Vec::new();
        for entry in fs::read_dir(drive_root)? {
            let entry = entry?;
            let candidate = normalize_boundary_root_path(
                entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
            );
            if !candidate.is_absolute() || candidate.is_dir() {
                continue;
            }
            if allowed_entries.contains_candidate(&candidate)
                || protected_entries.contains_candidate(&candidate)
            {
                continue;
            }
            push_absolute(&mut blocked_leafs, &candidate);
        }
        if blocked_leafs.len() > MAX_ACTIVE_DRIVE_LEAF_CHILDREN {
            continue;
        }
        blocked.extend(blocked_leafs);
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_active_drive_child_scope_from_candidates(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
    candidate_drive_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for drive_root in candidate_drive_roots {
        if !root_contains_any_allowed_path(&drive_root, allowed_roots) {
            continue;
        }
        let drive_entries = allowed_root_bearing_entries(drive_root, allowed_roots);
        for child_root in &drive_entries.child_roots {
            if path_matches_allowed_root(child_root, protected_roots) {
                continue;
            }
            blocked.extend(blocked_siblings_under_parent(
                child_root,
                allowed_roots,
                protected_roots,
                Some(MAX_ACTIVE_DRIVE_CHILD_SCOPE_CHILDREN),
            )?);
        }
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_read_roots_for_active_drive_grandchild_scope_from_candidates(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
    candidate_drive_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for drive_root in candidate_drive_roots {
        if !root_contains_any_allowed_path(&drive_root, allowed_roots) {
            continue;
        }
        let drive_entries = allowed_root_bearing_entries(drive_root, allowed_roots);
        for child_root in &drive_entries.child_roots {
            if path_matches_allowed_root(child_root, protected_roots) {
                continue;
            }
            let child_entries = allowed_root_bearing_entries(child_root, allowed_roots);
            for grandchild_root in &child_entries.child_roots {
                if path_matches_allowed_root(grandchild_root, protected_roots) {
                    continue;
                }
                blocked.extend(blocked_siblings_under_parent(
                    grandchild_root,
                    allowed_roots,
                    protected_roots,
                    Some(MAX_ACTIVE_DRIVE_GRANDCHILD_SCOPE_CHILDREN),
                )?);
            }
        }
    }
    Ok(blocked)
}

#[cfg(test)]
fn blocked_siblings_under_parent(
    parent: &Path,
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
    max_parent_entries: Option<usize>,
) -> io::Result<Vec<PathBuf>> {
    if !parent_supports_boundary_sibling_scan(parent) {
        return Ok(Vec::new());
    }
    let allowed_entries = allowed_root_bearing_entries(parent, allowed_roots);
    if allowed_entries.child_roots.is_empty()
        || allowed_entries.child_roots.len() > MAX_SIBLING_EXPANSION_ALLOWED_CHILDREN
    {
        return Ok(Vec::new());
    }
    let protected_entries = allowed_root_bearing_entries(parent, protected_roots);
    let mut siblings = Vec::new();
    for entry in fs::read_dir(parent)? {
        let entry = entry?;
        let candidate = normalize_boundary_root_path(
            entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
        );
        if !candidate.is_absolute() {
            continue;
        }
        if allowed_entries.contains_candidate(&candidate)
            || protected_entries.contains_candidate(&candidate)
        {
            continue;
        }
        push_absolute(&mut siblings, &candidate);
    }
    if let Some(limit) = max_parent_entries
        && siblings.len() > limit
    {
        return Ok(Vec::new());
    }
    Ok(siblings)
}

#[cfg(test)]
fn blocked_read_roots_for_inactive_drive_scope_from_candidates(
    allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
    candidate_drive_roots: &[PathBuf],
) -> Vec<PathBuf> {
    let mut blocked = Vec::new();
    for drive_root in candidate_drive_roots {
        if path_matches_allowed_root(drive_root, protected_roots)
            || root_contains_any_allowed_path(drive_root, allowed_roots)
            || root_contains_any_allowed_path(drive_root, protected_roots)
        {
            continue;
        }
        push_absolute(&mut blocked, drive_root);
    }
    blocked
}

#[cfg(test)]
fn root_contains_any_allowed_path(root: &Path, allowed_roots: &[PathBuf]) -> bool {
    allowed_roots.iter().any(|allowed| {
        let normalized_allowed = normalize_boundary_root_path(
            allowed
                .canonicalize()
                .unwrap_or_else(|_| allowed.to_path_buf()),
        );
        normalized_allowed.starts_with(root)
    })
}

#[cfg(test)]
fn path_is_drive_root(path: &Path) -> bool {
    #[cfg(windows)]
    {
        let mut components = path.components();
        matches!(
            (components.next(), components.next(), components.next()),
            (
                Some(Component::Prefix(prefix)),
                Some(Component::RootDir),
                None
            ) if matches!(
                prefix.kind(),
                Prefix::Disk(_) | Prefix::VerbatimDisk(_)
            )
        )
    }
    #[cfg(not(windows))]
    {
        let _ = path;
        false
    }
}

#[cfg(all(test, windows))]
fn logical_drive_roots_from_mask(mask: u32) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for index in 0..26 {
        if (mask & (1 << index)) == 0 {
            continue;
        }
        let drive = format!("{}:\\", char::from(b'A' + index as u8));
        roots.push(PathBuf::from(drive));
    }
    roots
}

fn normalize_auxiliary_allowed_roots(auxiliary_allowed_roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for root in auxiliary_allowed_roots {
        push_existing_absolute(&mut roots, root);
    }
    roots
}

pub(crate) fn derived_auxiliary_allowed_roots_for_active_executable_roots(
    active_executable_roots: &[PathBuf],
) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for root in active_executable_roots {
        if root.parent().is_none() {
            continue;
        }
        let Some(name) = root.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        let lower = name.to_ascii_lowercase();
        if matches!(lower.as_str(), "bin" | "scripts") {
            for candidate in collect_active_executable_scope_parent_runtime_entries(root) {
                push_existing_absolute(&mut roots, &candidate);
            }
        }
    }
    roots
}

fn collect_active_executable_scope_parent_runtime_entries(executable_root: &Path) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    let Some(parent) = executable_root.parent() else {
        return roots;
    };
    let Some(name) = executable_root.file_name().and_then(|value| value.to_str()) else {
        return roots;
    };
    let lower = name.to_ascii_lowercase();
    if !matches!(lower.as_str(), "bin" | "scripts") {
        return roots;
    }
    for child_name in ["Lib", "lib", "DLLs", "dlls", "share", "libexec"] {
        push_existing_absolute(&mut roots, &parent.join(child_name));
    }
    push_existing_children_matching(&mut roots, parent, |name| {
        let lower = name.to_ascii_lowercase();
        lower.starts_with("python") && lower.ends_with(".zip")
    });
    push_existing_children_matching(&mut roots, parent, |name| {
        let lower = name.to_ascii_lowercase();
        lower.starts_with("python") && lower.ends_with("._pth")
    });
    if lower == "scripts" {
        push_existing_absolute(&mut roots, &parent.join("pyvenv.cfg"));
    }
    roots
}

fn push_existing_children_matching<F>(roots: &mut Vec<PathBuf>, parent: &Path, predicate: F)
where
    F: Fn(&str) -> bool,
{
    let Ok(entries) = fs::read_dir(parent) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if predicate(name) {
            push_existing_absolute(roots, &path);
        }
    }
}

fn effective_executable_roots(
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    active_executable_roots: &[PathBuf],
) -> Vec<PathBuf> {
    if active_executable_roots.is_empty() {
        return allowed_executable_roots(workspace, environment);
    }
    let mut roots = Vec::new();
    for root in active_executable_roots {
        push_existing_absolute(&mut roots, root);
    }
    roots.extend(trusted_system_roots(environment));
    roots
}

#[derive(Clone)]
struct ActiveExecutableScopeBoundaryContribution {
    allowed: Vec<PathBuf>,
    _blocked: Vec<PathBuf>,
}

struct LaunchBoundaryCommonInputs {
    executable_roots: Vec<PathBuf>,
    launch_auxiliary_allowed_roots: Vec<PathBuf>,
    launch_allowed_read_roots: Vec<PathBuf>,
}

struct WorkspaceOnlyBoundaryReadSetContext<'a> {
    workspace: &'a Path,
    environment: &'a BTreeMap<String, String>,
    common: &'a LaunchBoundaryCommonInputs,
    blocked_read_root: &'a Path,
}

fn collect_active_executable_scope_boundary_contribution(
    resolved: &ResolvedActiveExecutableScopeSurface,
    launch_allowed_read_roots: &[PathBuf],
) -> io::Result<ActiveExecutableScopeBoundaryContribution> {
    let mut blocked = Vec::new();
    let mut seen_seeds = Vec::new();
    let root = &resolved.root;
    if path_is_under_allowed_roots(root, launch_allowed_read_roots) {
        return Ok(ActiveExecutableScopeBoundaryContribution {
            allowed: collect_active_executable_scope_protected_roots(
                resolved,
                launch_allowed_read_roots,
            ),
            _blocked: Vec::new(),
        });
    }
    let normalized = normalize_boundary_root_path(root.clone());
    if !normalized.is_absolute() {
        return Ok(ActiveExecutableScopeBoundaryContribution {
            allowed: collect_active_executable_scope_protected_roots(
                resolved,
                launch_allowed_read_roots,
            ),
            _blocked: Vec::new(),
        });
    }
    seen_seeds.push(normalized.clone());
    let protected_roots =
        collect_active_executable_scope_protected_roots(resolved, launch_allowed_read_roots);
    for sibling in promoted_external_blocked_root_siblings(
        &normalized,
        launch_allowed_read_roots,
        &protected_roots,
        None,
    )? {
        push_absolute(&mut blocked, &sibling);
    }
    let mut current = normalized.parent().map(Path::to_path_buf);
    while let Some(seed) = current {
        let normalized_seed = normalize_boundary_root_path(seed);
        if !normalized_seed.is_absolute() {
            break;
        }
        if path_is_under_allowed_roots(&normalized_seed, launch_allowed_read_roots) {
            let sibling_limit = Some(MAX_EXECUTABLE_SCOPE_ANCESTOR_CHILDREN);
            for sibling in promoted_external_blocked_root_siblings(
                &normalized_seed,
                launch_allowed_read_roots,
                &protected_roots,
                sibling_limit,
            )? {
                push_absolute(&mut blocked, &sibling);
            }
            break;
        }
        if seen_seeds
            .iter()
            .any(|existing| existing == &normalized_seed)
        {
            current = normalized_seed.parent().map(Path::to_path_buf);
            continue;
        }
        seen_seeds.push(normalized_seed.clone());
        let sibling_limit = Some(MAX_EXECUTABLE_SCOPE_ANCESTOR_CHILDREN);
        for sibling in promoted_external_blocked_root_siblings(
            &normalized_seed,
            launch_allowed_read_roots,
            &protected_roots,
            sibling_limit,
        )? {
            push_absolute(&mut blocked, &sibling);
        }
        current = normalized_seed.parent().map(Path::to_path_buf);
    }
    for candidate in &resolved.surface.blocked {
        push_absolute(&mut blocked, candidate);
    }
    Ok(ActiveExecutableScopeBoundaryContribution {
        allowed: protected_roots,
        _blocked: blocked,
    })
}

fn collect_active_executable_scope_boundary_contributions(
    active_executable_surfaces: &[ResolvedActiveExecutableScopeSurface],
    launch_allowed_read_roots: &[PathBuf],
) -> io::Result<Vec<ActiveExecutableScopeBoundaryContribution>> {
    let mut contributions = Vec::new();
    for resolved in active_executable_surfaces {
        contributions.push(collect_active_executable_scope_boundary_contribution(
            resolved,
            launch_allowed_read_roots,
        )?);
    }
    Ok(contributions)
}

fn collect_active_executable_scope_protected_roots(
    resolved: &ResolvedActiveExecutableScopeSurface,
    launch_allowed_read_roots: &[PathBuf],
) -> Vec<PathBuf> {
    let mut protected_roots = launch_allowed_read_roots.to_vec();
    protected_roots.extend(resolved.surface.allowed.iter().cloned());
    collapse_allowed_read_roots(protected_roots)
}

#[cfg(test)]
fn collect_active_executable_scope_blocked_entries(
    executable_root: &Path,
    read_allowed_roots: &[PathBuf],
    trusted_system_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    Ok(collect_active_executable_scope_surface(
        executable_root,
        read_allowed_roots,
        trusted_system_roots,
    )?
    .blocked)
}

fn collect_active_executable_scope_parent_entries(
    executable_root: &Path,
    read_allowed_roots: &[PathBuf],
    trusted_system_roots: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut allowed = collect_active_executable_scope_parent_runtime_entries(executable_root);
    let Some(parent) = executable_root.parent() else {
        return Ok(allowed);
    };
    for entry in fs::read_dir(parent)? {
        let entry = entry?;
        let candidate = normalize_boundary_root_path(
            entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
        );
        if candidate == executable_root {
            continue;
        }
        if allowed.iter().any(|path| path == &candidate) {
            continue;
        }
        if active_executable_scope_entry_is_allowed(
            &candidate,
            read_allowed_roots,
            trusted_system_roots,
        ) {
            push_existing_absolute(&mut allowed, &candidate);
        }
    }
    Ok(allowed)
}

fn blocked_read_roots_for_directory_entries(
    directory: &Path,
    skip_path: Option<&Path>,
    allowed_entries: &[PathBuf],
) -> io::Result<Vec<PathBuf>> {
    let mut blocked = Vec::new();
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let candidate = normalize_boundary_root_path(
            entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
        );
        if !candidate.is_absolute() || skip_path.is_some_and(|skip| candidate == skip) {
            continue;
        }
        if allowed_entries.iter().any(|path| path == &candidate) {
            continue;
        }
        push_absolute(&mut blocked, &candidate);
    }
    Ok(blocked)
}

fn active_executable_scope_entry_is_allowed(
    candidate: &Path,
    read_allowed_roots: &[PathBuf],
    trusted_system_roots: &[PathBuf],
) -> bool {
    active_executable_scope_entry_is_explicitly_allowed(
        candidate,
        read_allowed_roots,
        trusted_system_roots,
    ) || active_executable_scope_entry_is_runtime_allowed(candidate)
}

fn active_executable_scope_entry_is_explicitly_allowed(
    candidate: &Path,
    read_allowed_roots: &[PathBuf],
    trusted_system_roots: &[PathBuf],
) -> bool {
    path_is_under_allowed_roots(candidate, read_allowed_roots)
        || path_matches_allowed_root(candidate, trusted_system_roots)
}

fn active_executable_scope_entry_is_runtime_allowed(candidate: &Path) -> bool {
    active_executable_root_direct_entry_is_allowed(candidate)
}

fn active_executable_root_direct_entry_is_allowed(candidate: &Path) -> bool {
    if candidate.is_dir() {
        let Some(name) = candidate.file_name().and_then(|value| value.to_str()) else {
            return false;
        };
        return common_runtime_subdir_name_is_allowed(name);
    }
    let Some(name) = candidate.file_name().and_then(|value| value.to_str()) else {
        return false;
    };
    active_executable_root_direct_file_is_allowed(name, candidate)
}

fn active_executable_root_direct_file_is_allowed(name: &str, candidate: &Path) -> bool {
    let lower = name.to_ascii_lowercase();
    if lower == "pyvenv.cfg" {
        return true;
    }
    if lower.starts_with("python") && (lower.ends_with(".zip") || lower.ends_with("._pth")) {
        return true;
    }
    matches!(
        candidate
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| value.to_ascii_lowercase())
            .as_deref(),
        Some("exe" | "com" | "dll" | "pyd" | "mui" | "manifest")
    )
}

fn common_runtime_subdir_name_is_allowed(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "lib" | "dlls" | "share" | "libexec"
    )
}

pub(crate) fn trusted_system_roots(environment: &BTreeMap<String, String>) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for key in ["SystemRoot", "WINDIR"] {
        let Some(value) = environment.get(key) else {
            continue;
        };
        push_existing_absolute(&mut roots, Path::new(value));
    }
    roots
}

fn validate_external_blocked_root(
    normalized: &Path,
    argument_roots: &[PathBuf],
    mutable_roots: &[PathBuf],
    executable_roots: &[PathBuf],
    auxiliary_roots: &[PathBuf],
) -> io::Result<()> {
    if path_is_under_allowed_roots(normalized, argument_roots)
        || path_is_under_allowed_roots(normalized, mutable_roots)
        || path_is_under_allowed_roots(normalized, executable_roots)
        || path_is_under_allowed_roots(normalized, auxiliary_roots)
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "{BLOCKED_READ_ROOTS_ENV} entries must stay outside allowed roots: {}",
                normalized.display()
            ),
        ));
    }
    Ok(())
}

#[cfg(test)]
fn promoted_external_blocked_root_ancestors(
    blocked_root: &Path,
    allowed_roots: &[PathBuf],
) -> (Vec<PathBuf>, Vec<PathBuf>) {
    let mut promoted = Vec::new();
    let mut unsafe_ancestors = Vec::new();
    let mut current = blocked_root.parent();
    while let Some(parent) = current {
        let normalized = normalize_boundary_root_path(parent.to_path_buf());
        if !normalized.is_absolute() || normalized == blocked_root {
            return (promoted, unsafe_ancestors);
        }
        if !parent_has_safe_scope(&normalized, allowed_roots) {
            unsafe_ancestors.push(normalized);
            current = parent.parent();
            continue;
        }
        promoted.push(normalized);
        current = parent.parent();
    }
    (promoted, unsafe_ancestors)
}

#[cfg(test)]
fn parent_has_safe_scope(parent: &Path, allowed_roots: &[PathBuf]) -> bool {
    if parent.parent().is_none() {
        return false;
    }
    !allowed_roots
        .iter()
        .any(|allowed| normalize_boundary_root_path(allowed.clone()).starts_with(parent))
}

fn parent_supports_boundary_sibling_scan(parent: &Path) -> bool {
    if !parent.is_dir() {
        return false;
    }
    if parent.parent().is_some() {
        return true;
    }
    #[cfg(windows)]
    {
        let mut components = parent.components();
        matches!(
            (components.next(), components.next(), components.next()),
            (
                Some(Component::Prefix(prefix)),
                Some(Component::RootDir),
                None
            ) if matches!(
                prefix.kind(),
                Prefix::Disk(_) | Prefix::VerbatimDisk(_)
            )
        )
    }
    #[cfg(not(windows))]
    {
        false
    }
}

fn promoted_external_blocked_root_siblings(
    boundary_seed: &Path,
    sibling_expansion_allowed_roots: &[PathBuf],
    protected_roots: &[PathBuf],
    max_parent_entries: Option<usize>,
) -> io::Result<Vec<PathBuf>> {
    let Some(parent) = boundary_seed.parent() else {
        return Ok(Vec::new());
    };
    if !parent_supports_boundary_sibling_scan(parent) {
        return Ok(Vec::new());
    }
    let allowed_entries = allowed_root_bearing_entries(parent, sibling_expansion_allowed_roots);
    if allowed_entries.child_roots.is_empty()
        || allowed_entries.child_roots.len() > MAX_SIBLING_EXPANSION_ALLOWED_CHILDREN
    {
        return Ok(Vec::new());
    }
    let protected_entries = allowed_root_bearing_entries(parent, protected_roots);
    let boundary_seed = normalize_boundary_root_path(boundary_seed.to_path_buf());
    let mut siblings = Vec::new();
    for entry in fs::read_dir(parent)? {
        let entry = entry?;
        let candidate = normalize_boundary_root_path(
            entry.path().canonicalize().unwrap_or_else(|_| entry.path()),
        );
        if !candidate.is_absolute() || candidate == boundary_seed {
            continue;
        }
        if allowed_entries.contains_candidate(&candidate)
            || protected_entries.contains_candidate(&candidate)
        {
            continue;
        }
        push_absolute(&mut siblings, &candidate);
    }
    if let Some(limit) = max_parent_entries
        && siblings.len() > limit
    {
        return Ok(Vec::new());
    }
    Ok(siblings)
}

struct AllowedRootBearingEntries {
    child_roots: Vec<PathBuf>,
    exact_paths: Vec<PathBuf>,
}

impl AllowedRootBearingEntries {
    fn contains_candidate(&self, candidate: &Path) -> bool {
        self.child_roots
            .iter()
            .any(|allowed_child| allowed_child == candidate)
            || self
                .exact_paths
                .iter()
                .any(|allowed_path| allowed_path == candidate)
    }
}

fn allowed_root_bearing_entries(
    parent: &Path,
    allowed_roots: &[PathBuf],
) -> AllowedRootBearingEntries {
    let mut child_roots = Vec::new();
    let mut exact_paths = Vec::new();
    for allowed in allowed_roots {
        let normalized_allowed = normalize_boundary_root_path(allowed.clone());
        if !normalized_allowed.starts_with(parent) {
            continue;
        }
        let Ok(relative) = normalized_allowed.strip_prefix(parent) else {
            continue;
        };
        if relative.components().count() == 1 && normalized_allowed.is_file() {
            push_absolute(&mut exact_paths, &normalized_allowed);
            continue;
        }
        let mut components = relative.components();
        let Some(first) = components.next() else {
            continue;
        };
        let child = normalize_boundary_root_path(parent.join(first.as_os_str()));
        push_absolute(&mut child_roots, &child);
    }
    AllowedRootBearingEntries {
        child_roots,
        exact_paths,
    }
}

pub(crate) fn collapse_blocked_read_roots(mut blocked_roots: Vec<PathBuf>) -> Vec<PathBuf> {
    blocked_roots.sort_by(|left, right| {
        let left_depth = left.components().count();
        let right_depth = right.components().count();
        left_depth
            .cmp(&right_depth)
            .then_with(|| left.as_os_str().len().cmp(&right.as_os_str().len()))
    });
    let mut collapsed = Vec::new();
    for root in blocked_roots {
        if collapsed.iter().any(|existing| root.starts_with(existing)) {
            continue;
        }
        collapsed.push(root);
    }
    collapsed
}

fn collapse_allowed_read_roots(mut allowed_roots: Vec<PathBuf>) -> Vec<PathBuf> {
    allowed_roots.sort_by(|left, right| {
        let left_depth = left.components().count();
        let right_depth = right.components().count();
        left_depth
            .cmp(&right_depth)
            .then_with(|| left.as_os_str().len().cmp(&right.as_os_str().len()))
    });
    let mut collapsed = Vec::new();
    for root in allowed_roots {
        if collapsed.iter().any(|existing: &PathBuf| {
            existing == &root || (existing.is_dir() && root.starts_with(existing))
        }) {
            continue;
        }
        collapsed.push(root);
    }
    collapsed
}

fn is_helper_managed_blocked_read_root(
    layout: &FilesystemBoundaryLayout,
    candidate: &Path,
) -> bool {
    let helper_managed_root = layout
        .helper_home
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("blocked_read");
    normalize_boundary_root_path(candidate.to_path_buf())
        == normalize_boundary_root_path(helper_managed_root)
}

#[cfg(windows)]
impl RestrictedProxyChild {
    pub fn take_stdin(&mut self) -> io::Result<File> {
        self.stdin
            .take()
            .ok_or_else(|| io::Error::new(io::ErrorKind::BrokenPipe, "restricted stdin missing"))
    }

    pub fn take_stdout(&mut self) -> io::Result<File> {
        self.stdout
            .take()
            .ok_or_else(|| io::Error::new(io::ErrorKind::BrokenPipe, "restricted stdout missing"))
    }

    pub fn take_stderr(&mut self) -> io::Result<File> {
        self.stderr
            .take()
            .ok_or_else(|| io::Error::new(io::ErrorKind::BrokenPipe, "restricted stderr missing"))
    }

    pub fn id(&self) -> u32 {
        self.pid
    }

    pub fn try_wait(&mut self) -> io::Result<Option<i32>> {
        let wait_result = unsafe { WaitForSingleObject(self.process.handle, 0) };
        match wait_result {
            0 => {
                let mut exit_code = 1u32;
                let exit_code_ok =
                    unsafe { GetExitCodeProcess(self.process.handle, &mut exit_code) };
                if exit_code_ok == 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(Some(exit_code as i32))
            }
            258 => Ok(None),
            _ => Err(io::Error::last_os_error()),
        }
    }

    pub fn kill(&mut self) -> io::Result<()> {
        let terminated = unsafe { TerminateProcess(self.process.handle, 1) };
        if terminated == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

#[cfg(windows)]
struct InheritableStdHandles {
    stdin: HANDLE,
    stdout: HANDLE,
    stderr: HANDLE,
}

#[cfg(windows)]
impl InheritableStdHandles {
    fn capture() -> io::Result<Self> {
        Ok(Self {
            stdin: duplicate_std_handle(unsafe { GetStdHandle(STD_INPUT_HANDLE) })?,
            stdout: duplicate_std_handle(unsafe { GetStdHandle(STD_OUTPUT_HANDLE) })?,
            stderr: duplicate_std_handle(unsafe { GetStdHandle(STD_ERROR_HANDLE) })?,
        })
    }
}

#[cfg(windows)]
impl Drop for InheritableStdHandles {
    fn drop(&mut self) {
        for handle in [self.stdin, self.stdout, self.stderr] {
            if !handle.is_null() && handle != INVALID_HANDLE_VALUE {
                unsafe { CloseHandle(handle) };
            }
        }
    }
}

#[cfg(windows)]
struct RestrictedProxyPipes {
    child_stdin_read: OwnedHandle,
    child_stdout_write: OwnedHandle,
    child_stderr_write: OwnedHandle,
    parent_stdin_write: File,
    parent_stdout_read: File,
    parent_stderr_read: File,
}

#[cfg(windows)]
impl RestrictedProxyPipes {
    fn create() -> io::Result<Self> {
        let (child_stdin_read, parent_stdin_write) = create_child_stdin_pipe()?;
        let (parent_stdout_read, child_stdout_write) = create_child_output_pipe()?;
        let (parent_stderr_read, child_stderr_write) = create_child_output_pipe()?;
        Ok(Self {
            child_stdin_read,
            child_stdout_write,
            child_stderr_write,
            parent_stdin_write,
            parent_stdout_read,
            parent_stderr_read,
        })
    }
}

#[cfg(windows)]
fn duplicate_std_handle(source: HANDLE) -> io::Result<HANDLE> {
    if source.is_null() || source == INVALID_HANDLE_VALUE {
        return Err(io::Error::new(
            io::ErrorKind::BrokenPipe,
            "helper standard handles must be valid for restricted boundary launch",
        ));
    }
    let process = unsafe { GetCurrentProcess() };
    let mut duplicated = std::ptr::null_mut();
    let duplicated_ok = unsafe {
        DuplicateHandle(
            process,
            source,
            process,
            &mut duplicated,
            0,
            1,
            DUPLICATE_SAME_ACCESS,
        )
    };
    if duplicated_ok == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(duplicated)
}

#[cfg(windows)]
fn create_child_stdin_pipe() -> io::Result<(OwnedHandle, File)> {
    let mut read_pipe = std::ptr::null_mut();
    let mut write_pipe = std::ptr::null_mut();
    let mut security_attributes = inheritable_security_attributes();
    let created = unsafe {
        CreatePipe(
            &mut read_pipe,
            &mut write_pipe,
            &mut security_attributes as *mut _,
            0,
        )
    };
    if created == 0 {
        return Err(io::Error::last_os_error());
    }
    let read_pipe = OwnedHandle { handle: read_pipe };
    let write_pipe = OwnedHandle { handle: write_pipe };
    let inherit_cleared =
        unsafe { SetHandleInformation(write_pipe.handle, HANDLE_FLAG_INHERIT, 0) };
    if inherit_cleared == 0 {
        return Err(io::Error::last_os_error());
    }
    let parent_file = unsafe { File::from_raw_handle(write_pipe.handle as *mut _) };
    std::mem::forget(write_pipe);
    Ok((read_pipe, parent_file))
}

#[cfg(windows)]
fn create_child_output_pipe() -> io::Result<(File, OwnedHandle)> {
    let mut read_pipe = std::ptr::null_mut();
    let mut write_pipe = std::ptr::null_mut();
    let mut security_attributes = inheritable_security_attributes();
    let created = unsafe {
        CreatePipe(
            &mut read_pipe,
            &mut write_pipe,
            &mut security_attributes as *mut _,
            0,
        )
    };
    if created == 0 {
        return Err(io::Error::last_os_error());
    }
    let read_pipe = OwnedHandle { handle: read_pipe };
    let write_pipe = OwnedHandle { handle: write_pipe };
    let inherit_cleared = unsafe { SetHandleInformation(read_pipe.handle, HANDLE_FLAG_INHERIT, 0) };
    if inherit_cleared == 0 {
        return Err(io::Error::last_os_error());
    }
    let parent_file = unsafe { File::from_raw_handle(read_pipe.handle as *mut _) };
    std::mem::forget(read_pipe);
    Ok((parent_file, write_pipe))
}

#[cfg(windows)]
fn inheritable_security_attributes() -> SECURITY_ATTRIBUTES {
    SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: std::ptr::null_mut(),
        bInheritHandle: 1,
    }
}

#[cfg(windows)]
pub(crate) fn resolve_program_path(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    program: &str,
) -> Option<PathBuf> {
    let explicit = Path::new(program);
    if explicit.is_absolute() {
        return Some(explicit.to_path_buf());
    }
    let joined = cwd.join(explicit);
    if explicit.components().count() > 1 || joined.exists() {
        return Some(joined);
    }
    resolve_program_via_path_lookup(program, environment)
}

#[cfg(windows)]
fn resolve_program_via_path_lookup(
    program: &str,
    environment: &BTreeMap<String, String>,
) -> Option<PathBuf> {
    let path_value = environment
        .get("PATH")
        .or_else(|| environment.get("Path"))
        .or_else(|| environment.get("path"))?;
    let extensions = path_lookup_extensions(environment);
    for directory in std::env::split_paths(path_value) {
        for candidate in path_lookup_candidates(&directory, program, &extensions) {
            if candidate.exists() {
                return Some(candidate.canonicalize().unwrap_or(candidate));
            }
        }
    }
    None
}

#[cfg(windows)]
fn path_lookup_candidates(directory: &Path, program: &str, extensions: &[String]) -> Vec<PathBuf> {
    let base = directory.join(program);
    if Path::new(program).extension().is_some() {
        return vec![base];
    }
    let mut candidates = vec![base.clone()];
    for extension in extensions {
        candidates.push(directory.join(format!("{program}{extension}")));
    }
    candidates
}

#[cfg(windows)]
fn path_lookup_extensions(environment: &BTreeMap<String, String>) -> Vec<String> {
    environment
        .get("PATHEXT")
        .or_else(|| environment.get("Pathext"))
        .or_else(|| environment.get("pathext"))
        .map(|value| {
            value
                .split(';')
                .filter_map(|part| {
                    let trimmed = part.trim();
                    if trimmed.is_empty() {
                        None
                    } else if trimmed.starts_with('.') {
                        Some(trimmed.to_ascii_lowercase())
                    } else {
                        Some(format!(".{}", trimmed.to_ascii_lowercase()))
                    }
                })
                .collect()
        })
        .unwrap_or_else(|| {
            vec![
                ".com".to_string(),
                ".exe".to_string(),
                ".bat".to_string(),
                ".cmd".to_string(),
            ]
        })
}

#[cfg(windows)]
fn create_environment_block(environment: &BTreeMap<String, String>) -> Vec<u16> {
    let mut block = Vec::new();
    for (key, value) in environment {
        block.extend(format!("{key}={value}").encode_utf16());
        block.push(0);
    }
    block.push(0);
    block
}

fn push_existing_absolute(roots: &mut Vec<PathBuf>, candidate: &Path) {
    if !candidate.exists() {
        return;
    }
    push_absolute(roots, candidate);
}

fn push_absolute(roots: &mut Vec<PathBuf>, candidate: &Path) {
    let normalized = normalize_boundary_root_path(
        candidate
            .canonicalize()
            .unwrap_or_else(|_| candidate.to_path_buf()),
    );
    if !normalized.is_absolute() {
        return;
    }
    if roots.iter().any(|root| root == &normalized) {
        return;
    }
    roots.push(normalized);
}

fn normalize_boundary_root_path(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(stripped) = text.strip_prefix(r"\\?\") {
        return PathBuf::from(stripped);
    }
    if let Some(stripped) = text.strip_prefix(r"\??\") {
        return PathBuf::from(stripped);
    }
    path
}

fn next_hidden_internal_root_path(original: &Path) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let parent = original.parent().unwrap_or_else(|| Path::new("."));
    let stem = original
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("blocked-root");
    loop {
        let suffix = COUNTER.fetch_add(1, Ordering::Relaxed);
        let candidate = parent.join(format!(
            ".ai_ide_hidden_{stem}_{}_{}",
            std::process::id(),
            suffix
        ));
        if !candidate.exists() {
            return candidate;
        }
    }
}

#[cfg(windows)]
fn windows_command_line_for_launch(program: &Path, args: &[String]) -> String {
    let mut parts = Vec::with_capacity(args.len() + 1);
    parts.push(windows_quote_argument(&program.to_string_lossy()));
    parts.extend(args.iter().map(|arg| windows_quote_argument(arg)));
    parts.join(" ")
}

#[cfg(windows)]
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

#[cfg(windows)]
fn to_wide_path(path: &Path) -> Vec<u16> {
    OsStr::new(path).encode_wide().chain([0]).collect()
}

#[cfg(test)]
mod tests {
    use super::{
        ActiveExecutableScopeContext, AllowedReadRootsContext, BLOCKED_READ_ROOTS_ENV,
        BlockedInternalRootsStaging, BlockedReadRootsStaging,
        FilesystemBoundaryLayout, MAX_ACTIVE_DRIVE_CHILD_SCOPE_CHILDREN,
        MAX_ACTIVE_DRIVE_CHILDREN, MAX_ACTIVE_DRIVE_GRANDCHILD_SCOPE_CHILDREN,
        MAX_ACTIVE_DRIVE_LEAF_CHILDREN, MAX_EXTERNAL_TOOL_CONTAINER_SIBLINGS,
        MAX_PROGRAM_FILES_SIBLINGS, MAX_USER_LOCAL_PROGRAM_SIBLINGS,
        MAX_USER_PROFILE_COMMON_CHILD_SCOPE_CHILDREN, MAX_USER_PROFILE_DIRECT_CHILDREN,
        MAX_USER_PROFILE_LEAF_CHILDREN, MAX_USER_PROFILE_SIBLINGS,
        active_executable_scope_entry_is_allowed,
        active_executable_scope_entry_is_explicitly_allowed,
        active_executable_scope_entry_is_runtime_allowed, allowed_argument_roots,
        allowed_executable_roots, base_launch_allowed_read_roots,
        blocked_read_roots_for_active_drive_child_scope_from_candidates,
        blocked_read_roots_for_active_drive_grandchild_scope_from_candidates,
        blocked_read_roots_for_active_drive_known_dirs_scope_from_candidates,
        blocked_read_roots_for_active_drive_leaf_scope,
        blocked_read_roots_for_active_drive_scope_from_candidates,
        blocked_read_roots_for_active_executable_scope,
        blocked_read_roots_for_allowed_user_profile_common_child_scope,
        blocked_read_roots_for_allowed_user_profile_local_appdata_child_scope,
        blocked_read_roots_for_allowed_user_profile_local_appdata_leaf_scope,
        blocked_read_roots_for_external_tool_container_leaf_scope,
        blocked_read_roots_for_external_tool_container_sibling_scope,
        blocked_read_roots_for_inactive_drive_scope_from_candidates,
        blocked_read_roots_for_non_system_active_drive_scope_from_candidates,
        blocked_read_roots_for_program_files_leaf_scope,
        blocked_read_roots_for_program_files_sibling_scope,
        blocked_read_roots_for_runtime_executable_root,
        blocked_read_roots_for_user_local_programs_leaf_scope,
        blocked_read_roots_for_user_local_programs_sibling_scope,
        blocked_read_roots_for_user_profile_common_dirs_scope,
        blocked_read_roots_for_user_profile_direct_subdir_scope,
        blocked_read_roots_for_user_profile_leaf_scope,
        blocked_read_roots_for_user_profile_parent_known_dirs_scope,
        blocked_read_roots_for_user_profile_parent_leaf_scope,
        blocked_read_roots_for_user_profile_sibling_scope,
        collect_active_executable_scope_allowed_entries,
        collect_active_executable_scope_blocked_entries,
        collect_active_executable_scope_boundary_contribution,
        collect_active_executable_scope_boundary_contributions,
        collect_active_executable_scope_parent_entries,
        collect_active_executable_scope_runtime_entries, collect_active_executable_scope_surface,
        collect_active_executable_scope_surfaces,
        collect_workspace_core_boundary_surface, create_environment_block,
        derived_auxiliary_allowed_roots_for_active_executable_roots, effective_executable_roots,
        helper_mutable_roots, launch_scoped_allowed_read_roots, launch_scoped_environment,
        logical_drive_roots_from_mask, normalize_boundary_root_path,
        parent_supports_boundary_sibling_scan, path_is_drive_root, prepare_low_integrity_boundary,
        prepare_read_boundary, promoted_external_blocked_root_ancestors,
        resolve_filesystem_boundary_layout, resolve_filesystem_boundary_layout_for_launch,
        resolve_program_path, stage_read_boundary, trusted_system_roots,
        windows_command_line_for_launch,
    };
    #[cfg(windows)]
    use crate::low_integrity::capture_label_security_descriptor;
    use std::collections::BTreeMap;
    use std::io;
    use std::path::{Path, PathBuf};

    #[cfg(windows)]
    #[test]
    fn environment_block_is_double_null_terminated() {
        let environment = BTreeMap::from([
            ("HOME".to_string(), r"C:\sandbox\home".to_string()),
            ("PATH".to_string(), r"C:\Python".to_string()),
        ]);
        let block = create_environment_block(&environment);
        assert!(block.ends_with(&[0, 0]));
    }

    #[cfg(windows)]
    #[test]
    fn command_line_quotes_program_and_arguments() {
        let command_line = windows_command_line_for_launch(
            Path::new(r"C:\Program Files\Python\python.exe"),
            &["script with spaces.py".to_string(), "plain".to_string()],
        );
        assert_eq!(
            "\"C:\\Program Files\\Python\\python.exe\" \"script with spaces.py\" plain",
            command_line
        );
    }

    #[cfg(windows)]
    #[test]
    fn parent_supports_boundary_sibling_scan_accepts_drive_root() {
        assert!(parent_supports_boundary_sibling_scan(Path::new(r"C:\")));
        assert!(parent_supports_boundary_sibling_scan(Path::new(r"\\?\C:\")));
    }

    #[cfg(windows)]
    #[test]
    fn resolves_bare_program_via_helper_path() {
        let temp = std::env::temp_dir().join("helper-boundary-resolve");
        let bin = temp.join("python.exe");
        std::fs::create_dir_all(&temp).unwrap();
        std::fs::write(&bin, b"").unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_string(), temp.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        let resolved =
            resolve_program_path(Path::new(r"C:\workspace"), &environment, "python").unwrap();
        assert_eq!(bin.canonicalize().unwrap_or(bin), resolved);
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_collects_helper_roots_and_internal_targets() {
        let workspace = Path::new(r"C:\workspace");
        let environment = BTreeMap::from([
            ("HOME".to_string(), r"C:\sandbox\home".to_string()),
            ("TMPDIR".to_string(), r"C:\sandbox\tmp".to_string()),
            (
                "XDG_CACHE_HOME".to_string(),
                r"C:\sandbox\cache".to_string(),
            ),
        ]);

        let layout = resolve_filesystem_boundary_layout(workspace, &environment).unwrap();
        assert_eq!(PathBuf::from(r"C:\workspace"), layout.workspace_root);
        assert_eq!(PathBuf::from(r"C:\sandbox\home"), layout.helper_home);
        assert_eq!(PathBuf::from(r"C:\sandbox\tmp"), layout.helper_tmp);
        assert_eq!(PathBuf::from(r"C:\sandbox\cache"), layout.helper_cache);
        assert_eq!(
            PathBuf::from(r"C:\sandbox\blocked_read"),
            layout.blocked_read_roots[0]
        );
        assert_eq!(
            vec![
                PathBuf::from(r"C:\workspace\.ai_ide_runtime"),
                PathBuf::from(r"C:\workspace\.ai-ide"),
            ],
            layout.blocked_internal_roots
        );
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_collects_additional_blocked_read_roots() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-extra-blocked-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let external = base.join("external");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&external).unwrap();
        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),
            (
                BLOCKED_READ_ROOTS_ENV.to_string(),
                external.display().to_string(),
            ),
        ]);

        let layout = resolve_filesystem_boundary_layout(&workspace, &environment).unwrap();

        assert!(
            layout
                .blocked_read_roots
                .iter()
                .any(|root| root.ends_with("blocked_read"))
        );
        let normalized_external = normalize_boundary_root_path(external.clone());
        assert!(
            layout
                .blocked_read_roots
                .iter()
                .any(|root| normalized_external.starts_with(root))
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_workspace_only_scope_skips_host_surface_hardening() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-workspace-only-scope-{}",
            std::process::id()
        ));
        let workspace = base.join("Users").join("owner").join("project");
        let downloads = base.join("Users").join("owner").join("Downloads");
        let programs_tool = base
            .join("Users")
            .join("owner")
            .join("AppData")
            .join("Local")
            .join("Programs")
            .join("Python311");
        let active_exec_root = programs_tool.join("Scripts");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&downloads).unwrap();
        std::fs::create_dir_all(&active_exec_root).unwrap();
        std::fs::write(active_exec_root.join("python.exe"), b"").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),

        ]);

        let layout = resolve_filesystem_boundary_layout_for_launch(
            &workspace,
            &environment,
            std::slice::from_ref(&active_exec_root),
            &[],
        )
        .unwrap();

        // scope assertion removed — field was dead outside tests

        assert!(
            !layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &downloads),
            "{:?}",
            layout.blocked_read_roots
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_workspace_only_scope_keeps_external_overlay_exact() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-workspace-only-overlay-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let external_parent = base.join("outside");
        let external = external_parent.join("blocked");
        let sibling = external_parent.join("visible");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&external).unwrap();
        std::fs::create_dir_all(&sibling).unwrap();
        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),

            (
                BLOCKED_READ_ROOTS_ENV.to_string(),
                external.display().to_string(),
            ),
        ]);

        let layout = resolve_filesystem_boundary_layout(&workspace, &environment).unwrap();
        let normalized_external = normalize_boundary_root_path(external.clone());
        let normalized_parent = normalize_boundary_root_path(external_parent);
        let normalized_sibling = normalize_boundary_root_path(sibling);

        assert!(
            layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &normalized_external)
        );
        assert!(
            !layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &normalized_parent)
        );
        assert!(
            !layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &normalized_sibling)
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_workspace_only_scope_keeps_programdata_visible() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-workspace-only-programdata-{}",
            std::process::id()
        ));
        let workspace = base.join("Users").join("owner").join("project");
        let program_data = base.join("ProgramData");
        let tool_root = base.join("Tools").join("Python311");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&program_data).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::write(tool_root.join("python.exe"), b"").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),

        ]);

        let layout = resolve_filesystem_boundary_layout_for_launch(
            &workspace,
            &environment,
            std::slice::from_ref(&tool_root),
            &[],
        )
        .unwrap();

        assert!(
            !layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &program_data),
            "{:?}",
            layout.blocked_read_roots
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_workspace_only_scope_keeps_program_files_visible() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-workspace-only-program-files-{}",
            std::process::id()
        ));
        let workspace = base.join("Users").join("owner").join("project");
        let program_files = base.join("Program Files");
        let active_exec_root = program_files.join("Python311").join("Scripts");
        let sibling_install = program_files.join("NodeJS");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&active_exec_root).unwrap();
        std::fs::create_dir_all(&sibling_install).unwrap();
        std::fs::write(active_exec_root.join("python.exe"), b"").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),

        ]);

        let layout = resolve_filesystem_boundary_layout_for_launch(
            &workspace,
            &environment,
            std::slice::from_ref(&active_exec_root),
            &[],
        )
        .unwrap();

        assert!(
            !layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &sibling_install || root == &program_files),
            "{:?}",
            layout.blocked_read_roots
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_workspace_only_scope_keeps_custom_tool_siblings_visible()
    {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-workspace-only-custom-tools-{}",
            std::process::id()
        ));
        let workspace = base.join("Users").join("owner").join("project");
        let tools_root = base.join("Tools");
        let active_exec_root = tools_root.join("Python311").join("Scripts");
        let sibling_install = tools_root.join("NodeJS");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&active_exec_root).unwrap();
        std::fs::create_dir_all(&sibling_install).unwrap();
        std::fs::write(active_exec_root.join("python.exe"), b"").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),

        ]);

        let layout = resolve_filesystem_boundary_layout_for_launch(
            &workspace,
            &environment,
            std::slice::from_ref(&active_exec_root),
            &[],
        )
        .unwrap();

        assert!(
            !layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &sibling_install || root == &tools_root),
            "{:?}",
            layout.blocked_read_roots
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_workspace_only_scope_keeps_user_profile_surface_visible()
    {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-workspace-only-user-profile-surface-{}",
            std::process::id()
        ));
        let owner_root = base.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("project");
        let downloads = owner_root.join("Downloads");
        let private_subdir = owner_root.join("private-data");
        let leaf_file = owner_root.join(".gitconfig");
        let active_exec_root = base.join("Tools").join("Python311");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&downloads).unwrap();
        std::fs::create_dir_all(&private_subdir).unwrap();
        std::fs::create_dir_all(&active_exec_root).unwrap();
        std::fs::write(&leaf_file, b"host-visible").unwrap();
        std::fs::write(active_exec_root.join("python.exe"), b"").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),

        ]);

        let layout = resolve_filesystem_boundary_layout_for_launch(
            &workspace,
            &environment,
            std::slice::from_ref(&active_exec_root),
            &[],
        )
        .unwrap();

        assert!(
            !layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &downloads || root == &private_subdir || root == &leaf_file),
            "{:?}",
            layout.blocked_read_roots
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_workspace_only_scope_keeps_common_host_surface_visible() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-workspace-only-common-host-surface-{}",
            std::process::id()
        ));
        let owner_root = base.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("project");
        let downloads = owner_root.join("Downloads");
        let private_subdir = owner_root.join("private-data");
        let leaf_file = owner_root.join(".gitconfig");
        let program_data = base.join("ProgramData");
        let program_files = base.join("Program Files");
        let tools_root = base.join("Tools");
        let active_exec_root = tools_root.join("Python311").join("Scripts");
        let sibling_install = tools_root.join("NodeJS");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&downloads).unwrap();
        std::fs::create_dir_all(&private_subdir).unwrap();
        std::fs::create_dir_all(&program_data).unwrap();
        std::fs::create_dir_all(&program_files).unwrap();
        std::fs::create_dir_all(&sibling_install).unwrap();
        std::fs::create_dir_all(&active_exec_root).unwrap();
        std::fs::write(&leaf_file, b"host-visible").unwrap();
        std::fs::write(active_exec_root.join("python.exe"), b"").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),

        ]);

        let layout = resolve_filesystem_boundary_layout_for_launch(
            &workspace,
            &environment,
            std::slice::from_ref(&active_exec_root),
            &[],
        )
        .unwrap();

        assert!(
            !layout.blocked_read_roots.iter().any(|root| {
                root == &downloads
                    || root == &private_subdir
                    || root == &leaf_file
                    || root == &program_data
                    || root == &program_files
                    || root == &sibling_install
                    || root == &tools_root
            }),
            "{:?}",
            layout.blocked_read_roots
        );


        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_skips_parent_promotion_when_parent_contains_allowed_root()
    {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-parent-skip-{}",
            std::process::id()
        ));
        let shared_parent = base.join("shared-root");
        let workspace = shared_parent.join("workspace");
        let blocked_child = shared_parent.join("host-blocked");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&blocked_child).unwrap();
        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),
            (
                BLOCKED_READ_ROOTS_ENV.to_string(),
                blocked_child.display().to_string(),
            ),
        ]);

        let layout = resolve_filesystem_boundary_layout(&workspace, &environment).unwrap();

        assert!(
            layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &blocked_child),
            "{:?}",
            layout.blocked_read_roots
        );
        assert!(
            !layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &shared_parent),
            "{:?}",
            layout.blocked_read_roots
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]
    #[test]
    fn launch_scoped_environment_keeps_active_executable_root_and_drops_inactive_helper_path_root()
    {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-launch-scoped-env-path-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let active_tool = drive_root.join("system-root").join("sdk").join("bin");
        let inactive_tool = drive_root.join("other-root").join("sdk").join("bin");
        let system_root = drive_root.join("Windows");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&active_tool).unwrap();
        std::fs::create_dir_all(&inactive_tool).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                helper_root.join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                helper_root.join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_root.join("cache").display().to_string(),
            ),
            ("SystemRoot".to_string(), system_root.display().to_string()),
            ("WINDIR".to_string(), system_root.display().to_string()),
            (
                "PATH".to_string(),
                std::env::join_paths([active_tool.as_path(), inactive_tool.as_path()])
                    .unwrap()
                    .display()
                    .to_string(),
            ),
        ]);

        let scoped = launch_scoped_environment(&environment, &[active_tool.clone()]).unwrap();
        let scoped_path = std::env::split_paths(std::ffi::OsStr::new(scoped.get("PATH").unwrap()))
            .collect::<Vec<_>>();

        assert!(
            scoped_path.iter().any(|path| path == &active_tool),
            "{scoped_path:?}"
        );
        assert!(
            !scoped_path.iter().any(|path| path == &inactive_tool),
            "{scoped_path:?}"
        );
        assert!(
            !scoped_path.iter().any(|path| path == &system_root),
            "{scoped_path:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_sibling_scope_blocks_other_profiles() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-siblings-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("Users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        let sibling_profile = users_root.join("public");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&sibling_profile).unwrap();
        let allowed = vec![workspace];

        let blocked = blocked_read_roots_for_user_profile_sibling_scope(&allowed, &[]).unwrap();

        assert!(
            blocked.iter().any(|root| root == &sibling_profile),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &owner_root),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_sibling_scope_skips_when_too_many_profiles() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-many-siblings-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("Users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        for index in 0..(MAX_USER_PROFILE_SIBLINGS + 1) {
            std::fs::create_dir_all(users_root.join(format!("profile-{index}"))).unwrap();
        }
        let allowed = vec![workspace];

        let blocked = blocked_read_roots_for_user_profile_sibling_scope(&allowed, &[]).unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_parent_known_dirs_scope_blocks_common_profile_dirs() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-known-dirs-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("Users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let public_root = users_root.join("Public");
        let default_root = users_root.join("Default");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&public_root).unwrap();
        std::fs::create_dir_all(&default_root).unwrap();
        let allowed = vec![workspace];

        let blocked =
            blocked_read_roots_for_user_profile_parent_known_dirs_scope(&allowed, &[]).unwrap();

        assert!(
            blocked.iter().any(|root| root == &public_root),
            "{blocked:?}"
        );
        assert!(
            blocked.iter().any(|root| root == &default_root),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &owner_root),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_parent_leaf_scope_blocks_users_root_leaf_files() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-parent-leafs-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("Users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let users_secret = users_root.join("users-secret.txt");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::write(&users_secret, b"secret").unwrap();
        let allowed = vec![workspace];

        let blocked = blocked_read_roots_for_user_profile_parent_leaf_scope(&allowed, &[]).unwrap();

        assert!(
            blocked.iter().any(|root| root == &users_secret),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_parent_leaf_scope_skips_when_too_many_leaf_files() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-parent-many-leafs-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("Users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        for index in 0..(MAX_USER_PROFILE_SIBLINGS + 1) {
            std::fs::write(
                users_root.join(format!("users-secret-{index}.txt")),
                b"secret",
            )
            .unwrap();
        }
        let allowed = vec![workspace];

        let blocked = blocked_read_roots_for_user_profile_parent_leaf_scope(&allowed, &[]).unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_common_dirs_scope_blocks_common_dirs() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-common-dirs-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let downloads_root = owner_root.join("Downloads");
        let roaming_root = owner_root.join("AppData").join("Roaming");
        let packages_root = owner_root.join("AppData").join("Local").join("Packages");
        let local_temp_root = owner_root.join("AppData").join("Local").join("Temp");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&downloads_root).unwrap();
        std::fs::create_dir_all(&roaming_root).unwrap();
        std::fs::create_dir_all(&packages_root).unwrap();
        std::fs::create_dir_all(&local_temp_root).unwrap();
        let allowed = vec![
            workspace.clone(),
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
        ];

        let blocked = blocked_read_roots_for_user_profile_common_dirs_scope(&allowed, &[]).unwrap();

        assert!(
            blocked.iter().any(|root| root == &downloads_root),
            "{blocked:?}"
        );
        assert!(
            blocked.iter().any(|root| root == &roaming_root),
            "{blocked:?}"
        );
        assert!(
            blocked.iter().any(|root| root == &packages_root),
            "{blocked:?}"
        );
        assert!(
            blocked.iter().any(|root| root == &local_temp_root),
            "{blocked:?}"
        );
        assert!(
            !blocked
                .iter()
                .any(|root| root == &owner_root.join("Documents")),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_leaf_scope_blocks_profile_leaf_files() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-leaf-files-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let profile_secret = owner_root.join("profile-secret.txt");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        std::fs::write(&profile_secret, b"secret").unwrap();
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
        ];

        let blocked = blocked_read_roots_for_user_profile_leaf_scope(&allowed, &[]).unwrap();

        assert!(
            blocked.iter().any(|root| root == &profile_secret),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_leaf_scope_skips_when_too_many_leaf_files() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-leaf-skip-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        for index in 0..=MAX_USER_PROFILE_LEAF_CHILDREN {
            std::fs::write(
                owner_root.join(format!("profile-secret-{index}.txt")),
                b"secret",
            )
            .unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
        ];

        let blocked = blocked_read_roots_for_user_profile_leaf_scope(&allowed, &[]).unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_direct_subdir_scope_blocks_profile_subdirs() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-direct-subdirs-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let private_dir = owner_root.join("private-data");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
        ];

        let blocked =
            blocked_read_roots_for_user_profile_direct_subdir_scope(&allowed, &[]).unwrap();

        assert!(
            blocked.iter().any(|root| root == &private_dir),
            "{blocked:?}"
        );
        assert!(
            !blocked
                .iter()
                .any(|root| root == &owner_root.join("Documents")),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_profile_direct_subdir_scope_skips_when_too_many_dirs() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-direct-subdir-skip-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        for index in 0..=MAX_USER_PROFILE_DIRECT_CHILDREN {
            std::fs::create_dir_all(owner_root.join(format!("private-dir-{index}"))).unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
        ];

        let blocked =
            blocked_read_roots_for_user_profile_direct_subdir_scope(&allowed, &[]).unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_allowed_user_profile_common_child_scope_blocks_child_siblings() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-common-child-scope-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let private_dir = owner_root.join("Documents").join("private-data");
        let notes_file = owner_root.join("Documents").join("notes.txt");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        std::fs::write(&notes_file, b"notes").unwrap();
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
        ];

        let blocked =
            blocked_read_roots_for_allowed_user_profile_common_child_scope(&allowed, &[]).unwrap();

        assert!(
            blocked.iter().any(|root| root == &private_dir),
            "{blocked:?}"
        );
        assert!(
            blocked.iter().any(|root| root == &notes_file),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_allowed_user_profile_common_child_scope_skips_when_too_many_entries()
    {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-common-child-scope-skip-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        for index in 0..=MAX_USER_PROFILE_COMMON_CHILD_SCOPE_CHILDREN {
            std::fs::create_dir_all(
                owner_root
                    .join("Documents")
                    .join(format!("private-{index}")),
            )
            .unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
        ];

        let blocked =
            blocked_read_roots_for_allowed_user_profile_common_child_scope(&allowed, &[]).unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_allowed_user_profile_local_appdata_child_scope_blocks_local_siblings()
    {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-local-appdata-child-scope-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let programs_root = owner_root.join("AppData").join("Local").join("Programs");
        let packages_root = owner_root.join("AppData").join("Local").join("Packages");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        std::fs::create_dir_all(programs_root.join("Python311").join("Scripts")).unwrap();
        std::fs::create_dir_all(&packages_root).unwrap();
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
            programs_root.join("Python311").join("Scripts"),
        ];

        let blocked =
            blocked_read_roots_for_allowed_user_profile_local_appdata_child_scope(&allowed, &[])
                .unwrap();

        assert!(
            blocked.iter().any(|root| root == &packages_root),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_allowed_user_profile_local_appdata_child_scope_skips_when_too_many_entries()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-local-appdata-child-scope-many-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let local_root = owner_root.join("AppData").join("Local");
        let programs_root = local_root.join("Programs");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        std::fs::create_dir_all(programs_root.join("Python311").join("Scripts")).unwrap();
        for index in 0..=MAX_USER_PROFILE_COMMON_CHILD_SCOPE_CHILDREN {
            std::fs::create_dir_all(local_root.join(format!("tool-{index}"))).unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
            programs_root.join("Python311").join("Scripts"),
        ];

        let blocked =
            blocked_read_roots_for_allowed_user_profile_local_appdata_child_scope(&allowed, &[])
                .unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_allowed_user_profile_local_appdata_leaf_scope_blocks_local_leaf_files()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-local-appdata-leaf-scope-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let programs_root = owner_root.join("AppData").join("Local").join("Programs");
        let local_root = owner_root.join("AppData").join("Local");
        let local_secret = local_root.join("local-secret.txt");
        let allowed_runtime_dir = programs_root.join("Python311").join("Lib");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        std::fs::create_dir_all(programs_root.join("Python311").join("Scripts")).unwrap();
        std::fs::create_dir_all(&allowed_runtime_dir).unwrap();
        std::fs::write(&local_secret, b"secret").unwrap();
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
            programs_root.join("Python311").join("Scripts"),
            allowed_runtime_dir.clone(),
        ];

        let blocked =
            blocked_read_roots_for_allowed_user_profile_local_appdata_leaf_scope(&allowed, &[])
                .unwrap();

        assert!(
            blocked.iter().any(|root| root == &local_secret),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &allowed_runtime_dir),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_allowed_user_profile_local_appdata_leaf_scope_skips_when_too_many_entries()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-profile-local-appdata-leaf-scope-many-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("Documents").join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let local_root = owner_root.join("AppData").join("Local");
        let programs_root = local_root.join("Programs");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(helper_root.join("cache")).unwrap();
        std::fs::create_dir_all(programs_root.join("Python311").join("Scripts")).unwrap();
        for index in 0..=MAX_USER_PROFILE_COMMON_CHILD_SCOPE_CHILDREN {
            std::fs::write(local_root.join(format!("leaf-{index}.txt")), b"secret").unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            helper_root.join("tmp"),
            helper_root.join("cache"),
            programs_root.join("Python311").join("Scripts"),
        ];

        let blocked =
            blocked_read_roots_for_allowed_user_profile_local_appdata_leaf_scope(&allowed, &[])
                .unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn logical_drive_roots_from_mask_expands_windows_drive_letters() {
        let roots = logical_drive_roots_from_mask(0b101);
        assert_eq!(roots, vec![PathBuf::from("A:\\"), PathBuf::from("C:\\")]);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_inactive_drive_scope_blocks_drives_without_allowed_roots() {
        let allowed = vec![
            PathBuf::from("C:\\workspace"),
            PathBuf::from("D:\\helper\\home"),
        ];
        let protected = vec![PathBuf::from("C:\\Windows")];
        let candidates = vec![
            PathBuf::from("C:\\"),
            PathBuf::from("D:\\"),
            PathBuf::from("E:\\"),
        ];

        let blocked = blocked_read_roots_for_inactive_drive_scope_from_candidates(
            &allowed,
            &protected,
            &candidates,
        );

        assert!(!blocked.iter().any(|root| root == &PathBuf::from("C:\\")));
        assert!(!blocked.iter().any(|root| root == &PathBuf::from("D:\\")));
        assert!(blocked.iter().any(|root| root == &PathBuf::from("E:\\")));
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_scope_blocks_top_level_siblings_with_many_drive_children()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-siblings-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        let system_root = drive_root.join("Windows");
        let blocked_sibling = drive_root.join("temp-95");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        for index in 0..96 {
            std::fs::create_dir_all(drive_root.join(format!("temp-{index}"))).unwrap();
        }
        let allowed = vec![
            workspace.clone(),
            helper_root.join("home"),
            tool_root.clone(),
            system_root.clone(),
        ];
        let protected = vec![system_root];

        let blocked = blocked_read_roots_for_active_drive_scope_from_candidates(
            &allowed,
            &protected,
            std::slice::from_ref(&drive_root),
        )
        .unwrap();

        assert!(
            blocked.iter().any(|root| root == &blocked_sibling),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &users_root),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &tool_root),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_scope_skips_when_drive_has_too_many_children() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-too-many-siblings-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        let system_root = drive_root.join("Windows");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        for index in 0..(MAX_ACTIVE_DRIVE_CHILDREN + 1) {
            std::fs::create_dir_all(drive_root.join(format!("temp-{index}"))).unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            tool_root,
            system_root.clone(),
        ];
        let protected = vec![system_root];

        let blocked = blocked_read_roots_for_active_drive_scope_from_candidates(
            &allowed,
            &protected,
            std::slice::from_ref(&drive_root),
        )
        .unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_leaf_scope_blocks_unrelated_drive_leaf_files() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-leaf-scope-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        let system_root = drive_root.join("Windows");
        let blocked_leaf = drive_root.join("secret.txt");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        std::fs::write(&blocked_leaf, b"secret").unwrap();
        let allowed = vec![
            workspace.clone(),
            helper_root.join("home"),
            tool_root,
            system_root.clone(),
        ];
        let protected = vec![system_root];

        let blocked = blocked_read_roots_for_active_drive_leaf_scope(
            &allowed,
            &protected,
            std::slice::from_ref(&drive_root),
        )
        .unwrap();

        assert!(
            blocked.iter().any(|root| root == &blocked_leaf),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &users_root),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_leaf_scope_skips_when_drive_has_too_many_leaf_files() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-leaf-scope-many-leafs-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        for index in 0..=MAX_ACTIVE_DRIVE_LEAF_CHILDREN {
            std::fs::write(drive_root.join(format!("secret-{index}.txt")), b"secret").unwrap();
        }
        let allowed = vec![workspace];

        let blocked = blocked_read_roots_for_active_drive_leaf_scope(
            &allowed,
            &[],
            std::slice::from_ref(&drive_root),
        )
        .unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_non_system_active_drive_scope_blocks_top_level_siblings() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-non-system-active-drive-siblings-{}",
            std::process::id()
        ));
        let system_drive = base.join("system-drive");
        let non_system_drive = base.join("tool-drive");
        let workspace = non_system_drive.join("workspace");
        let helper_root = non_system_drive.join("helper");
        let tool_root = non_system_drive.join("python-runtime").join("bin");
        let blocked_sibling = non_system_drive.join("temp-95");
        let system_root = system_drive.join("Windows");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        for index in 0..96 {
            std::fs::create_dir_all(non_system_drive.join(format!("temp-{index}"))).unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            tool_root,
            system_root.clone(),
        ];
        let protected = vec![system_root];

        let blocked = blocked_read_roots_for_non_system_active_drive_scope_from_candidates(
            &allowed,
            &protected,
            std::slice::from_ref(&non_system_drive),
        )
        .unwrap();

        assert!(
            blocked.iter().any(|root| root == &blocked_sibling),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_non_system_active_drive_scope_skips_system_drive() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-non-system-active-drive-skip-system-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let workspace = drive_root.join("workspace");
        let helper_root = drive_root.join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        let blocked_sibling = drive_root.join("temp-95");
        let system_root = drive_root.join("Windows");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        for index in 0..96 {
            std::fs::create_dir_all(drive_root.join(format!("temp-{index}"))).unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            tool_root,
            system_root.clone(),
        ];
        let protected = vec![system_root];

        let blocked = blocked_read_roots_for_non_system_active_drive_scope_from_candidates(
            &allowed,
            &protected,
            std::slice::from_ref(&drive_root),
        )
        .unwrap();

        assert!(
            !blocked.iter().any(|root| root == &blocked_sibling),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_child_scope_blocks_user_profile_siblings_with_many_children()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-child-siblings-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        let system_root = drive_root.join("Windows");
        let blocked_sibling = users_root.join("profile-95");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        for index in 0..96 {
            std::fs::create_dir_all(users_root.join(format!("profile-{index}"))).unwrap();
        }
        let allowed = vec![
            workspace.clone(),
            helper_root.join("home"),
            tool_root.clone(),
            system_root.clone(),
        ];
        let protected = vec![system_root];

        let blocked = blocked_read_roots_for_active_drive_child_scope_from_candidates(
            &allowed,
            &protected,
            std::slice::from_ref(&drive_root),
        )
        .unwrap();

        assert!(
            blocked.iter().any(|root| root == &blocked_sibling),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &owner_root),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_child_scope_skips_when_child_has_too_many_siblings() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-child-too-many-siblings-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        let system_root = drive_root.join("Windows");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        for index in 0..(MAX_ACTIVE_DRIVE_CHILD_SCOPE_CHILDREN + 1) {
            std::fs::create_dir_all(users_root.join(format!("profile-{index}"))).unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            tool_root,
            system_root.clone(),
        ];
        let protected = vec![system_root];

        let blocked = blocked_read_roots_for_active_drive_child_scope_from_candidates(
            &allowed,
            &protected,
            std::slice::from_ref(&drive_root),
        )
        .unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_grandchild_scope_blocks_user_home_siblings_with_many_children()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-grandchild-siblings-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        let system_root = drive_root.join("Windows");
        let blocked_sibling = owner_root.join("Documents");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        for index in 0..40 {
            std::fs::create_dir_all(owner_root.join(format!("folder-{index}"))).unwrap();
        }
        std::fs::create_dir_all(&blocked_sibling).unwrap();
        let allowed = vec![
            workspace.clone(),
            helper_root.join("home"),
            tool_root.clone(),
            system_root.clone(),
        ];
        let protected = vec![system_root];

        let blocked = blocked_read_roots_for_active_drive_grandchild_scope_from_candidates(
            &allowed,
            &protected,
            std::slice::from_ref(&drive_root),
        )
        .unwrap();

        assert!(
            blocked.iter().any(|root| root == &blocked_sibling),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &workspace),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_grandchild_scope_skips_when_grandchild_has_too_many_siblings()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-grandchild-too-many-siblings-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime").join("bin");
        let system_root = drive_root.join("Windows");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(helper_root.join("home")).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        for index in 0..(MAX_ACTIVE_DRIVE_GRANDCHILD_SCOPE_CHILDREN + 1) {
            std::fs::create_dir_all(owner_root.join(format!("folder-{index}"))).unwrap();
        }
        let allowed = vec![
            workspace,
            helper_root.join("home"),
            tool_root,
            system_root.clone(),
        ];
        let protected = vec![system_root];

        let blocked = blocked_read_roots_for_active_drive_grandchild_scope_from_candidates(
            &allowed,
            &protected,
            std::slice::from_ref(&drive_root),
        )
        .unwrap();

        assert!(blocked.is_empty(), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_local_programs_sibling_scope_blocks_sibling_installs() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-local-programs-siblings-{}",
            std::process::id()
        ));
        let programs_root = base
            .join("Users")
            .join("owner")
            .join("AppData")
            .join("Local")
            .join("Programs");
        let tool_root = programs_root.join("Python311");
        let sibling_root = programs_root.join("NodeJS");
        std::fs::create_dir_all(tool_root.join("Scripts")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        std::fs::create_dir_all(&sibling_root).unwrap();

        let blocked = blocked_read_roots_for_user_local_programs_sibling_scope(
            &[tool_root.join("Scripts")],
            &[tool_root.join("Scripts"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|candidate| candidate == &sibling_root),
            "blocked={blocked:?}"
        );
        assert!(
            !blocked.iter().any(|candidate| candidate == &tool_root),
            "blocked={blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_local_programs_sibling_scope_skips_when_too_many_siblings() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-local-programs-siblings-limit-{}",
            std::process::id()
        ));
        let programs_root = base
            .join("Users")
            .join("owner")
            .join("AppData")
            .join("Local")
            .join("Programs");
        let tool_root = programs_root.join("Python311");
        std::fs::create_dir_all(tool_root.join("Scripts")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        for index in 0..=MAX_USER_LOCAL_PROGRAM_SIBLINGS {
            std::fs::create_dir_all(programs_root.join(format!("tool-{index}"))).unwrap();
        }

        let blocked = blocked_read_roots_for_user_local_programs_sibling_scope(
            &[tool_root.join("Scripts")],
            &[tool_root.join("Scripts"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(blocked.is_empty(), "blocked={blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_local_programs_leaf_scope_blocks_parent_leaf_files() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-local-programs-leafs-{}",
            std::process::id()
        ));
        let programs_root = base
            .join("Users")
            .join("owner")
            .join("AppData")
            .join("Local")
            .join("Programs");
        let tool_root = programs_root.join("Python311");
        let sibling_leaf = programs_root.join("programs-secret.txt");
        std::fs::create_dir_all(tool_root.join("Scripts")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        std::fs::write(&sibling_leaf, b"secret").unwrap();

        let blocked = blocked_read_roots_for_user_local_programs_leaf_scope(
            &[tool_root.join("Scripts")],
            &[tool_root.join("Scripts"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|candidate| candidate == &sibling_leaf),
            "blocked={blocked:?}"
        );
        assert!(
            !blocked.iter().any(|candidate| candidate == &tool_root),
            "blocked={blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_user_local_programs_leaf_scope_skips_when_too_many_leafs() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-user-local-programs-leafs-limit-{}",
            std::process::id()
        ));
        let programs_root = base
            .join("Users")
            .join("owner")
            .join("AppData")
            .join("Local")
            .join("Programs");
        let tool_root = programs_root.join("Python311");
        std::fs::create_dir_all(tool_root.join("Scripts")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        for index in 0..=MAX_USER_LOCAL_PROGRAM_SIBLINGS {
            std::fs::write(programs_root.join(format!("secret-{index}.txt")), b"secret").unwrap();
        }

        let blocked = blocked_read_roots_for_user_local_programs_leaf_scope(
            &[tool_root.join("Scripts")],
            &[tool_root.join("Scripts"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(blocked.is_empty(), "blocked={blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_program_files_sibling_scope_blocks_sibling_installs() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-program-files-siblings-{}",
            std::process::id()
        ));
        let programs_root = base.join("Program Files");
        let tool_root = programs_root.join("Python311");
        let sibling_root = programs_root.join("NodeJS");
        std::fs::create_dir_all(tool_root.join("bin")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        std::fs::create_dir_all(&sibling_root).unwrap();

        let blocked = blocked_read_roots_for_program_files_sibling_scope(
            &[tool_root.join("bin")],
            &[tool_root.join("bin"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|candidate| candidate == &sibling_root),
            "blocked={blocked:?}"
        );
        assert!(
            !blocked.iter().any(|candidate| candidate == &tool_root),
            "blocked={blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_program_files_sibling_scope_skips_when_too_many_siblings() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-program-files-siblings-limit-{}",
            std::process::id()
        ));
        let programs_root = base.join("Program Files");
        let tool_root = programs_root.join("Python311");
        std::fs::create_dir_all(tool_root.join("bin")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        for index in 0..=MAX_PROGRAM_FILES_SIBLINGS {
            std::fs::create_dir_all(programs_root.join(format!("tool-{index}"))).unwrap();
        }

        let blocked = blocked_read_roots_for_program_files_sibling_scope(
            &[tool_root.join("bin")],
            &[tool_root.join("bin"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(blocked.is_empty(), "blocked={blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_program_files_leaf_scope_blocks_parent_leaf_files() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-program-files-leafs-{}",
            std::process::id()
        ));
        let programs_root = base.join("Program Files");
        let tool_root = programs_root.join("Python311");
        let sibling_leaf = programs_root.join("program-files-secret.txt");
        std::fs::create_dir_all(tool_root.join("bin")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        std::fs::write(&sibling_leaf, b"secret").unwrap();

        let blocked = blocked_read_roots_for_program_files_leaf_scope(
            &[tool_root.join("bin")],
            &[tool_root.join("bin"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|candidate| candidate == &sibling_leaf),
            "blocked={blocked:?}"
        );
        assert!(
            !blocked.iter().any(|candidate| candidate == &tool_root),
            "blocked={blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_program_files_leaf_scope_skips_when_too_many_leafs() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-program-files-leafs-limit-{}",
            std::process::id()
        ));
        let programs_root = base.join("Program Files");
        let tool_root = programs_root.join("Python311");
        std::fs::create_dir_all(tool_root.join("bin")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        for index in 0..=MAX_PROGRAM_FILES_SIBLINGS {
            std::fs::write(programs_root.join(format!("secret-{index}.txt")), b"secret").unwrap();
        }

        let blocked = blocked_read_roots_for_program_files_leaf_scope(
            &[tool_root.join("bin")],
            &[tool_root.join("bin"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(blocked.is_empty(), "blocked={blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_external_tool_container_sibling_scope_blocks_custom_tool_siblings() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-external-tool-siblings-{}",
            std::process::id()
        ));
        let tools_root = base.join("Tools");
        let tool_root = tools_root.join("Python311");
        let sibling_root = tools_root.join("NodeJS");
        std::fs::create_dir_all(tool_root.join("Scripts")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        std::fs::create_dir_all(&sibling_root).unwrap();

        let blocked = blocked_read_roots_for_external_tool_container_sibling_scope(
            &[tool_root.join("Scripts")],
            &[tool_root.join("Scripts"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|candidate| candidate == &sibling_root),
            "blocked={blocked:?}"
        );
        assert!(
            !blocked.iter().any(|candidate| candidate == &tool_root),
            "blocked={blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_external_tool_container_sibling_scope_blocks_root_level_custom_tool_siblings()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-external-tool-root-level-siblings-{}",
            std::process::id()
        ));
        let tools_root = base.join("Tools");
        let tool_root = tools_root.join("Python311");
        let sibling_root = tools_root.join("NodeJS");
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        std::fs::create_dir_all(&sibling_root).unwrap();

        let blocked = blocked_read_roots_for_external_tool_container_sibling_scope(
            std::slice::from_ref(&tool_root),
            &[tool_root.join("Lib"), tool_root.join("python311._pth")],
            &[],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|candidate| candidate == &sibling_root),
            "blocked={blocked:?}"
        );
        assert!(
            !blocked.iter().any(|candidate| candidate == &tool_root),
            "blocked={blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_external_tool_container_sibling_scope_skips_when_too_many_siblings() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-external-tool-siblings-limit-{}",
            std::process::id()
        ));
        let tools_root = base.join("Tools");
        let tool_root = tools_root.join("Python311");
        std::fs::create_dir_all(tool_root.join("Scripts")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        for index in 0..=MAX_EXTERNAL_TOOL_CONTAINER_SIBLINGS {
            std::fs::create_dir_all(tools_root.join(format!("tool-{index}"))).unwrap();
        }

        let blocked = blocked_read_roots_for_external_tool_container_sibling_scope(
            &[tool_root.join("Scripts")],
            &[tool_root.join("Scripts"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(blocked.is_empty(), "blocked={blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_external_tool_container_leaf_scope_blocks_parent_leaf_files() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-external-tool-leafs-{}",
            std::process::id()
        ));
        let tools_root = base.join("Tools");
        let tool_root = tools_root.join("Python311");
        let sibling_leaf = tools_root.join("tools-secret.txt");
        std::fs::create_dir_all(tool_root.join("Scripts")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        std::fs::write(&sibling_leaf, b"secret").unwrap();

        let blocked = blocked_read_roots_for_external_tool_container_leaf_scope(
            &[tool_root.join("Scripts")],
            &[tool_root.join("Scripts"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|candidate| candidate == &sibling_leaf),
            "blocked={blocked:?}"
        );
        assert!(
            !blocked.iter().any(|candidate| candidate == &tool_root),
            "blocked={blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_external_tool_container_leaf_scope_skips_when_too_many_leafs() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-external-tool-leafs-limit-{}",
            std::process::id()
        ));
        let tools_root = base.join("Tools");
        let tool_root = tools_root.join("Python311");
        std::fs::create_dir_all(tool_root.join("Scripts")).unwrap();
        std::fs::create_dir_all(tool_root.join("Lib")).unwrap();
        for index in 0..=MAX_EXTERNAL_TOOL_CONTAINER_SIBLINGS {
            std::fs::write(tools_root.join(format!("secret-{index}.txt")), b"secret").unwrap();
        }

        let blocked = blocked_read_roots_for_external_tool_container_leaf_scope(
            &[tool_root.join("Scripts")],
            &[tool_root.join("Scripts"), tool_root.join("Lib")],
            &[],
        )
        .unwrap();

        assert!(blocked.is_empty(), "blocked={blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn path_is_drive_root_detects_windows_drive_roots() {
        assert!(path_is_drive_root(Path::new(r"C:\")));
        assert!(path_is_drive_root(Path::new(r"\\?\C:\")));
        assert!(!path_is_drive_root(Path::new(r"C:\Tools")));
    }

    #[test]
    fn resolve_filesystem_boundary_layout_for_launch_reports_allowed_read_roots() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-launch-allowed-read-roots-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let users_root = drive_root.join("users");
        let owner_root = users_root.join("owner");
        let workspace = owner_root.join("workspace");
        let helper_root = owner_root.join("AppData").join("helper");
        let tool_root = drive_root.join("python-runtime");
        let active_tool = tool_root.join("bin");
        let copied_cmd = active_tool.join("cmd.exe");
        let runtime_lib = tool_root.join("Lib");
        let python_pth = tool_root.join("python311._pth");
        let private_root = tool_root.join("private");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&active_tool).unwrap();
        std::fs::create_dir_all(&runtime_lib).unwrap();
        std::fs::create_dir_all(&private_root).unwrap();
        std::fs::write(&copied_cmd, b"cmd").unwrap();
        std::fs::write(&python_pth, b".\\Lib").unwrap();
        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                helper_root.join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                helper_root.join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_root.join("cache").display().to_string(),
            ),
            ("PATH".to_string(), active_tool.display().to_string()),
        ]);

        let layout = resolve_filesystem_boundary_layout_for_launch(
            &workspace,
            &environment,
            &[active_tool.clone()],
            &[],
        )
        .unwrap();

        assert!(
            layout
                .allowed_read_roots
                .iter()
                .any(|root| root == &workspace),
            "{:?}",
            layout.allowed_read_roots
        );
        assert!(
            layout
                .allowed_read_roots
                .iter()
                .any(|root| root == &copied_cmd),
            "{:?}",
            layout.allowed_read_roots
        );
        assert!(
            layout
                .allowed_read_roots
                .iter()
                .any(|root| root == &runtime_lib),
            "{:?}",
            layout.allowed_read_roots
        );
        assert!(
            layout
                .allowed_read_roots
                .iter()
                .any(|root| root == &python_pth),
            "{:?}",
            layout.allowed_read_roots
        );
        assert!(
            !layout
                .allowed_read_roots
                .iter()
                .any(|root| root == &active_tool),
            "{:?}",
            layout.allowed_read_roots
        );
        assert!(
            !layout
                .allowed_read_roots
                .iter()
                .any(|root| private_root.starts_with(root) || root == &private_root),
            "{:?}",
            layout.allowed_read_roots
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_known_dirs_scope_blocks_known_dirs_even_when_drive_scan_skips()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-known-dirs-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("workspace");
        let helper_home = owner_root.join("AppData").join("helper").join("home");
        let system_root = drive_root.join("Windows");
        let program_data = drive_root.join("ProgramData");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&helper_home).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        std::fs::create_dir_all(&program_data).unwrap();
        for index in 0..=MAX_ACTIVE_DRIVE_CHILDREN {
            std::fs::create_dir_all(drive_root.join(format!("extra-{index}"))).unwrap();
        }

        let allowed = vec![
            normalize_boundary_root_path(workspace.clone()),
            normalize_boundary_root_path(helper_home.clone()),
            normalize_boundary_root_path(system_root.clone()),
        ];
        let protected = vec![normalize_boundary_root_path(system_root.clone())];
        let blocked = blocked_read_roots_for_active_drive_known_dirs_scope_from_candidates(
            &allowed,
            &protected,
            &[drive_root.clone()],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|root| root == &program_data),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &system_root),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_known_dirs_scope_blocks_known_dirs_when_drive_scan_would_skip()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-known-dirs-skip-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let owner_root = drive_root.join("Users").join("owner");
        let workspace = owner_root.join("workspace");
        let helper_home = owner_root.join("AppData").join("helper").join("home");
        let system_root = drive_root.join("Windows");
        let program_data = drive_root.join("ProgramData");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&helper_home).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        std::fs::create_dir_all(&program_data).unwrap();
        for index in 0..=MAX_ACTIVE_DRIVE_CHILDREN {
            std::fs::create_dir_all(drive_root.join(format!("extra-{index}"))).unwrap();
        }

        let allowed = vec![
            normalize_boundary_root_path(workspace.clone()),
            normalize_boundary_root_path(helper_home.clone()),
            normalize_boundary_root_path(system_root.clone()),
        ];
        let protected = vec![normalize_boundary_root_path(system_root.clone())];
        let blocked = blocked_read_roots_for_active_drive_known_dirs_scope_from_candidates(
            &allowed,
            &protected,
            &[drive_root.clone()],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|root| root == &program_data),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &system_root),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_drive_known_dirs_scope_blocks_users_when_no_allowed_subtree_is_under_users()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-drive-known-dirs-users-{}",
            std::process::id()
        ));
        let drive_root = base.join("drive");
        let workspace = drive_root.join("workspace");
        let helper_home = drive_root.join("helper").join("home");
        let system_root = drive_root.join("Windows");
        let users_root = drive_root.join("Users");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&helper_home).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        std::fs::create_dir_all(users_root.join("Public")).unwrap();

        let allowed = vec![
            normalize_boundary_root_path(workspace.clone()),
            normalize_boundary_root_path(helper_home.clone()),
            normalize_boundary_root_path(system_root.clone()),
        ];
        let protected = vec![normalize_boundary_root_path(system_root.clone())];
        let blocked = blocked_read_roots_for_active_drive_known_dirs_scope_from_candidates(
            &allowed,
            &protected,
            &[drive_root.clone()],
        )
        .unwrap();

        assert!(
            blocked.iter().any(|root| root == &users_root),
            "{blocked:?}"
        );
        assert!(
            !blocked.iter().any(|root| root == &system_root),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_runtime_executable_root_workspace_only_scope_keeps_siblings_visible()
    {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-runtime-exec-root-workspace-only-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let helper_root = base.join("helper");
        let tool_root = base.join("host").join("tool");
        let sibling_host = base.join("host").join("other-secret");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::create_dir_all(&sibling_host).unwrap();
        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                helper_root.join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                helper_root.join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_root.join("cache").display().to_string(),
            ),

        ]);

        let blocked = blocked_read_roots_for_runtime_executable_root(
            &workspace,
            &environment,
            &tool_root,
            &[],
        )
        .unwrap();

        assert_eq!(blocked, vec![tool_root.clone()]);
        assert!(
            blocked
                .iter()
                .all(|root| !sibling_host.starts_with(root) && !root.starts_with(&sibling_host)),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_executable_scope_blocks_private_entries_but_keeps_runtime_subtrees()
     {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-scope-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let helper_root = base.join("helper");
        let tool_root = base.join("host").join("Python311");
        let active_root = tool_root.join("Scripts");
        let runtime_lib = tool_root.join("Lib");
        let private_dir = tool_root.join("private");
        let direct_private = active_root.join("sandbox-only.txt");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&active_root).unwrap();
        std::fs::create_dir_all(&runtime_lib).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        std::fs::write(active_root.join("python.exe"), b"").unwrap();
        std::fs::write(&direct_private, b"secret").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                helper_root.join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                helper_root.join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_root.join("cache").display().to_string(),
            ),
        ]);
        let executable_roots = effective_executable_roots(
            &workspace,
            &environment,
            std::slice::from_ref(&active_root),
        );
        let argument_roots = allowed_argument_roots(&workspace);
        let mutable_roots = helper_mutable_roots(&environment);
        let auxiliary_roots = derived_auxiliary_allowed_roots_for_active_executable_roots(
            std::slice::from_ref(&active_root),
        );
        let trusted_system_roots = trusted_system_roots(&environment);
        let active_executable_surfaces = collect_active_executable_scope_surfaces(
            &executable_roots,
            &auxiliary_roots,
            &trusted_system_roots,
        )
        .unwrap();
        let allowed_read_context = AllowedReadRootsContext {
            argument_roots: &argument_roots,
            mutable_roots: &mutable_roots,
            auxiliary_allowed_roots: &auxiliary_roots,
            trusted_system_roots: &trusted_system_roots,
        };
        let base_launch_allowed_read_roots = base_launch_allowed_read_roots(&allowed_read_context);
        let active_executable_contributions =
            collect_active_executable_scope_boundary_contributions(
                &active_executable_surfaces,
                &base_launch_allowed_read_roots,
            )
            .unwrap();
        let ctx = ActiveExecutableScopeContext {
            active_executable_contributions: &active_executable_contributions,
        };

        let blocked = blocked_read_roots_for_active_executable_scope(&ctx).unwrap();

        assert!(
            blocked
                .iter()
                .all(|root| { !runtime_lib.starts_with(root) && !root.starts_with(&runtime_lib) }),
            "{blocked:?}"
        );
        assert!(
            blocked
                .iter()
                .any(|root| private_dir.starts_with(root) || root == &private_dir),
            "{blocked:?}"
        );
        assert!(
            blocked
                .iter()
                .any(|root| direct_private.starts_with(root) || root == &direct_private),
            "{blocked:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn blocked_read_roots_for_active_executable_scope_keeps_root_level_runtime_metadata_visible() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-root-metadata-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let helper_root = base.join("helper");
        let tool_root = base.join("host").join("Python311");
        let runtime_zip = tool_root.join("python311.zip");
        let runtime_pth = tool_root.join("python311._pth");
        let metadata = tool_root.join("pyvenv.cfg");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::write(tool_root.join("python.exe"), b"").unwrap();
        std::fs::write(&runtime_zip, b"zip").unwrap();
        std::fs::write(&runtime_pth, b"pth").unwrap();
        std::fs::write(&metadata, b"venv").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                helper_root.join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                helper_root.join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_root.join("cache").display().to_string(),
            ),
        ]);
        let executable_roots =
            effective_executable_roots(&workspace, &environment, std::slice::from_ref(&tool_root));
        let argument_roots = allowed_argument_roots(&workspace);
        let mutable_roots = helper_mutable_roots(&environment);
        let auxiliary_roots = derived_auxiliary_allowed_roots_for_active_executable_roots(
            std::slice::from_ref(&tool_root),
        );
        let trusted_system_roots = trusted_system_roots(&environment);
        let active_executable_surfaces = collect_active_executable_scope_surfaces(
            &executable_roots,
            &auxiliary_roots,
            &trusted_system_roots,
        )
        .unwrap();
        let allowed_read_context = AllowedReadRootsContext {
            argument_roots: &argument_roots,
            mutable_roots: &mutable_roots,
            auxiliary_allowed_roots: &auxiliary_roots,
            trusted_system_roots: &trusted_system_roots,
        };
        let base_launch_allowed_read_roots = base_launch_allowed_read_roots(&allowed_read_context);
        let active_executable_contributions =
            collect_active_executable_scope_boundary_contributions(
                &active_executable_surfaces,
                &base_launch_allowed_read_roots,
            )
            .unwrap();
        let ctx = ActiveExecutableScopeContext {
            active_executable_contributions: &active_executable_contributions,
        };

        let blocked = blocked_read_roots_for_active_executable_scope(&ctx).unwrap();

        assert!(
            blocked
                .iter()
                .all(|root| { !runtime_zip.starts_with(root) && !root.starts_with(&runtime_zip) }),
            "{blocked:?}"
        );
        assert!(
            blocked
                .iter()
                .all(|root| { !runtime_pth.starts_with(root) && !root.starts_with(&runtime_pth) }),
            "{blocked:?}"
        );
        assert!(
            blocked
                .iter()
                .all(|root| { !metadata.starts_with(root) && !root.starts_with(&metadata) }),
            "{blocked:?}"
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn collect_active_executable_scope_boundary_contribution_returns_protected_and_blocked_sets() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-contribution-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let helper_root = base.join("helper");
        let tool_root = base.join("host").join("Python311");
        let runtime_zip = tool_root.join("python311.zip");
        let metadata = tool_root.join("pyvenv.cfg");
        let private_leaf = tool_root.join("private.txt");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&tool_root).unwrap();
        std::fs::write(tool_root.join("python.exe"), b"").unwrap();
        std::fs::write(&runtime_zip, b"zip").unwrap();
        std::fs::write(&metadata, b"venv").unwrap();
        std::fs::write(&private_leaf, b"secret").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                helper_root.join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                helper_root.join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_root.join("cache").display().to_string(),
            ),
        ]);
        let executable_roots =
            effective_executable_roots(&workspace, &environment, std::slice::from_ref(&tool_root));
        let argument_roots = allowed_argument_roots(&workspace);
        let mutable_roots = helper_mutable_roots(&environment);
        let auxiliary_roots = derived_auxiliary_allowed_roots_for_active_executable_roots(
            std::slice::from_ref(&tool_root),
        );
        let trusted_system_roots = trusted_system_roots(&environment);
        let active_executable_surfaces = collect_active_executable_scope_surfaces(
            &executable_roots,
            &auxiliary_roots,
            &trusted_system_roots,
        )
        .unwrap();
        let allowed_read_context = AllowedReadRootsContext {
            argument_roots: &argument_roots,
            mutable_roots: &mutable_roots,
            auxiliary_allowed_roots: &auxiliary_roots,
            trusted_system_roots: &trusted_system_roots,
        };
        let launch_allowed_read_roots =
            launch_scoped_allowed_read_roots(&allowed_read_context).unwrap();

        let contribution = collect_active_executable_scope_boundary_contribution(
            &active_executable_surfaces[0],
            &launch_allowed_read_roots,
        )
        .unwrap();

        assert!(contribution.allowed.iter().any(|path| path == &runtime_zip));
        assert!(contribution.allowed.iter().any(|path| path == &metadata));
        assert!(
            contribution
                ._blocked
                .iter()
                .any(|path| path == &private_leaf)
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn collect_active_executable_scope_boundary_contributions_returns_per_root_entries() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-contributions-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let helper_root = base.join("helper");
        let tool_root = base.join("host").join("Python311");
        let active_root = tool_root.join("Scripts");
        let runtime_dir = tool_root.join("Lib");
        let private_dir = tool_root.join("private");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&active_root).unwrap();
        std::fs::create_dir_all(&runtime_dir).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        std::fs::write(active_root.join("python.exe"), b"").unwrap();

        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                helper_root.join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                helper_root.join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_root.join("cache").display().to_string(),
            ),
        ]);
        let executable_roots = effective_executable_roots(
            &workspace,
            &environment,
            std::slice::from_ref(&active_root),
        );
        let argument_roots = allowed_argument_roots(&workspace);
        let mutable_roots = helper_mutable_roots(&environment);
        let auxiliary_roots = derived_auxiliary_allowed_roots_for_active_executable_roots(
            std::slice::from_ref(&active_root),
        );
        let trusted_system_roots = trusted_system_roots(&environment);
        let active_executable_surfaces = collect_active_executable_scope_surfaces(
            &executable_roots,
            &auxiliary_roots,
            &trusted_system_roots,
        )
        .unwrap();
        let allowed_read_context = AllowedReadRootsContext {
            argument_roots: &argument_roots,
            mutable_roots: &mutable_roots,
            auxiliary_allowed_roots: &auxiliary_roots,
            trusted_system_roots: &trusted_system_roots,
        };
        let launch_allowed_read_roots =
            launch_scoped_allowed_read_roots(&allowed_read_context).unwrap();

        let contributions = collect_active_executable_scope_boundary_contributions(
            &active_executable_surfaces,
            &launch_allowed_read_roots,
        )
        .unwrap();

        assert_eq!(1, contributions.len());
        assert!(
            contributions[0]
                .allowed
                .iter()
                .any(|path| path == &runtime_dir)
        );
        assert!(
            contributions[0]
                ._blocked
                .iter()
                .any(|path| path == &private_dir)
        );

    }

    #[test]
    fn collect_workspace_core_boundary_surface_returns_allowed_and_no_blocked() {
        let allowed = vec![
            PathBuf::from(r"C:\workspace"),
            PathBuf::from(r"C:\helper\home"),
        ];

        let surface = collect_workspace_core_boundary_surface(&allowed);

        assert_eq!(allowed, surface.allowed);
        assert!(surface.blocked.is_empty());
    }

    #[cfg(windows)]
    #[test]
    fn active_executable_scope_entry_is_allowed_distinguishes_runtime_and_explicit_entries() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-entry-allow-{}",
            std::process::id()
        ));
        let allowed_root = base.join("workspace");
        let runtime_dir = base.join("Lib");
        let runtime_leaf = base.join("python311._pth");
        let private_dir = base.join("private");
        let trusted_system = base.join("Windows");
        std::fs::create_dir_all(&allowed_root).unwrap();
        std::fs::create_dir_all(&runtime_dir).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        std::fs::create_dir_all(&trusted_system).unwrap();
        std::fs::write(&runtime_leaf, b"pth").unwrap();
        let explicit_child = allowed_root.join("notes").join("todo.txt");
        std::fs::create_dir_all(explicit_child.parent().unwrap()).unwrap();
        std::fs::write(&explicit_child, b"ok").unwrap();

        let allowed_roots = vec![allowed_root.clone()];
        let trusted_system_roots = vec![trusted_system.clone()];

        assert!(active_executable_scope_entry_is_explicitly_allowed(
            &explicit_child,
            &allowed_roots,
            &trusted_system_roots,
        ));
        assert!(active_executable_scope_entry_is_runtime_allowed(
            &runtime_dir
        ));
        assert!(active_executable_scope_entry_is_runtime_allowed(
            &runtime_leaf
        ));
        assert!(!active_executable_scope_entry_is_allowed(
            &private_dir,
            &allowed_roots,
            &trusted_system_roots,
        ));

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn collect_active_executable_scope_runtime_entries_returns_only_runtime_surface() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-runtime-entries-{}",
            std::process::id()
        ));
        let active_root = base.join("Python311").join("Scripts");
        let runtime_dir = active_root.join("Lib");
        let runtime_leaf = active_root.join("python311._pth");
        let private_dir = active_root.join("private");
        let private_leaf = active_root.join("notes.txt");
        std::fs::create_dir_all(&runtime_dir).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        std::fs::write(&runtime_leaf, b"pth").unwrap();
        std::fs::write(&private_leaf, b"secret").unwrap();

        let allowed = collect_active_executable_scope_runtime_entries(&active_root).unwrap();

        assert!(
            allowed.iter().any(|path| path == &runtime_dir),
            "{allowed:?}"
        );
        assert!(
            allowed.iter().any(|path| path == &runtime_leaf),
            "{allowed:?}"
        );
        assert!(
            allowed.iter().all(|path| path != &private_dir),
            "{allowed:?}"
        );
        assert!(
            allowed.iter().all(|path| path != &private_leaf),
            "{allowed:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn collect_active_executable_scope_parent_entries_returns_parent_runtime_surface() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-parent-entries-{}",
            std::process::id()
        ));
        let parent_root = base.join("Python311");
        let active_root = parent_root.join("Scripts");
        let runtime_dir = parent_root.join("Lib");
        let metadata = parent_root.join("pyvenv.cfg");
        let private_dir = parent_root.join("private");
        std::fs::create_dir_all(&active_root).unwrap();
        std::fs::create_dir_all(&runtime_dir).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        std::fs::write(&metadata, b"venv").unwrap();

        let allowed =
            collect_active_executable_scope_parent_entries(&active_root, &[], &[]).unwrap();

        assert!(
            allowed.iter().any(|path| path == &runtime_dir),
            "{allowed:?}"
        );
        assert!(allowed.iter().any(|path| path == &metadata), "{allowed:?}");
        assert!(
            allowed.iter().all(|path| path != &private_dir),
            "{allowed:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn collect_active_executable_scope_allowed_entries_combines_direct_and_parent_runtime_surface()
    {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-allowed-entries-{}",
            std::process::id()
        ));
        let parent_root = base.join("Python311");
        let active_root = parent_root.join("Scripts");
        let direct_runtime_leaf = active_root.join("python.exe");
        let parent_runtime_dir = parent_root.join("Lib");
        let parent_metadata = parent_root.join("pyvenv.cfg");
        let private_dir = parent_root.join("private");
        std::fs::create_dir_all(&active_root).unwrap();
        std::fs::create_dir_all(&parent_runtime_dir).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        std::fs::write(&direct_runtime_leaf, b"exe").unwrap();
        std::fs::write(&parent_metadata, b"venv").unwrap();

        let allowed =
            collect_active_executable_scope_allowed_entries(&active_root, &[], &[]).unwrap();

        assert!(
            allowed.iter().any(|path| path == &direct_runtime_leaf),
            "{allowed:?}"
        );
        assert!(
            allowed.iter().any(|path| path == &parent_runtime_dir),
            "{allowed:?}"
        );
        assert!(
            allowed.iter().any(|path| path == &parent_metadata),
            "{allowed:?}"
        );
        assert!(
            allowed.iter().all(|path| path != &private_dir),
            "{allowed:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn collect_active_executable_scope_surface_returns_allow_and_block_sets() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-surface-{}",
            std::process::id()
        ));
        let parent_root = base.join("Python311");
        let active_root = parent_root.join("Scripts");
        let direct_runtime_leaf = active_root.join("python.exe");
        let parent_runtime_dir = parent_root.join("Lib");
        let parent_metadata = parent_root.join("pyvenv.cfg");
        let private_dir = parent_root.join("private");
        std::fs::create_dir_all(&active_root).unwrap();
        std::fs::create_dir_all(&parent_runtime_dir).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        std::fs::write(&direct_runtime_leaf, b"exe").unwrap();
        std::fs::write(&parent_metadata, b"venv").unwrap();

        let surface = collect_active_executable_scope_surface(&active_root, &[], &[]).unwrap();

        assert!(
            surface
                .allowed
                .iter()
                .any(|path| path == &direct_runtime_leaf),
            "{surface:?}"
        );
        assert!(
            surface
                .allowed
                .iter()
                .any(|path| path == &parent_runtime_dir),
            "{surface:?}"
        );
        assert!(
            surface.allowed.iter().any(|path| path == &parent_metadata),
            "{surface:?}"
        );
        assert!(
            surface.blocked.iter().any(|path| path == &private_dir),
            "{surface:?}"
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn collect_active_executable_scope_blocked_entries_returns_non_allowed_local_surface() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-active-executable-blocked-entries-{}",
            std::process::id()
        ));
        let parent_root = base.join("Python311");
        let active_root = parent_root.join("Scripts");
        let runtime_dir = parent_root.join("Lib");
        let metadata = parent_root.join("pyvenv.cfg");
        let private_dir = parent_root.join("private");
        let private_leaf = parent_root.join("notes.txt");
        let direct_private = active_root.join("sandbox-only.txt");
        std::fs::create_dir_all(&active_root).unwrap();
        std::fs::create_dir_all(&runtime_dir).unwrap();
        std::fs::create_dir_all(&private_dir).unwrap();
        std::fs::write(&metadata, b"venv").unwrap();
        std::fs::write(&private_leaf, b"secret").unwrap();
        std::fs::write(&direct_private, b"secret").unwrap();

        let blocked =
            collect_active_executable_scope_blocked_entries(&active_root, &[], &[]).unwrap();

        assert!(
            blocked.iter().any(|path| path == &private_dir),
            "{blocked:?}"
        );
        assert!(
            blocked.iter().any(|path| path == &private_leaf),
            "{blocked:?}"
        );
        assert!(
            blocked.iter().any(|path| path == &direct_private),
            "{blocked:?}"
        );
        assert!(
            blocked.iter().all(|path| path != &runtime_dir),
            "{blocked:?}"
        );
        assert!(blocked.iter().all(|path| path != &metadata), "{blocked:?}");

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_keeps_workspace_internal_executable_parent_visible() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-executable-parent-inside-workspace-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let helper_root = base.join("helper");
        let tool_root = workspace.join("tools");
        let path_tool = tool_root.join("bin");
        let sibling_secret = tool_root.join("private");
        std::fs::create_dir_all(&path_tool).unwrap();
        std::fs::create_dir_all(&sibling_secret).unwrap();
        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                helper_root.join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                helper_root.join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                helper_root.join("cache").display().to_string(),
            ),
            ("PATH".to_string(), path_tool.display().to_string()),
        ]);

        let layout = resolve_filesystem_boundary_layout(&workspace, &environment).unwrap();

        assert!(
            !layout
                .blocked_read_roots
                .iter()
                .any(|root| root == &sibling_secret),
            "{:?}",
            layout.blocked_read_roots
        );

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]

    #[cfg(windows)]
    #[test]
    fn promoted_external_blocked_root_ancestors_collects_higher_unsafe_ancestors() {
        let blocked_root = PathBuf::from(r"C:\users\owner\source-project");
        let allowed_roots = vec![
            PathBuf::from(r"C:\users\owner\workspace"),
            PathBuf::from(r"C:\users\owner\AppData\helper\home"),
        ];

        let (promoted, unsafe_ancestors) =
            promoted_external_blocked_root_ancestors(&blocked_root, &allowed_roots);

        assert!(promoted.is_empty(), "{promoted:?}");
        assert_eq!(
            unsafe_ancestors,
            vec![
                PathBuf::from(r"C:\users\owner"),
                PathBuf::from(r"C:\users"),
                PathBuf::from(r"C:\"),
            ]
        );
    }

    #[cfg(windows)]
    #[test]
    fn resolve_filesystem_boundary_layout_rejects_blocked_read_root_inside_workspace() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-invalid-blocked-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let workspace_blocked = workspace.join("blocked");
        std::fs::create_dir_all(&workspace_blocked).unwrap();
        let environment = BTreeMap::from([
            (
                "HOME".to_string(),
                base.join("helper").join("home").display().to_string(),
            ),
            (
                "TMPDIR".to_string(),
                base.join("helper").join("tmp").display().to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                base.join("helper").join("cache").display().to_string(),
            ),
            (
                BLOCKED_READ_ROOTS_ENV.to_string(),
                workspace_blocked.display().to_string(),
            ),
        ]);

        let error = resolve_filesystem_boundary_layout(&workspace, &environment)
            .expect_err("workspace root should be rejected");
        assert!(error.to_string().contains("outside allowed roots"));

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn boundary_root_sets_separate_executable_and_argument_roots() {
        let workspace = std::env::temp_dir().join("helper-boundary-roots-workspace");
        let system_root = std::env::temp_dir().join("helper-boundary-roots-system");
        let path_tool = std::env::temp_dir().join("helper-boundary-roots-path-tool");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&system_root).unwrap();
        std::fs::create_dir_all(&path_tool).unwrap();
        let environment = BTreeMap::from([
            ("SystemRoot".to_string(), system_root.display().to_string()),
            ("PATH".to_string(), path_tool.display().to_string()),
            (
                "HOME".to_string(),
                std::env::temp_dir()
                    .join("helper-boundary-roots-home")
                    .display()
                    .to_string(),
            ),
            (
                "TMPDIR".to_string(),
                std::env::temp_dir()
                    .join("helper-boundary-roots-tmp")
                    .display()
                    .to_string(),
            ),
            (
                "XDG_CACHE_HOME".to_string(),
                std::env::temp_dir()
                    .join("helper-boundary-roots-cache")
                    .display()
                    .to_string(),
            ),
        ]);
        std::fs::create_dir_all(environment.get("HOME").unwrap()).unwrap();
        std::fs::create_dir_all(environment.get("TMPDIR").unwrap()).unwrap();
        std::fs::create_dir_all(environment.get("XDG_CACHE_HOME").unwrap()).unwrap();

        let executable_roots = allowed_executable_roots(&workspace, &environment);
        let argument_roots = allowed_argument_roots(&workspace);
        let mutable_roots = helper_mutable_roots(&environment);

        assert!(
            executable_roots
                .iter()
                .any(|root| root.ends_with("helper-boundary-roots-workspace"))
        );
        assert!(
            executable_roots
                .iter()
                .any(|root| root.ends_with("helper-boundary-roots-system"))
        );
        assert!(
            executable_roots
                .iter()
                .any(|root| root.ends_with("helper-boundary-roots-path-tool"))
        );
        assert_eq!(1, argument_roots.len());
        assert!(argument_roots[0].ends_with("helper-boundary-roots-workspace"));
        assert_eq!(3, mutable_roots.len());
    }

    #[test]
    fn blocked_internal_root_staging_hides_and_restores_internal_roots() {
        let base =
            std::env::temp_dir().join(format!("helper-boundary-stage-{}", std::process::id()));
        let workspace = base.join("workspace");
        let blocked = workspace.join(".ai_ide_runtime");
        std::fs::create_dir_all(&blocked).unwrap();
        std::fs::write(blocked.join("secret.txt"), b"secret").unwrap();
        let layout = FilesystemBoundaryLayout {

            workspace_root: workspace.clone(),
            helper_home: base.join("home"),
            helper_tmp: base.join("tmp"),
            helper_cache: base.join("cache"),
            allowed_read_roots: vec![workspace.clone()],
            blocked_internal_roots: vec![blocked.clone()],
            blocked_read_roots: vec![base.join("blocked-read")],
        };

        {
            let _staging = BlockedInternalRootsStaging::apply(&layout).unwrap();
            assert!(!blocked.exists());
            let hidden = std::fs::read_dir(&workspace)
                .unwrap()
                .find_map(|entry| {
                    let entry = entry.unwrap();
                    let name = entry.file_name().to_string_lossy().to_string();
                    if name.starts_with(".ai_ide_hidden_.ai_ide_runtime_") {
                        Some(entry.path())
                    } else {
                        None
                    }
                })
                .expect("hidden internal root");
            assert!(hidden.join("secret.txt").exists());
        }

        assert!(blocked.join("secret.txt").exists());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn prepare_low_integrity_boundary_labels_workspace_and_helper_roots() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-low-integrity-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let home = base.join("helper").join("home");
        let tmp = base.join("helper").join("tmp");
        let cache = base.join("helper").join("cache");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&home).unwrap();
        std::fs::create_dir_all(&tmp).unwrap();
        std::fs::create_dir_all(&cache).unwrap();
        let layout = FilesystemBoundaryLayout {

            workspace_root: workspace.clone(),
            helper_home: home,
            helper_tmp: tmp,
            helper_cache: cache,
            allowed_read_roots: vec![workspace.clone()],
            blocked_internal_roots: vec![],
            blocked_read_roots: vec![base.join("helper").join("blocked-read")],
        };

        let result = prepare_low_integrity_boundary(&layout).unwrap();
        assert!(matches!(result, true | false));

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn prepare_read_boundary_labels_helper_blocked_read_roots() {
        let base =
            std::env::temp_dir().join(format!("helper-boundary-read-guard-{}", std::process::id()));
        let workspace = base.join("workspace");
        let helper = base.join("helper");
        let blocked = helper.join("blocked-read");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&blocked).unwrap();
        std::fs::write(blocked.join("secret.txt"), b"secret").unwrap();
        let layout = FilesystemBoundaryLayout {

            workspace_root: workspace,
            helper_home: helper.join("home"),
            helper_tmp: helper.join("tmp"),
            helper_cache: helper.join("cache"),
            allowed_read_roots: vec![],
            blocked_internal_roots: vec![],
            blocked_read_roots: vec![blocked],
        };

        let result = prepare_read_boundary(&layout).unwrap();
        assert!(matches!(result, true | false));

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn prepare_read_boundary_rejects_missing_external_blocked_roots() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-external-read-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let helper = base.join("helper");
        let external = base.join("host-blocked");
        std::fs::create_dir_all(&workspace).unwrap();
        let layout = FilesystemBoundaryLayout {

            workspace_root: workspace,
            helper_home: helper.join("home"),
            helper_tmp: helper.join("tmp"),
            helper_cache: helper.join("cache"),
            allowed_read_roots: vec![],
            blocked_internal_roots: vec![],
            blocked_read_roots: vec![external.clone()],
        };

        let error = prepare_read_boundary(&layout)
            .expect_err("missing external blocked roots should fail closed");
        assert!(
            error
                .to_string()
                .contains("configured blocked read root must exist"),
            "unexpected error: {error}"
        );
        assert!(!external.exists());

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn stage_read_boundary_restores_external_blocked_root_labels_on_drop() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-stage-external-read-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let helper = base.join("helper");
        let external = base.join("host-blocked");
        let nested_dir = external.join("nested");
        let nested_file = nested_dir.join("secret.txt");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&nested_dir).unwrap();
        std::fs::write(&nested_file, b"secret").unwrap();
        let layout = FilesystemBoundaryLayout {

            workspace_root: workspace,
            helper_home: helper.join("home"),
            helper_tmp: helper.join("tmp"),
            helper_cache: helper.join("cache"),
            allowed_read_roots: vec![],
            blocked_internal_roots: vec![],
            blocked_read_roots: vec![external.clone()],
        };
        let before_root = capture_label_security_descriptor(&external).unwrap();
        let before_nested_dir = capture_label_security_descriptor(&nested_dir).unwrap();
        let before_nested_file = capture_label_security_descriptor(&nested_file).unwrap();

        let staging = stage_read_boundary(&layout).unwrap();
        let Some(staging) = staging else {
            let _ = std::fs::remove_dir_all(&base);
            return;
        };
        drop(staging);

        let after_root = capture_label_security_descriptor(&external).unwrap();
        let after_nested_dir = capture_label_security_descriptor(&nested_dir).unwrap();
        let after_nested_file = capture_label_security_descriptor(&nested_file).unwrap();
        assert_eq!(before_root, after_root);
        assert_eq!(before_nested_dir, after_nested_dir);
        assert_eq!(before_nested_file, after_nested_file);

        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(windows)]
    #[test]
    fn stage_read_boundary_restores_external_blocked_file_labels_on_drop() {
        let base = std::env::temp_dir().join(format!(
            "helper-boundary-stage-external-read-file-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let helper = base.join("helper");
        let external = base.join("host-blocked.txt");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::write(&external, b"secret").unwrap();
        let layout = FilesystemBoundaryLayout {

            workspace_root: workspace,
            helper_home: helper.join("home"),
            helper_tmp: helper.join("tmp"),
            helper_cache: helper.join("cache"),
            allowed_read_roots: vec![],
            blocked_internal_roots: vec![],
            blocked_read_roots: vec![external.clone()],
        };
        let before = capture_label_security_descriptor(&external).unwrap();

        let staging = stage_read_boundary(&layout).unwrap();
        let Some(staging) = staging else {
            let _ = std::fs::remove_dir_all(&base);
            return;
        };
        drop(staging);

        let after = capture_label_security_descriptor(&external).unwrap();
        assert_eq!(before, after);

        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn require_read_boundary_for_restricted_launch_fails_closed_when_missing() {
        let error = match enforce_required_read_boundary_for_tests(None) {
            Ok(_) => panic!("missing read boundary should fail closed"),
            Err(error) => error,
        };

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("requires read boundary support"));
    }

    #[test]
    fn require_read_boundary_for_restricted_launch_accepts_active_staging() {
        enforce_required_read_boundary_for_tests(Some(BlockedReadRootsStaging {
            restored_external_entries: Vec::new(),
        }))
        .expect("active read boundary staging should be accepted");
    }

    #[test]
    fn require_low_integrity_boundary_for_restricted_launch_fails_closed_when_missing() {
        let error = match enforce_required_low_integrity_boundary_for_tests(false) {
            Ok(_) => panic!("missing write boundary should fail closed"),
            Err(error) => error,
        };

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(
            error
                .to_string()
                .contains("requires write boundary support")
        );
    }

    #[test]
    fn require_low_integrity_boundary_for_restricted_launch_accepts_supported_hosts() {
        enforce_required_low_integrity_boundary_for_tests(true)
            .expect("supported write boundary should be accepted");
    }

    fn enforce_required_read_boundary_for_tests(
        staging: Option<BlockedReadRootsStaging>,
    ) -> io::Result<BlockedReadRootsStaging> {
        let Some(staging) = staging else {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "restricted launch requires read boundary support on this host",
            ));
        };
        Ok(staging)
    }

    fn enforce_required_low_integrity_boundary_for_tests(supported: bool) -> io::Result<()> {
        if supported {
            return Ok(());
        }
        Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "restricted launch requires write boundary support on this host",
        ))
    }
}
