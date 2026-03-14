use std::error::Error;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use ai_ide_persistence::{
    PathGuardError, StoreError, WorkspaceIndexStateStore, ensure_no_hardlink_alias,
    ensure_no_symlink_components, normalize_absolute_path,
};
use ai_ide_policy::PolicyEngine;
use ai_ide_protocol::{
    WorkspaceCatalogEntry, WorkspaceIndexEntry, WorkspaceIndexSnapshot, WorkspaceSearchMatch,
};

const INTERNAL_ROOT_DIR_NAMES: [&str; 2] = [".ai_ide_runtime", ".ai-ide"];

#[derive(Debug)]
pub enum WorkspaceError {
    PathEscapesRoot { target: String },
    InternalPath { target: String },
    BlockedByPolicy { target: String },
    SymlinkPath { target: String },
    HardlinkPath { target: String },
    DirectoryNotFound { target: String },
    Io(io::Error),
}

impl fmt::Display for WorkspaceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            WorkspaceError::PathEscapesRoot { target } => {
                write!(f, "Outside active workspace root: {target}")
            }
            WorkspaceError::InternalPath { target } => {
                write!(f, "Blocked internal path: {target}")
            }
            WorkspaceError::BlockedByPolicy { target } => {
                write!(f, "Blocked by policy: {target}")
            }
            WorkspaceError::SymlinkPath { target } => {
                write!(f, "Blocked symlink path: {target}")
            }
            WorkspaceError::HardlinkPath { target } => {
                write!(f, "Blocked hardlink path: {target}")
            }
            WorkspaceError::DirectoryNotFound { target } => {
                write!(f, "Directory not found: {target}")
            }
            WorkspaceError::Io(error) => error.fmt(f),
        }
    }
}

impl Error for WorkspaceError {}

impl From<io::Error> for WorkspaceError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

impl From<PathGuardError> for WorkspaceError {
    fn from(value: PathGuardError) -> Self {
        match value {
            PathGuardError::SymlinkPath { target } => Self::SymlinkPath { target },
            PathGuardError::HardlinkPath { target } => Self::HardlinkPath { target },
        }
    }
}

#[derive(Debug)]
pub enum WorkspaceIndexError {
    Workspace(WorkspaceError),
    Store(StoreError),
}

impl fmt::Display for WorkspaceIndexError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            WorkspaceIndexError::Workspace(error) => error.fmt(f),
            WorkspaceIndexError::Store(error) => error.fmt(f),
        }
    }
}

impl Error for WorkspaceIndexError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            WorkspaceIndexError::Workspace(error) => Some(error),
            WorkspaceIndexError::Store(error) => Some(error),
        }
    }
}

impl From<WorkspaceError> for WorkspaceIndexError {
    fn from(value: WorkspaceError) -> Self {
        Self::Workspace(value)
    }
}

impl From<StoreError> for WorkspaceIndexError {
    fn from(value: StoreError) -> Self {
        Self::Store(value)
    }
}

pub struct WorkspaceCatalog {
    root: PathBuf,
}

impl WorkspaceCatalog {
    pub fn new(root: impl AsRef<Path>) -> Self {
        Self {
            root: normalize_absolute_path(root.as_ref()),
        }
    }

    pub fn list_dir(
        &self,
        policy: &PolicyEngine,
        target: &str,
    ) -> Result<Vec<WorkspaceCatalogEntry>, WorkspaceError> {
        let directory = self.resolve_directory(policy, target)?;
        let mut entries = Vec::new();
        for path in self.visible_children(policy, &directory)? {
            entries.push(self.catalog_entry(&path)?);
        }
        Ok(entries)
    }

    pub fn tree(
        &self,
        policy: &PolicyEngine,
        target: &str,
    ) -> Result<Vec<WorkspaceCatalogEntry>, WorkspaceError> {
        let directory = self.resolve_directory(policy, target)?;
        let mut entries = Vec::new();
        self.collect_visible_descendants(policy, &directory, false, &mut entries)?;
        Ok(entries)
    }

    pub fn grep(
        &self,
        policy: &PolicyEngine,
        pattern: &str,
        target: &str,
    ) -> Result<Vec<WorkspaceSearchMatch>, WorkspaceError> {
        let directory = self.resolve_directory(policy, target)?;
        let mut files = Vec::new();
        self.collect_visible_paths(policy, &directory, true, &mut files)?;

        let mut matches = Vec::new();
        for path in files {
            let content = match fs::read_to_string(&path) {
                Ok(content) => content,
                Err(error) if error.kind() == io::ErrorKind::InvalidData => continue,
                Err(error) => return Err(WorkspaceError::Io(error)),
            };
            let relative = self.relative_path(&path)?;
            for (index, line) in content.lines().enumerate() {
                if line.contains(pattern) {
                    matches.push(WorkspaceSearchMatch {
                        path: relative.clone(),
                        line_number: (index + 1) as u64,
                        line_text: line.trim().to_owned(),
                    });
                }
            }
        }
        Ok(matches)
    }

    fn resolve_directory(
        &self,
        policy: &PolicyEngine,
        target: &str,
    ) -> Result<PathBuf, WorkspaceError> {
        let directory = self.resolve_allowed_path(policy, target)?;
        if !directory.exists() || !directory.is_dir() {
            return Err(WorkspaceError::DirectoryNotFound {
                target: target.to_owned(),
            });
        }
        Ok(directory)
    }

    fn resolve_allowed_path(
        &self,
        policy: &PolicyEngine,
        target: &str,
    ) -> Result<PathBuf, WorkspaceError> {
        let raw_candidate = self.candidate_path(Path::new(target));
        self.ensure_alias_safe(&raw_candidate)?;
        match self.resolve_under_root_with_target(&raw_candidate, target) {
            Ok(candidate) => {
                let relative = self.relative_path(&candidate)?;
                if self.is_internal_relative(&relative) {
                    return Err(WorkspaceError::InternalPath { target: relative });
                }
                if !policy.is_allowed(&candidate) {
                    return Err(WorkspaceError::BlockedByPolicy { target: relative });
                }
                Ok(candidate)
            }
            Err(WorkspaceError::PathEscapesRoot { .. }) => {
                Ok(normalize_absolute_path(&raw_candidate))
            }
            Err(other) => Err(other),
        }
    }

    fn visible_children(
        &self,
        policy: &PolicyEngine,
        directory: &Path,
    ) -> Result<Vec<PathBuf>, WorkspaceError> {
        let mut entries = fs::read_dir(directory)?
            .map(|entry| entry.map(|item| item.path()))
            .collect::<Result<Vec<_>, io::Error>>()?;
        entries.sort_by(|left, right| sort_key(left).cmp(&sort_key(right)));

        let mut visible = Vec::new();
        for entry in entries {
            if self.is_visible(policy, &entry)? {
                visible.push(entry);
            }
        }
        Ok(visible)
    }

    fn collect_visible_descendants(
        &self,
        policy: &PolicyEngine,
        directory: &Path,
        files_only: bool,
        results: &mut Vec<WorkspaceCatalogEntry>,
    ) -> Result<(), WorkspaceError> {
        let mut paths = Vec::new();
        self.collect_visible_paths(policy, directory, files_only, &mut paths)?;
        for path in paths {
            results.push(self.catalog_entry(&path)?);
        }
        Ok(())
    }

    fn collect_visible_paths(
        &self,
        policy: &PolicyEngine,
        directory: &Path,
        files_only: bool,
        results: &mut Vec<PathBuf>,
    ) -> Result<(), WorkspaceError> {
        for entry in self.visible_children(policy, directory)? {
            if entry.is_dir() {
                if !files_only {
                    results.push(entry.clone());
                }
                self.collect_visible_paths(policy, &entry, files_only, results)?;
                continue;
            }
            results.push(entry);
        }
        Ok(())
    }

    fn is_visible(&self, policy: &PolicyEngine, path: &Path) -> Result<bool, WorkspaceError> {
        if let Err(error) = self.ensure_alias_safe(path) {
            return match error {
                WorkspaceError::SymlinkPath { .. } | WorkspaceError::HardlinkPath { .. } => {
                    Ok(false)
                }
                other => Err(other),
            };
        }
        let candidate = match self.resolve_under_root(path) {
            Ok(path) => path,
            Err(WorkspaceError::PathEscapesRoot { .. }) => return Ok(false),
            Err(error) => return Err(error),
        };
        let relative = self.relative_path(&candidate)?;
        if self.is_internal_relative(&relative) {
            return Ok(false);
        }
        Ok(policy.is_allowed(&candidate))
    }

    fn catalog_entry(&self, path: &Path) -> Result<WorkspaceCatalogEntry, WorkspaceError> {
        let relative = self.relative_path(path)?;
        let name = Path::new(&relative)
            .file_name()
            .map(|name| name.to_string_lossy().to_string())
            .unwrap_or_else(|| relative.clone());
        let is_dir = path.is_dir();
        Ok(WorkspaceCatalogEntry {
            path: relative.clone(),
            name: name.clone(),
            is_dir,
            display_name: if is_dir { format!("{name}/") } else { name },
            display_path: if is_dir {
                format!("{relative}/")
            } else {
                relative
            },
        })
    }

    fn relative_path(&self, path: &Path) -> Result<String, WorkspaceError> {
        let candidate = match self.resolve_under_root(path) {
            Ok(resolved) => resolved,
            Err(WorkspaceError::PathEscapesRoot { .. }) => {
                return Ok(normalize_absolute_path(path)
                    .to_string_lossy()
                    .replace('\\', "/"));
            }
            Err(other) => return Err(other),
        };
        let relative =
            candidate
                .strip_prefix(&self.root)
                .map_err(|_| WorkspaceError::PathEscapesRoot {
                    target: candidate.display().to_string(),
                })?;
        let text = relative.to_string_lossy().replace('\\', "/");
        if text.is_empty() {
            Ok(".".to_owned())
        } else {
            Ok(text)
        }
    }

    fn resolve_under_root(&self, path: &Path) -> Result<PathBuf, WorkspaceError> {
        self.resolve_under_root_with_target(path, &path.display().to_string())
    }

    fn resolve_under_root_with_target(
        &self,
        path: &Path,
        target: &str,
    ) -> Result<PathBuf, WorkspaceError> {
        let candidate = if path.is_absolute() {
            normalize_absolute_path(path)
        } else {
            normalize_absolute_path(&self.root.join(path))
        };
        if candidate.strip_prefix(&self.root).is_err() {
            return Err(WorkspaceError::PathEscapesRoot {
                target: target.to_owned(),
            });
        }
        Ok(candidate)
    }

    fn is_internal_relative(&self, relative: &str) -> bool {
        let Some(first) = relative.split('/').next() else {
            return false;
        };
        INTERNAL_ROOT_DIR_NAMES.contains(&first)
    }

    fn candidate_path(&self, path: &Path) -> PathBuf {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.root.join(path)
        }
    }

    fn ensure_alias_safe(&self, path: &Path) -> Result<(), WorkspaceError> {
        ensure_no_symlink_components(&self.root, path)?;
        ensure_no_hardlink_alias(&self.root, path)?;
        Ok(())
    }
}

pub struct WorkspaceIndexService {
    workspace: WorkspaceCatalog,
    store: WorkspaceIndexStateStore,
}

impl WorkspaceIndexService {
    pub fn with_store_path(root: impl AsRef<Path>, store_path: impl AsRef<Path>) -> Self {
        Self {
            workspace: WorkspaceCatalog::new(root),
            store: WorkspaceIndexStateStore::with_path(store_path),
        }
    }

    pub fn load(&self) -> Result<WorkspaceIndexSnapshot, WorkspaceIndexError> {
        self.store.load().map_err(WorkspaceIndexError::from)
    }

    pub fn refresh(
        &self,
        policy: &PolicyEngine,
    ) -> Result<WorkspaceIndexSnapshot, WorkspaceIndexError> {
        let mut paths = Vec::new();
        self.workspace
            .collect_visible_paths(policy, &self.workspace.root, false, &mut paths)?;
        let entries = paths
            .into_iter()
            .map(|path| self.index_entry(&path))
            .collect::<Result<Vec<_>, WorkspaceIndexError>>()?;
        let snapshot = WorkspaceIndexSnapshot {
            policy_version: policy.state().version,
            entries,
        };
        self.store.save(&snapshot)?;
        Ok(snapshot)
    }

    fn index_entry(&self, path: &Path) -> Result<WorkspaceIndexEntry, WorkspaceIndexError> {
        let metadata = fs::metadata(path)
            .map_err(WorkspaceError::from)
            .map_err(WorkspaceIndexError::from)?;
        Ok(WorkspaceIndexEntry {
            path: self.workspace.relative_path(path)?,
            is_dir: metadata.is_dir(),
            size: if metadata.is_dir() { 0 } else { metadata.len() },
            modified_ns: modified_ns(&metadata),
        })
    }
}

fn sort_key(path: &Path) -> (String, String) {
    let name = path
        .file_name()
        .map(|value| value.to_string_lossy().to_string())
        .unwrap_or_default();
    (name.to_lowercase(), name)
}

fn modified_ns(metadata: &fs::Metadata) -> u64 {
    metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos().min(u64::MAX as u128) as u64)
        .unwrap_or(0)
}
