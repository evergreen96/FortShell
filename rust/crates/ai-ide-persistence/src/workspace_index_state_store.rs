use std::fs;
use std::path::{Path, PathBuf};

use ai_ide_protocol::WorkspaceIndexSnapshot;

use crate::StoreError;
use crate::file_lock::FileLockGuard;
use crate::replace::replace_file;

pub struct WorkspaceIndexStateStore {
    path: PathBuf,
    lock_path: PathBuf,
}

impl WorkspaceIndexStateStore {
    pub fn with_path(path: impl AsRef<Path>) -> Self {
        let path = path.as_ref().to_path_buf();
        let lock_path = lock_path_for(&path);
        Self { path, lock_path }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn load(&self) -> Result<WorkspaceIndexSnapshot, StoreError> {
        if !self.path.exists() {
            return Ok(WorkspaceIndexSnapshot {
                policy_version: 0,
                entries: Vec::new(),
            });
        }

        let _lock = FileLockGuard::acquire(&self.lock_path)?;
        if !self.path.exists() {
            return Ok(WorkspaceIndexSnapshot {
                policy_version: 0,
                entries: Vec::new(),
            });
        }

        let text = fs::read_to_string(&self.path)?;
        serde_json::from_str(&text).map_err(StoreError::from)
    }

    pub fn save(&self, snapshot: &WorkspaceIndexSnapshot) -> Result<(), StoreError> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }

        let _lock = FileLockGuard::acquire(&self.lock_path)?;
        let temp_path = temporary_path_for(&self.path);
        let serialized = serde_json::to_string_pretty(snapshot)?;
        fs::write(&temp_path, format!("{serialized}\n"))?;
        replace_file(&temp_path, &self.path)?;
        Ok(())
    }
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
