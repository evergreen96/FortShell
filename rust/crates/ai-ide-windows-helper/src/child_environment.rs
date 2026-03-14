use std::collections::BTreeMap;
use std::io;
#[cfg(windows)]
use std::path::Component;
use std::path::{Path, PathBuf};

#[cfg(windows)]
use std::ffi::OsString;
#[cfg(windows)]
use std::os::windows::ffi::OsStringExt;
#[cfg(windows)]
use windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW;

use crate::path_alias_guard::path_is_hardlink_alias_under_root;
use crate::path_policy::{
    path_targets_internal_workspace_root, path_traverses_reparse_point_under_root,
    path_traverses_workspace_reparse_point, path_uses_windows_alternate_data_stream,
    path_uses_windows_reserved_device_name, path_uses_windows_root_relative_form,
    relative_path_targets_internal_workspace_root,
};

pub fn merged_environment(overrides: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    let base = std::env::vars().collect::<BTreeMap<_, _>>();
    build_child_environment_from_base(&base, overrides)
}

pub fn prepare_child_environment(
    workspace: &Path,
    overrides: &BTreeMap<String, String>,
) -> io::Result<BTreeMap<String, String>> {
    reject_reserved_override_keys(overrides)?;
    let layout = resolve_sandbox_layout(workspace, overrides)?;
    #[cfg(windows)]
    validate_path_like_override_values(workspace, overrides, &layout)?;
    std::fs::create_dir_all(&layout.home)?;
    std::fs::create_dir_all(&layout.temp)?;
    std::fs::create_dir_all(&layout.cache)?;

    #[cfg(windows)]
    let mut prepared = normalize_path_like_override_values(workspace, overrides);
    #[cfg(not(windows))]
    let mut prepared = overrides.clone();
    prepared.insert("AI_IDE_SANDBOX_ROOT".to_string(), "/workspace".to_string());
    prepared.insert(
        "HOME".to_string(),
        layout.home.to_string_lossy().to_string(),
    );
    prepared.insert(
        "TMPDIR".to_string(),
        layout.temp.to_string_lossy().to_string(),
    );
    prepared.insert(
        "XDG_CACHE_HOME".to_string(),
        layout.cache.to_string_lossy().to_string(),
    );
    Ok(merged_environment(&prepared))
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct HelperSandboxLayout {
    root: PathBuf,
    home: PathBuf,
    temp: PathBuf,
    cache: PathBuf,
}

const RESERVED_OVERRIDE_KEYS: &[&str] = &[
    "PATH",
    "PATHEXT",
    "NoDefaultCurrentDirectoryInExePath",
    "SystemRoot",
    "WINDIR",
    "ComSpec",
    "USERPROFILE",
    "TEMP",
    "TMP",
];

#[cfg(windows)]
const PATH_LIST_OVERRIDE_KEYS: &[&str] = &[
    "PYTHONPATH",
    "NODE_PATH",
    "GEM_PATH",
    "RUBYLIB",
    "PERL5LIB",
    "CLASSPATH",
    "LIB",
    "LIBPATH",
    "INCLUDE",
];

#[cfg(windows)]
const SINGLE_PATH_OVERRIDE_KEYS: &[&str] = &[
    "PYTHONHOME",
    "GEM_HOME",
    "VIRTUAL_ENV",
    "NPM_CONFIG_CACHE",
    "PIP_CACHE_DIR",
    "PIP_TARGET",
    "PIP_PREFIX",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "DOTNET_SHARED_STORE",
];

fn reject_reserved_override_keys(overrides: &BTreeMap<String, String>) -> io::Result<()> {
    for key in RESERVED_OVERRIDE_KEYS {
        if let Some((name, _)) = overrides
            .iter()
            .find(|(candidate, _)| candidate.eq_ignore_ascii_case(key))
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("{name} must not be overridden in strict helper env"),
            ));
        }
    }
    Ok(())
}

#[cfg(windows)]
fn validate_path_like_override_values(
    workspace: &Path,
    overrides: &BTreeMap<String, String>,
    layout: &HelperSandboxLayout,
) -> io::Result<()> {
    for (key, value) in overrides {
        if key_matches(key, PATH_LIST_OVERRIDE_KEYS) {
            for entry in value.split(';') {
                validate_override_path_entry(key, workspace, layout, entry)?;
            }
            continue;
        }
        if key_matches(key, SINGLE_PATH_OVERRIDE_KEYS) {
            validate_override_path_entry(key, workspace, layout, value)?;
        }
    }
    Ok(())
}

#[cfg(windows)]
fn normalize_path_like_override_values(
    workspace: &Path,
    overrides: &BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    let mut normalized = overrides.clone();
    for (key, value) in overrides {
        if key_matches(key, PATH_LIST_OVERRIDE_KEYS) {
            let entries = value
                .split(';')
                .filter_map(|entry| normalize_override_path_entry(workspace, entry))
                .collect::<Vec<_>>();
            normalized.insert(key.clone(), entries.join(";"));
            continue;
        }
        if key_matches(key, SINGLE_PATH_OVERRIDE_KEYS) {
            if let Some(entry) = normalize_override_path_entry(workspace, value) {
                normalized.insert(key.clone(), entry);
            }
        }
    }
    normalized
}

#[cfg(windows)]
fn normalize_override_path_entry(workspace: &Path, raw_value: &str) -> Option<String> {
    let trimmed = raw_value.trim();
    if trimmed.is_empty() {
        return None;
    }
    let candidate = PathBuf::from(trimmed);
    let normalized = normalize_candidate_path(if candidate.is_absolute() {
        candidate
    } else {
        workspace.join(candidate)
    });
    Some(normalized.to_string_lossy().to_string())
}

#[cfg(windows)]
fn validate_override_path_entry(
    key: &str,
    workspace: &Path,
    layout: &HelperSandboxLayout,
    raw_value: &str,
) -> io::Result<()> {
    let trimmed = raw_value.trim();
    if trimmed.is_empty() {
        return Ok(());
    }
    if is_windows_unc_or_device_path(trimmed) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{key} must not use UNC or device paths in strict helper env"),
        ));
    }
    if path_uses_windows_root_relative_form(trimmed) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{key} must not use root-relative paths in strict helper env"),
        ));
    }
    if is_windows_drive_relative_path(trimmed) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{key} must not use drive-relative paths in strict helper env"),
        ));
    }
    if path_uses_windows_reserved_device_name(Path::new(trimmed)) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{key} must not use Windows reserved device names in strict helper env"),
        ));
    }
    if path_uses_windows_alternate_data_stream(Path::new(trimmed)) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{key} must not use alternate data streams in strict helper env"),
        ));
    }

    let candidate = PathBuf::from(trimmed);
    if !candidate.is_absolute() && relative_path_targets_internal_workspace_root(&candidate) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{key} must not target internal workspace metadata in strict helper env"),
        ));
    }
    let normalized = normalize_candidate_path(if candidate.is_absolute() {
        candidate
    } else {
        workspace.join(candidate)
    });
    if path_targets_internal_workspace_root(workspace, &normalized) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{key} must not target internal workspace metadata in strict helper env"),
        ));
    }
    if path_traverses_workspace_reparse_point(workspace, &normalized)
        || path_traverses_reparse_point_under_root(&layout.root, &normalized)
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "{key} must not traverse workspace or helper-owned reparse points in strict helper env"
            ),
        ));
    }
    if path_is_hardlink_alias_under_root(workspace, &normalized)
        || path_is_hardlink_alias_under_root(&layout.root, &normalized)
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "{key} must not use hardlink aliases under workspace or helper-owned roots in strict helper env"
            ),
        ));
    }
    if normalized.starts_with(&layout.root) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{key} must not target helper-owned roots in strict helper env"),
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn key_matches(key: &str, accepted: &[&str]) -> bool {
    accepted
        .iter()
        .any(|candidate| key.eq_ignore_ascii_case(candidate))
}

fn resolve_sandbox_layout(
    workspace: &Path,
    overrides: &BTreeMap<String, String>,
) -> io::Result<HelperSandboxLayout> {
    if let Some(logical_root) = overrides.get("AI_IDE_SANDBOX_ROOT") {
        if logical_root != "/workspace" {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("AI_IDE_SANDBOX_ROOT must be /workspace, got {logical_root}"),
            ));
        }
    }

    let expected_root_base = std::env::temp_dir().join("ai_ide_strict_helper");
    let home_override = optional_absolute_path(overrides, "HOME")?;
    let temp_override = optional_absolute_path(overrides, "TMPDIR")?;
    let cache_override = optional_absolute_path(overrides, "XDG_CACHE_HOME")?;

    let root = match (&home_override, &temp_override, &cache_override) {
        (Some(home), Some(temp), Some(cache)) => {
            let candidate_root = home.parent().ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "HOME must have a parent directory",
                )
            })?;
            if temp.parent() != Some(candidate_root) || cache.parent() != Some(candidate_root) {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "HOME, TMPDIR, and XDG_CACHE_HOME must share the same helper root",
                ));
            }
            if home.file_name().and_then(|value| value.to_str()) != Some("home")
                || temp.file_name().and_then(|value| value.to_str()) != Some("tmp")
                || cache.file_name().and_then(|value| value.to_str()) != Some("cache")
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "helper environment paths must end in home/tmp/cache",
                ));
            }
            candidate_root.to_path_buf()
        }
        (None, None, None) => expected_root_base.join(stable_workspace_token(workspace)),
        _ => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "HOME, TMPDIR, and XDG_CACHE_HOME must be provided together",
            ));
        }
    };

    if !root.starts_with(&expected_root_base) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "helper environment root must stay under {}",
                expected_root_base.display()
            ),
        ));
    }

    let layout = HelperSandboxLayout {
        root: root.clone(),
        home: root.join("home"),
        temp: root.join("tmp"),
        cache: root.join("cache"),
    };

    if path_traverses_reparse_point_under_root(&expected_root_base, &layout.root)
        || path_traverses_reparse_point_under_root(&expected_root_base, &layout.home)
        || path_traverses_reparse_point_under_root(&expected_root_base, &layout.temp)
        || path_traverses_reparse_point_under_root(&expected_root_base, &layout.cache)
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "helper environment root must not traverse reparse points under {}",
                expected_root_base.display()
            ),
        ));
    }

    Ok(layout)
}

fn build_child_environment_from_base(
    base: &BTreeMap<String, String>,
    overrides: &BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    let mut environment = BTreeMap::new();
    for key in inherited_environment_keys() {
        if let Some(value) = get_case_insensitive(base, key) {
            if let Some(sanitized) = sanitize_inherited_value(key, value) {
                environment.insert((*key).to_string(), sanitized);
            }
        }
    }
    environment.extend(
        overrides
            .iter()
            .map(|(key, value)| (key.clone(), value.clone())),
    );
    apply_derived_variables(&mut environment);
    environment
}

fn get_case_insensitive<'a>(base: &'a BTreeMap<String, String>, key: &str) -> Option<&'a str> {
    base.iter()
        .find(|(candidate, _)| candidate.eq_ignore_ascii_case(key))
        .map(|(_, value)| value.as_str())
}

#[cfg(windows)]
fn inherited_environment_keys() -> &'static [&'static str] {
    &["PATH", "PATHEXT"]
}

#[cfg(not(windows))]
fn inherited_environment_keys() -> &'static [&'static str] {
    &["PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM"]
}

fn apply_derived_variables(environment: &mut BTreeMap<String, String>) {
    #[cfg(windows)]
    {
        environment.insert(
            "NoDefaultCurrentDirectoryInExePath".to_string(),
            "1".to_string(),
        );
        apply_windows_shell_variables(environment);
        if !environment.contains_key("USERPROFILE") {
            if let Some(home) = environment.get("HOME").cloned() {
                environment.insert("USERPROFILE".to_string(), home);
            }
        }
        if let Some(tmpdir) = environment.get("TMPDIR").cloned() {
            environment
                .entry("TEMP".to_string())
                .or_insert_with(|| tmpdir.clone());
            environment.entry("TMP".to_string()).or_insert(tmpdir);
        }
    }
}

#[cfg(windows)]
fn apply_windows_shell_variables(environment: &mut BTreeMap<String, String>) {
    let Some((system_root, comspec)) = default_windows_shell_environment() else {
        return;
    };
    let system_root = system_root.to_string_lossy().to_string();
    let comspec = comspec.to_string_lossy().to_string();
    environment.insert("SystemRoot".to_string(), system_root.clone());
    environment.insert("WINDIR".to_string(), system_root);
    environment.insert("ComSpec".to_string(), comspec);
}

#[cfg(windows)]
fn sanitize_inherited_value(key: &str, value: &str) -> Option<String> {
    if key.eq_ignore_ascii_case("PATH") {
        return sanitize_windows_path_value(value);
    }
    if key.eq_ignore_ascii_case("PATHEXT") {
        return Some(sanitize_windows_pathext_value(value));
    }
    Some(value.to_string())
}

#[cfg(not(windows))]
fn sanitize_inherited_value(_key: &str, value: &str) -> Option<String> {
    Some(value.to_string())
}

#[cfg(windows)]
fn default_windows_shell_environment() -> Option<(PathBuf, PathBuf)> {
    let system_directory = default_windows_system_directory()?;
    let system_root = normalize_env_path_entry(system_directory.parent()?.to_path_buf());
    let comspec = normalize_env_path_entry(system_directory.join("cmd.exe"));
    Some((system_root, comspec))
}

#[cfg(windows)]
fn default_windows_system_directory() -> Option<PathBuf> {
    let mut buffer = vec![0u16; 260];
    let mut written = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) };
    if written == 0 {
        return None;
    }
    if written as usize >= buffer.len() {
        buffer.resize(written as usize + 1, 0);
        written = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) };
        if written == 0 || written as usize >= buffer.len() {
            return None;
        }
    }
    Some(PathBuf::from(OsString::from_wide(
        &buffer[..written as usize],
    )))
}

#[cfg(windows)]
fn sanitize_windows_path_value(value: &str) -> Option<String> {
    let mut sanitized = Vec::<PathBuf>::new();
    for entry in std::env::split_paths(value) {
        if entry.as_os_str().is_empty() || !entry.is_absolute() {
            continue;
        }
        let text = entry.to_string_lossy();
        if is_windows_unc_or_device_path(&text) {
            continue;
        }
        let normalized = normalize_candidate_path(entry.canonicalize().unwrap_or(entry));
        if sanitized.iter().any(|candidate| candidate == &normalized) {
            continue;
        }
        sanitized.push(normalized);
    }
    if sanitized.is_empty() {
        return None;
    }
    std::env::join_paths(sanitized)
        .ok()
        .map(|joined| joined.to_string_lossy().to_string())
}

#[cfg(windows)]
fn sanitize_windows_pathext_value(value: &str) -> String {
    let mut extensions = Vec::<String>::new();
    for item in value.split(';') {
        let trimmed = item.trim();
        if trimmed.is_empty() {
            continue;
        }
        let normalized = trimmed.to_ascii_uppercase();
        if !matches!(normalized.as_str(), ".COM" | ".EXE" | ".BAT" | ".CMD") {
            continue;
        }
        if extensions.iter().any(|candidate| candidate == &normalized) {
            continue;
        }
        extensions.push(normalized);
    }
    if extensions.is_empty() {
        return ".COM;.EXE;.BAT;.CMD".to_string();
    }
    extensions.join(";")
}

#[cfg(windows)]
fn is_windows_unc_or_device_path(token: &str) -> bool {
    token.starts_with("\\\\")
        || token.starts_with("//")
        || token.starts_with(r"\??\")
        || token.starts_with(r"\\?\")
        || token.starts_with(r"\\.\")
}

#[cfg(windows)]
fn is_windows_drive_relative_path(token: &str) -> bool {
    let bytes = token.as_bytes();
    if bytes.len() < 3 || bytes[1] != b':' || !bytes[0].is_ascii_alphabetic() {
        return false;
    }
    let separator = bytes[2];
    separator != b'\\' && separator != b'/'
}

#[cfg(windows)]
fn normalize_env_path_entry(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(stripped) = text.strip_prefix(r"\\?\") {
        return PathBuf::from(stripped);
    }
    if let Some(stripped) = text.strip_prefix(r"\??\") {
        return PathBuf::from(stripped);
    }
    path
}

#[cfg(windows)]
fn normalize_candidate_path(path: PathBuf) -> PathBuf {
    let path = normalize_env_path_entry(path);
    let mut normalized = PathBuf::new();
    let mut normal_segments = Vec::<OsString>::new();
    let mut relative_parents = Vec::<OsString>::new();
    let mut has_root = false;

    for component in path.components() {
        match component {
            Component::Prefix(prefix) => normalized.push(prefix.as_os_str()),
            Component::RootDir => has_root = true,
            Component::CurDir => {}
            Component::ParentDir => {
                if normal_segments.pop().is_none() && !has_root {
                    relative_parents.push(OsString::from(".."));
                }
            }
            Component::Normal(segment) => normal_segments.push(segment.to_os_string()),
        }
    }

    if has_root {
        normalized.push(Path::new(r"\"));
    }
    for segment in relative_parents {
        normalized.push(segment);
    }
    for segment in normal_segments {
        normalized.push(segment);
    }
    normalized
}

fn optional_absolute_path(
    overrides: &BTreeMap<String, String>,
    key: &str,
) -> io::Result<Option<PathBuf>> {
    let Some(raw_value) = overrides.get(key) else {
        return Ok(None);
    };
    let path = PathBuf::from(raw_value);
    if !path.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{key} must be an absolute path"),
        ));
    }
    Ok(Some(path))
}

fn stable_workspace_token(workspace: &Path) -> String {
    let mut value = 0xcbf29ce484222325u64;
    for byte in workspace.to_string_lossy().as_bytes() {
        value ^= u64::from(*byte);
        value = value.wrapping_mul(0x100000001b3);
    }
    format!("{value:016x}")
}

#[cfg(test)]
mod tests {
    use super::{
        build_child_environment_from_base, default_windows_shell_environment,
        normalize_candidate_path, prepare_child_environment, reject_reserved_override_keys,
        resolve_sandbox_layout,
    };
    use crate::filesystem_boundary::helper_mutable_roots;
    use std::collections::BTreeMap;
    use std::path::{Path, PathBuf};
    #[cfg(windows)]
    use std::process::{Command, Stdio};

    #[test]
    #[cfg(windows)]
    fn child_environment_is_allowlisted_and_case_insensitive() {
        let base = BTreeMap::from([
            (
                "Path".to_string(),
                ".;\\\\server\\share;C:\\Windows\\System32;C:\\Windows\\System32".to_string(),
            ),
            ("PATHEXT".to_string(), ".exe;.cmd;.js;.cmd".to_string()),
            ("SystemRoot".to_string(), "C:\\Windows".to_string()),
            ("SECRET_TOKEN".to_string(), "do-not-leak".to_string()),
        ]);
        let overrides = BTreeMap::new();

        let environment = build_child_environment_from_base(&base, &overrides);

        assert_eq!(
            Some("C:\\Windows\\System32"),
            environment.get("PATH").map(String::as_str)
        );
        assert_eq!(
            Some("1"),
            environment
                .get("NoDefaultCurrentDirectoryInExePath")
                .map(String::as_str)
        );
        assert_eq!(
            Some(".EXE;.CMD"),
            environment.get("PATHEXT").map(String::as_str)
        );
        let (system_root, comspec) = default_windows_shell_environment().unwrap();
        assert_eq!(
            Some(system_root.to_string_lossy().as_ref()),
            environment.get("SystemRoot").map(String::as_str)
        );
        assert_eq!(
            Some(system_root.to_string_lossy().as_ref()),
            environment.get("WINDIR").map(String::as_str)
        );
        assert_eq!(
            Some(comspec.to_string_lossy().as_ref()),
            environment.get("ComSpec").map(String::as_str)
        );
        assert!(!environment.contains_key("SECRET_TOKEN"));
    }

    #[test]
    #[cfg(windows)]
    fn child_environment_derives_windows_home_and_temp_variables() {
        let base = BTreeMap::new();
        let overrides = BTreeMap::from([
            ("HOME".to_string(), "C:\\helper\\home".to_string()),
            ("TMPDIR".to_string(), "C:\\helper\\tmp".to_string()),
        ]);

        let environment = build_child_environment_from_base(&base, &overrides);

        assert_eq!(
            Some("C:\\helper\\home"),
            environment.get("USERPROFILE").map(String::as_str)
        );
        assert_eq!(
            Some("C:\\helper\\tmp"),
            environment.get("TEMP").map(String::as_str)
        );
        assert_eq!(
            Some("C:\\helper\\tmp"),
            environment.get("TMP").map(String::as_str)
        );
        assert_eq!(
            Some("1"),
            environment
                .get("NoDefaultCurrentDirectoryInExePath")
                .map(String::as_str)
        );
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_derives_helper_root_when_overrides_are_missing() {
        let workspace = Path::new("C:\\project\\projected");
        let environment = prepare_child_environment(workspace, &BTreeMap::new()).unwrap();

        assert_eq!(
            Some("/workspace"),
            environment.get("AI_IDE_SANDBOX_ROOT").map(String::as_str)
        );
        assert!(
            environment
                .get("HOME")
                .map(|value| value.contains("ai_ide_strict_helper"))
                .unwrap_or(false)
        );
        assert!(
            environment
                .get("TMPDIR")
                .map(|value| value.contains("ai_ide_strict_helper"))
                .unwrap_or(false)
        );
        assert!(
            environment
                .get("XDG_CACHE_HOME")
                .map(|value| value.contains("ai_ide_strict_helper"))
                .unwrap_or(false)
        );
    }

    #[test]
    #[cfg(windows)]
    fn resolve_sandbox_layout_rejects_env_root_outside_helper_temp_base() {
        let workspace = Path::new("C:\\project\\projected");
        let overrides = BTreeMap::from([
            ("HOME".to_string(), "C:\\Users\\Public\\home".to_string()),
            ("TMPDIR".to_string(), "C:\\Users\\Public\\tmp".to_string()),
            (
                "XDG_CACHE_HOME".to_string(),
                "C:\\Users\\Public\\cache".to_string(),
            ),
        ]);

        let error = resolve_sandbox_layout(workspace, &overrides).unwrap_err();

        assert!(
            error
                .to_string()
                .contains("helper environment root must stay under")
        );
    }

    #[test]
    #[cfg(windows)]
    fn resolve_sandbox_layout_rejects_env_root_through_reparse_point() {
        let workspace = Path::new("C:\\project\\projected");
        let expected_root_base = std::env::temp_dir().join("ai_ide_strict_helper");
        let root = expected_root_base.join(format!("junction-layout-{}", std::process::id()));
        let outside =
            expected_root_base.join(format!("junction-layout-outside-{}", std::process::id()));
        std::fs::create_dir_all(&expected_root_base).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        if !create_junction(&root, &outside) {
            let _ = std::fs::remove_dir_all(&outside);
            let _ = std::fs::remove_dir_all(&root);
            return;
        }

        let overrides = BTreeMap::from([
            ("HOME".to_string(), root.join("home").display().to_string()),
            ("TMPDIR".to_string(), root.join("tmp").display().to_string()),
            (
                "XDG_CACHE_HOME".to_string(),
                root.join("cache").display().to_string(),
            ),
        ]);

        let error = resolve_sandbox_layout(workspace, &overrides).unwrap_err();

        assert!(error.to_string().contains("reparse points"));
        let _ = std::fs::remove_dir_all(&root);
        let _ = std::fs::remove_dir_all(&outside);
    }

    #[test]
    #[cfg(windows)]
    fn reserved_windows_path_overrides_are_rejected_case_insensitively() {
        let error = reject_reserved_override_keys(&BTreeMap::from([(
            "path".to_string(),
            "C:\\Users\\Public".to_string(),
        )]))
        .unwrap_err();

        assert!(error.to_string().contains("must not be overridden"));
    }

    #[test]
    #[cfg(windows)]
    fn reserved_windows_derived_overrides_are_rejected() {
        let error = prepare_child_environment(
            Path::new("C:\\project\\projected"),
            &BTreeMap::from([("USERPROFILE".to_string(), "C:\\Users\\Public".to_string())]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("must not be overridden"));
    }

    #[test]
    #[cfg(windows)]
    fn reserved_windows_current_directory_search_override_is_rejected() {
        let error = prepare_child_environment(
            Path::new("C:\\project\\projected"),
            &BTreeMap::from([(
                "NoDefaultCurrentDirectoryInExePath".to_string(),
                "0".to_string(),
            )]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("must not be overridden"));
    }

    #[test]
    #[cfg(windows)]
    fn child_environment_sanitizes_pathext_to_standard_executable_extensions() {
        let base = BTreeMap::from([(
            "PATHEXT".to_string(),
            ".JS;.EXE;.BAT;.VBS;.CMD;.EXE".to_string(),
        )]);

        let environment = build_child_environment_from_base(&base, &BTreeMap::new());

        assert_eq!(
            Some(".EXE;.BAT;.CMD"),
            environment.get("PATHEXT").map(String::as_str)
        );
    }

    #[test]
    #[cfg(windows)]
    fn child_environment_ignores_inherited_shell_path_variables_and_derives_local_defaults() {
        let base = BTreeMap::from([
            (
                "SystemRoot".to_string(),
                r"\\server\share\Windows".to_string(),
            ),
            ("WINDIR".to_string(), r"\\server\share\Windows".to_string()),
            ("ComSpec".to_string(), r"\\server\share\cmd.exe".to_string()),
        ]);

        let environment = build_child_environment_from_base(&base, &BTreeMap::new());
        let (system_root, comspec) = default_windows_shell_environment().unwrap();

        assert_eq!(
            Some(system_root.to_string_lossy().as_ref()),
            environment.get("SystemRoot").map(String::as_str)
        );
        assert_eq!(
            Some(system_root.to_string_lossy().as_ref()),
            environment.get("WINDIR").map(String::as_str)
        );
        assert_eq!(
            Some(comspec.to_string_lossy().as_ref()),
            environment.get("ComSpec").map(String::as_str)
        );
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_allows_path_like_override_outside_workspace() {
        let workspace = Path::new(r"C:\project\projected");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("projected");
        let environment = prepare_child_environment(
            workspace,
            &BTreeMap::from([
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
                (
                    "PYTHONPATH".to_string(),
                    r"C:\Users\Public\outside".to_string(),
                ),
            ]),
        )
        .unwrap();

        assert_eq!(
            Some(r"C:\Users\Public\outside"),
            environment.get("PYTHONPATH").map(String::as_str)
        );
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_allows_workspace_relative_path_like_override() {
        let workspace = std::env::temp_dir().join("helper-env-workspace");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace");
        let environment = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
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
                ("PYTHONPATH".to_string(), r"src;tests".to_string()),
            ]),
        )
        .unwrap();

        let expected = format!(
            "{};{}",
            normalize_candidate_path(workspace.join("src")).display(),
            normalize_candidate_path(workspace.join("tests")).display()
        );
        assert_eq!(
            Some(expected.as_str()),
            environment.get("PYTHONPATH").map(String::as_str)
        );
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_rejects_helper_root_path_like_override() {
        let workspace = std::env::temp_dir().join("helper-env-workspace-helper-root-data");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace-helper-root-data");
        let error = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
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
                (
                    "PYTHONPATH".to_string(),
                    helper_root.join("cache").join("libs").display().to_string(),
                ),
            ]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("helper-owned roots"));
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_allows_system_root_path_like_override() {
        let workspace = std::env::temp_dir().join("helper-env-workspace-system-root-data");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace-system-root-data");
        let (system_root, _) = default_windows_shell_environment().unwrap();
        let environment = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
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
                (
                    "PYTHONPATH".to_string(),
                    system_root.join("System32").display().to_string(),
                ),
            ]),
        )
        .unwrap();

        assert_eq!(
            Some(system_root.join("System32").to_string_lossy().as_ref()),
            environment.get("PYTHONPATH").map(String::as_str)
        );
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_rejects_path_like_override_into_internal_workspace_root() {
        let workspace = std::env::temp_dir().join("helper-env-workspace-internal");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace-internal");
        let error = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
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
                (
                    "PYTHONPATH".to_string(),
                    r".ai_ide_runtime\hidden".to_string(),
                ),
            ]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("internal workspace metadata"));
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_rejects_path_like_override_using_alternate_data_stream() {
        let workspace = std::env::temp_dir().join("helper-env-workspace-ads");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace-ads");
        let error = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
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
                ("PYTHONPATH".to_string(), r".\src\lib.py:secret".to_string()),
            ]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("alternate data streams"));
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_rejects_path_like_override_using_reserved_device_name() {
        let workspace = std::env::temp_dir().join("helper-env-workspace-reserved-device");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace-reserved-device");
        let error = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
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
                ("PYTHONPATH".to_string(), r"NUL.txt".to_string()),
            ]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("Windows reserved device names"));
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_rejects_path_like_override_using_root_relative_path() {
        let workspace = std::env::temp_dir().join("helper-env-workspace-root-relative");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace-root-relative");
        let error = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
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
                (
                    "PYTHONPATH".to_string(),
                    r"\Users\Public\outside".to_string(),
                ),
            ]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("root-relative paths"));
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_rejects_path_like_override_using_forward_slash_root_relative_path()
    {
        let workspace =
            std::env::temp_dir().join("helper-env-workspace-root-relative-forward-slash");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace-root-relative-forward-slash");
        let error = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
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
                (
                    "PYTHONPATH".to_string(),
                    "/Users/Public/outside".to_string(),
                ),
            ]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("root-relative paths"));
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_rejects_path_like_override_through_helper_reparse_point() {
        let workspace = std::env::temp_dir().join("helper-env-workspace-helper-reparse");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace-helper-reparse");
        let home = helper_root.join("home");
        let outside = helper_root.join("outside");
        let link = home.join("linked");
        std::fs::create_dir_all(&home).unwrap();
        std::fs::create_dir_all(&helper_root.join("tmp")).unwrap();
        std::fs::create_dir_all(&helper_root.join("cache")).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        if !create_junction(&link, &outside) {
            let _ = std::fs::remove_dir_all(&helper_root);
            return;
        }

        let error = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
                ("HOME".to_string(), home.display().to_string()),
                (
                    "TMPDIR".to_string(),
                    helper_root.join("tmp").display().to_string(),
                ),
                (
                    "XDG_CACHE_HOME".to_string(),
                    helper_root.join("cache").display().to_string(),
                ),
                ("PYTHONPATH".to_string(), link.display().to_string()),
            ]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("reparse points"));
        let _ = std::fs::remove_dir_all(&helper_root);
    }

    #[test]
    #[cfg(windows)]
    fn prepare_child_environment_rejects_path_like_override_through_workspace_hardlink() {
        let workspace = std::env::temp_dir().join("helper-env-workspace-hardlink");
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-workspace-hardlink");
        let outside = std::env::temp_dir().join("helper-env-workspace-hardlink-outside");
        std::fs::create_dir_all(workspace.join("src")).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        let target = outside.join("lib.py");
        let alias = workspace.join("src").join("linked.py");
        std::fs::write(&target, b"print('alias')\n").unwrap();
        std::fs::hard_link(&target, &alias).unwrap();

        let error = prepare_child_environment(
            &workspace,
            &BTreeMap::from([
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
                ("PYTHONPATH".to_string(), alias.display().to_string()),
            ]),
        )
        .unwrap_err();

        assert!(error.to_string().contains("hardlink aliases"));
        let _ = std::fs::remove_dir_all(&workspace);
        let _ = std::fs::remove_dir_all(&outside);
        let _ = std::fs::remove_dir_all(&helper_root);
    }

    #[test]
    #[cfg(windows)]
    fn boundary_seam_reports_helper_mutable_roots() {
        let helper_root = std::env::temp_dir()
            .join("ai_ide_strict_helper")
            .join("helper-env-boundary-roots");
        let home = helper_root.join("home");
        let temp = helper_root.join("tmp");
        let cache = helper_root.join("cache");
        std::fs::create_dir_all(&home).unwrap();
        std::fs::create_dir_all(&temp).unwrap();
        std::fs::create_dir_all(&cache).unwrap();

        let roots = helper_mutable_roots(&BTreeMap::from([
            ("HOME".to_string(), home.display().to_string()),
            ("TMPDIR".to_string(), temp.display().to_string()),
            ("XDG_CACHE_HOME".to_string(), cache.display().to_string()),
        ]));

        assert_eq!(3, roots.len());
        assert!(roots.iter().any(|root: &PathBuf| root.ends_with("home")));
        assert!(roots.iter().any(|root: &PathBuf| root.ends_with("tmp")));
        assert!(roots.iter().any(|root: &PathBuf| root.ends_with("cache")));
        let _ = std::fs::remove_dir_all(&helper_root);
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
}
