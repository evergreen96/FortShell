#[cfg(windows)]
use std::os::windows::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};

const INTERNAL_WORKSPACE_ROOT_NAMES: &[&str] = &[".ai_ide_runtime", ".ai-ide"];
#[cfg(windows)]
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;
const RESERVED_WINDOWS_DEVICE_NAMES: &[&str] = &[
    "con", "prn", "aux", "nul", "conin$", "conout$", "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8",
    "lpt9",
];

pub fn path_uses_windows_alternate_data_stream(candidate: &Path) -> bool {
    normalize_path(candidate)
        .components()
        .any(|component| match component {
            Component::Normal(segment) => segment.to_string_lossy().contains(':'),
            _ => false,
        })
}

pub fn path_uses_windows_root_relative_form(candidate: &str) -> bool {
    (candidate.starts_with('\\')
        && !candidate.starts_with("\\\\")
        && !candidate.starts_with(r"\??\")
        && !candidate.starts_with(r"\\?\")
        && !candidate.starts_with(r"\\.\"))
        || (candidate.starts_with('/')
            && !candidate.starts_with("//")
            && candidate[1..].chars().any(|ch| ch == '/' || ch == '\\'))
}

pub fn path_uses_windows_reserved_device_name(candidate: &Path) -> bool {
    normalize_path(candidate)
        .components()
        .any(|component| match component {
            Component::Normal(segment) => {
                let normalized = normalize_windows_policy_name(&segment.to_string_lossy());
                let stem = normalized.split('.').next().unwrap_or("");
                RESERVED_WINDOWS_DEVICE_NAMES.contains(&stem)
            }
            _ => false,
        })
}

pub fn relative_path_targets_internal_workspace_root(candidate: &Path) -> bool {
    let normalized = normalize_path(candidate);
    let Some(Component::Normal(first)) = normalized.components().next() else {
        return false;
    };
    let Some(name) = first.to_str() else {
        return false;
    };
    INTERNAL_WORKSPACE_ROOT_NAMES
        .iter()
        .any(|candidate| normalize_windows_policy_name(name) == *candidate)
}

pub fn path_targets_internal_workspace_root(workspace: &Path, candidate: &Path) -> bool {
    let workspace_components = normalized_component_strings(&workspace);
    let candidate_components = normalized_component_strings(&candidate);
    if candidate_components.len() <= workspace_components.len()
        || candidate_components[..workspace_components.len()] != workspace_components
    {
        return false;
    }
    let name = &candidate_components[workspace_components.len()];
    INTERNAL_WORKSPACE_ROOT_NAMES
        .iter()
        .any(|candidate| name.eq_ignore_ascii_case(candidate))
}

#[cfg(windows)]
pub fn path_traverses_workspace_reparse_point(workspace: &Path, candidate: &Path) -> bool {
    path_traverses_reparse_point_under_root(workspace, candidate)
}

#[cfg(not(windows))]
pub fn path_traverses_workspace_reparse_point(_workspace: &Path, _candidate: &Path) -> bool {
    false
}

#[cfg(windows)]
pub fn path_traverses_reparse_point_under_root(root: &Path, candidate: &Path) -> bool {
    let Ok(relative) = candidate.strip_prefix(root) else {
        return false;
    };

    let mut current = root.to_path_buf();
    if is_reparse_point(&current) {
        return true;
    }
    for component in relative.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => return true,
            Component::Normal(segment) => current.push(segment),
            _ => return true,
        }
        if is_reparse_point(&current) {
            return true;
        }
        if !current.exists() {
            break;
        }
    }
    false
}

#[cfg(not(windows))]
pub fn path_traverses_reparse_point_under_root(_root: &Path, _candidate: &Path) -> bool {
    false
}

#[cfg(windows)]
fn is_reparse_point(path: &Path) -> bool {
    std::fs::symlink_metadata(path)
        .map(|metadata| metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0)
        .unwrap_or(false)
}

fn normalize_path(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();
    let mut normal_segments = Vec::new();
    let mut relative_parents = Vec::new();
    let mut has_root = false;

    for component in path.components() {
        match component {
            Component::Prefix(prefix) => normalized.push(prefix.as_os_str()),
            Component::RootDir => has_root = true,
            Component::CurDir => {}
            Component::ParentDir => {
                if normal_segments.pop().is_none() && !has_root {
                    relative_parents.push("..".to_string());
                }
            }
            Component::Normal(segment) => normal_segments.push(segment.to_os_string()),
        }
    }

    if has_root {
        normalized.push(Path::new(std::path::MAIN_SEPARATOR_STR));
    }
    for segment in relative_parents {
        normalized.push(segment);
    }
    for segment in normal_segments {
        normalized.push(segment);
    }
    normalized
}

fn normalized_component_strings(path: &Path) -> Vec<String> {
    normalize_path(path)
        .components()
        .filter_map(|component| match component {
            Component::Prefix(prefix) => {
                Some(prefix.as_os_str().to_string_lossy().to_ascii_lowercase())
            }
            Component::RootDir => Some(String::from(std::path::MAIN_SEPARATOR_STR)),
            Component::CurDir => None,
            Component::ParentDir => Some("..".to_string()),
            Component::Normal(segment) => {
                Some(normalize_windows_policy_name(&segment.to_string_lossy()))
            }
        })
        .collect()
}

fn normalize_windows_policy_name(name: &str) -> String {
    name.trim_end_matches(['.', ' ']).to_ascii_lowercase()
}

#[cfg(test)]
mod tests {
    use super::{
        path_targets_internal_workspace_root, path_traverses_reparse_point_under_root,
        path_traverses_workspace_reparse_point, path_uses_windows_alternate_data_stream,
        path_uses_windows_reserved_device_name, path_uses_windows_root_relative_form,
        relative_path_targets_internal_workspace_root,
    };
    use std::path::{Path, PathBuf};
    #[cfg(windows)]
    use std::process::{Command, Stdio};
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn detects_internal_workspace_root_for_absolute_path() {
        assert!(path_targets_internal_workspace_root(
            Path::new(r"C:\workspace"),
            Path::new(r"C:\workspace\.ai_ide_runtime\file.txt"),
        ));
    }

    #[test]
    fn ignores_regular_workspace_paths() {
        assert!(!path_targets_internal_workspace_root(
            Path::new(r"C:\workspace"),
            Path::new(r"C:\workspace\src\main.py"),
        ));
    }

    #[test]
    fn detects_internal_workspace_root_for_relative_path() {
        assert!(relative_path_targets_internal_workspace_root(Path::new(
            r".ai_ide_runtime\file.txt"
        )));
    }

    #[test]
    fn detects_internal_workspace_root_when_workspace_exists_but_candidate_does_not() {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let workspace = std::env::temp_dir().join(format!(
            "ai-ide-windows-helper-path-policy-{}-{id}",
            std::process::id()
        ));
        std::fs::create_dir_all(&workspace).unwrap();
        let candidate = workspace.join(PathBuf::from(".ai_ide_runtime").join("missing.txt"));
        assert!(path_targets_internal_workspace_root(&workspace, &candidate));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn detects_windows_alternate_data_stream_path() {
        assert!(path_uses_windows_alternate_data_stream(Path::new(
            r"C:\workspace\file.txt:secret"
        )));
        assert!(path_uses_windows_alternate_data_stream(Path::new(
            r"scripts\runner.py:payload"
        )));
        assert!(!path_uses_windows_alternate_data_stream(Path::new(
            r"C:\workspace\file.txt"
        )));
    }

    #[test]
    fn detects_windows_root_relative_form() {
        assert!(path_uses_windows_root_relative_form(
            r"\Windows\System32\cmd.exe"
        ));
        assert!(path_uses_windows_root_relative_form(r"\Users\Public"));
        assert!(path_uses_windows_root_relative_form(
            "/Windows/System32/cmd.exe"
        ));
        assert!(path_uses_windows_root_relative_form(
            "/Users/Public/secret.txt"
        ));
        assert!(!path_uses_windows_root_relative_form(
            r"\\server\share\cmd.exe"
        ));
        assert!(!path_uses_windows_root_relative_form(
            "//server/share/cmd.exe"
        ));
        assert!(!path_uses_windows_root_relative_form("/c"));
        assert!(!path_uses_windows_root_relative_form(
            r"\??\C:\Windows\System32\cmd.exe"
        ));
        assert!(!path_uses_windows_root_relative_form(
            r"C:\Windows\System32\cmd.exe"
        ));
    }

    #[test]
    fn detects_windows_reserved_device_name_path() {
        assert!(path_uses_windows_reserved_device_name(Path::new("NUL")));
        assert!(path_uses_windows_reserved_device_name(Path::new("nul.txt")));
        assert!(path_uses_windows_reserved_device_name(Path::new(
            r"logs\COM1.trace"
        )));
        assert!(!path_uses_windows_reserved_device_name(Path::new(
            "notes.txt"
        )));
    }

    #[test]
    fn detects_internal_workspace_root_with_trailing_dot_or_space() {
        assert!(relative_path_targets_internal_workspace_root(Path::new(
            r".ai_ide_runtime.\file.txt"
        )));
        assert!(relative_path_targets_internal_workspace_root(Path::new(
            ".ai-ide \\event.log"
        )));
        assert!(path_targets_internal_workspace_root(
            Path::new(r"C:\workspace"),
            Path::new(r"C:\workspace\.ai_ide_runtime. \file.txt"),
        ));
    }

    #[test]
    #[cfg(windows)]
    fn detects_workspace_reparse_point_junction() {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let base = std::env::temp_dir().join(format!(
            "ai-ide-windows-helper-path-junction-{}-{id}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let outside = base.join("outside");
        let junction = workspace.join("linked");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        if !create_junction(&junction, &outside) {
            let _ = std::fs::remove_dir_all(&base);
            return;
        }

        assert!(path_traverses_workspace_reparse_point(
            &workspace,
            &junction.join("script.py")
        ));
        assert!(path_traverses_reparse_point_under_root(
            &workspace,
            &junction.join("script.py")
        ));
        let _ = std::fs::remove_dir_all(&base);
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
