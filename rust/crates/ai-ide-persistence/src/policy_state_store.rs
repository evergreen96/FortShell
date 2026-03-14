use std::error::Error;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use ai_ide_protocol::PolicyState;

use crate::file_lock::FileLockGuard;
use crate::path_utils::normalize_absolute_path;
use crate::replace::replace_file;
use crate::{POLICY_STATE_FILENAME, PROJECT_METADATA_DIR_NAME};

#[derive(Debug)]
pub enum StoreError {
    Io(io::Error),
    Parse(serde_json::Error),
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StoreError::Io(error) => write!(f, "{error}"),
            StoreError::Parse(error) => write!(f, "{error}"),
        }
    }
}

impl Error for StoreError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            StoreError::Io(error) => Some(error),
            StoreError::Parse(error) => Some(error),
        }
    }
}

impl From<io::Error> for StoreError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

impl From<serde_json::Error> for StoreError {
    fn from(value: serde_json::Error) -> Self {
        Self::Parse(value)
    }
}

pub struct PolicyStateStore {
    root: PathBuf,
    path: PathBuf,
    lock_path: PathBuf,
}

impl PolicyStateStore {
    pub fn new(root: impl AsRef<Path>) -> Self {
        let root = normalize_absolute_path(root.as_ref());
        let path = default_policy_state_path(&root);
        let lock_path = lock_path_for(&path);
        Self {
            root,
            path,
            lock_path,
        }
    }

    pub fn with_path(root: impl AsRef<Path>, path: impl AsRef<Path>) -> Self {
        let root = normalize_absolute_path(root.as_ref());
        let path = normalize_absolute_path(path.as_ref());
        let lock_path = lock_path_for(&path);
        Self {
            root,
            path,
            lock_path,
        }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn load(&self) -> Result<PolicyState, StoreError> {
        if !self.path.exists() {
            return Ok(PolicyState::default());
        }

        let _lock = FileLockGuard::acquire(&self.lock_path)?;
        if !self.path.exists() {
            return Ok(PolicyState::default());
        }

        let text = fs::read_to_string(&self.path)?;
        let payload: PolicyState = serde_json::from_str(&text)?;
        Ok(PolicyState {
            deny_globs: payload.deny_globs,
            version: payload.version.max(1),
        })
    }

    pub fn save(&self, state: &PolicyState) -> Result<(), StoreError> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }

        let _lock = FileLockGuard::acquire(&self.lock_path)?;
        let temp_path = temporary_path_for(&self.path);
        let payload = PolicyState {
            deny_globs: state.deny_globs.clone(),
            version: state.version.max(1),
        };
        let serialized = serde_json::to_string_pretty(&payload)?;
        fs::write(&temp_path, format!("{serialized}\n"))?;
        replace_file(&temp_path, &self.path)?;
        Ok(())
    }
}

pub fn default_policy_state_path(root: &Path) -> PathBuf {
    normalize_absolute_path(root)
        .join(PROJECT_METADATA_DIR_NAME)
        .join(POLICY_STATE_FILENAME)
}

fn lock_path_for(path: &Path) -> PathBuf {
    let mut value = path.as_os_str().to_os_string();
    value.push(".lock");
    PathBuf::from(value)
}

fn temporary_path_for(path: &Path) -> PathBuf {
    let mut value = path.as_os_str().to_os_string();
    value.push(".tmp");
    PathBuf::from(value)
}
