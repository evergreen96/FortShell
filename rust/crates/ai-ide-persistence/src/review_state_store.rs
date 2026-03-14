use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use ai_ide_protocol::WriteProposal;

use crate::file_lock::FileLockGuard;
use crate::policy_state_store::StoreError;
use crate::replace::replace_file;

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ReviewStateSnapshot {
    pub proposals: Vec<WriteProposal>,
}

#[derive(Serialize, Deserialize)]
struct ReviewPayload {
    proposals: Vec<WriteProposal>,
}

pub struct ReviewStateStore {
    path: Option<PathBuf>,
    lock_path: Option<PathBuf>,
}

impl ReviewStateStore {
    pub fn new(path: impl AsRef<Path>) -> Self {
        let path = crate::normalize_absolute_path(path.as_ref());
        let lock_path = lock_path_for(&path);
        Self {
            path: Some(path),
            lock_path: Some(lock_path),
        }
    }

    pub fn disabled() -> Self {
        Self {
            path: None,
            lock_path: None,
        }
    }

    pub fn path(&self) -> Option<&Path> {
        self.path.as_deref()
    }

    pub fn load(&self) -> Result<ReviewStateSnapshot, StoreError> {
        let Some(path) = &self.path else {
            return Ok(ReviewStateSnapshot::default());
        };

        if !path.exists() {
            return Ok(ReviewStateSnapshot::default());
        }

        let lock_path = self.lock_path.as_ref().expect("lock path should exist");
        let _lock = FileLockGuard::acquire(lock_path)?;
        if !path.exists() {
            return Ok(ReviewStateSnapshot::default());
        }

        let text = fs::read_to_string(path)?;
        let payload: ReviewPayload = serde_json::from_str(&text)?;
        Ok(ReviewStateSnapshot {
            proposals: payload.proposals,
        })
    }

    pub fn save(&self, proposals: &[WriteProposal]) -> Result<(), StoreError> {
        let Some(path) = &self.path else {
            return Ok(());
        };

        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }

        let lock_path = self.lock_path.as_ref().expect("lock path should exist");
        let _lock = FileLockGuard::acquire(lock_path)?;
        let payload = ReviewPayload {
            proposals: proposals.to_vec(),
        };
        let serialized = serde_json::to_string_pretty(&payload)?;
        let temp_path = temporary_path_for(path);
        fs::write(&temp_path, format!("{serialized}\n"))?;
        replace_file(&temp_path, path)?;
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
