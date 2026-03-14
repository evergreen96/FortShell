use std::error::Error;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use ai_ide_core::review::{ReviewError, ReviewManager};
use ai_ide_core::review_render::ReviewRenderService;
use ai_ide_persistence::{
    PROJECT_METADATA_DIR_NAME, PathGuardError, ReviewStateStore, StoreError,
    ensure_no_hardlink_alias, ensure_no_symlink_components, normalize_absolute_path,
};
use ai_ide_policy::PolicyEngine;
use ai_ide_protocol::{ProposalStatus, WriteProposal};

pub struct ReviewController {
    root: PathBuf,
    store: ReviewStateStore,
    manager: ReviewManager,
}

#[derive(Debug)]
pub enum ReviewControlError {
    Review(ReviewError),
    Store(StoreError),
    Io(io::Error),
    PathEscapesRoot { target: String },
    InternalPath { target: String },
    BlockedByPolicy { target: String },
    SymlinkPath { target: String },
    HardlinkPath { target: String },
    TargetIsDirectory { target: String },
}

impl fmt::Display for ReviewControlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ReviewControlError::Review(error) => write!(f, "{error:?}"),
            ReviewControlError::Store(error) => write!(f, "{error}"),
            ReviewControlError::Io(error) => write!(f, "{error}"),
            ReviewControlError::PathEscapesRoot { target } => {
                write!(f, "Outside active workspace root: {target}")
            }
            ReviewControlError::InternalPath { target } => {
                write!(f, "Blocked internal path: {target}")
            }
            ReviewControlError::BlockedByPolicy { target } => {
                write!(f, "Blocked by policy: {target}")
            }
            ReviewControlError::SymlinkPath { target } => {
                write!(f, "Blocked symlink path: {target}")
            }
            ReviewControlError::HardlinkPath { target } => {
                write!(f, "Blocked hardlink path: {target}")
            }
            ReviewControlError::TargetIsDirectory { target } => {
                write!(f, "Target is not a file: {target}")
            }
        }
    }
}

impl Error for ReviewControlError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            ReviewControlError::Review(_) => None,
            ReviewControlError::Store(error) => Some(error),
            ReviewControlError::Io(error) => Some(error),
            ReviewControlError::PathEscapesRoot { .. }
            | ReviewControlError::InternalPath { .. }
            | ReviewControlError::BlockedByPolicy { .. }
            | ReviewControlError::SymlinkPath { .. }
            | ReviewControlError::HardlinkPath { .. }
            | ReviewControlError::TargetIsDirectory { .. } => None,
        }
    }
}

impl From<ReviewError> for ReviewControlError {
    fn from(value: ReviewError) -> Self {
        Self::Review(value)
    }
}

impl From<StoreError> for ReviewControlError {
    fn from(value: StoreError) -> Self {
        Self::Store(value)
    }
}

impl From<io::Error> for ReviewControlError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

impl From<PathGuardError> for ReviewControlError {
    fn from(value: PathGuardError) -> Self {
        match value {
            PathGuardError::SymlinkPath { target } => Self::SymlinkPath { target },
            PathGuardError::HardlinkPath { target } => Self::HardlinkPath { target },
        }
    }
}

impl ReviewController {
    pub fn new(root: impl AsRef<Path>) -> Self {
        Self::from_parts(root.as_ref(), ReviewStateStore::disabled(), Vec::new())
    }

    pub fn with_store_path(
        root: impl AsRef<Path>,
        store_path: impl AsRef<Path>,
    ) -> Result<Self, ReviewControlError> {
        let store = ReviewStateStore::new(store_path);
        let snapshot = store.load()?;
        Ok(Self::from_parts(root.as_ref(), store, snapshot.proposals))
    }

    pub fn proposals(&self) -> &[WriteProposal] {
        self.manager.proposals()
    }

    pub fn list_proposals(
        &self,
        status: Option<ProposalStatus>,
        limit: usize,
    ) -> Vec<WriteProposal> {
        self.manager.list_proposals(status, limit)
    }

    pub fn count_proposals(&self, status: Option<ProposalStatus>) -> usize {
        self.manager.count_proposals(status)
    }

    pub fn get_proposal(&self, proposal_id: &str) -> Option<&WriteProposal> {
        self.manager.get_proposal(proposal_id)
    }

    pub fn render_proposal(&self, proposal_id: &str) -> Result<String, ReviewControlError> {
        let proposal =
            self.manager
                .get_proposal(proposal_id)
                .ok_or_else(|| ReviewError::UnknownProposal {
                    proposal_id: proposal_id.to_owned(),
                })?;
        Ok(ReviewRenderService::render_proposal(proposal))
    }

    pub fn stage_write(
        &mut self,
        policy: &PolicyEngine,
        target: &str,
        proposed_text: &str,
        session_id: &str,
        agent_session_id: &str,
    ) -> Result<WriteProposal, ReviewControlError> {
        let resolved_path = self.resolve_allowed_path(policy, target)?;
        if resolved_path.exists() && !resolved_path.is_file() {
            return Err(ReviewControlError::TargetIsDirectory {
                target: target.to_owned(),
            });
        }

        let current_content = if resolved_path.exists() {
            Some(fs::read_to_string(&resolved_path)?)
        } else {
            None
        };
        let proposal = self.manager.stage_write(
            self.relative_target(&resolved_path),
            current_content.as_deref(),
            proposed_text,
            session_id,
            agent_session_id,
        );
        self.persist()?;
        Ok(proposal)
    }

    pub fn apply_proposal(
        &mut self,
        policy: &PolicyEngine,
        proposal_id: &str,
    ) -> Result<WriteProposal, ReviewControlError> {
        let proposal = self
            .manager
            .get_proposal(proposal_id)
            .cloned()
            .ok_or_else(|| ReviewError::UnknownProposal {
                proposal_id: proposal_id.to_owned(),
            })?;
        if proposal.status != ProposalStatus::Pending {
            return Err(ReviewError::NotPending {
                proposal_id: proposal.proposal_id,
            }
            .into());
        }

        let resolved_path = self.resolve_allowed_path(policy, &proposal.target)?;
        if resolved_path.exists() && !resolved_path.is_file() {
            let error = self.mark_conflict_and_persist(proposal_id)?;
            return Err(error.into());
        }

        let current_content = if resolved_path.exists() {
            Some(fs::read_to_string(&resolved_path)?)
        } else {
            None
        };

        if proposal.base_text.as_deref() != current_content.as_deref() {
            let error = self
                .manager
                .apply_proposal(proposal_id, current_content.as_deref())
                .expect_err("base mismatch should return a conflict");
            self.persist()?;
            return Err(error.into());
        }

        if let Some(parent) = resolved_path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&resolved_path, proposal.proposed_text.as_bytes())?;

        let updated = self
            .manager
            .apply_proposal(proposal_id, current_content.as_deref())?;
        self.persist()?;
        Ok(updated)
    }

    pub fn reject_proposal(
        &mut self,
        proposal_id: &str,
    ) -> Result<WriteProposal, ReviewControlError> {
        let updated = self.manager.reject_proposal(proposal_id)?;
        self.persist()?;
        Ok(updated)
    }

    fn from_parts(root: &Path, store: ReviewStateStore, proposals: Vec<WriteProposal>) -> Self {
        Self {
            root: normalize_absolute_path(root),
            store,
            manager: ReviewManager::from_proposals(proposals),
        }
    }

    fn persist(&self) -> Result<(), ReviewControlError> {
        self.store.save(self.manager.proposals())?;
        Ok(())
    }

    fn mark_conflict_and_persist(
        &mut self,
        proposal_id: &str,
    ) -> Result<ReviewError, ReviewControlError> {
        let error = ReviewError::Conflict {
            proposal_id: proposal_id.to_owned(),
        };
        self.manager.mark_conflict(proposal_id)?;
        self.persist()?;
        Ok(error)
    }

    fn resolve_allowed_path(
        &self,
        policy: &PolicyEngine,
        target: &str,
    ) -> Result<PathBuf, ReviewControlError> {
        let raw_target = Path::new(target);
        let candidate = if raw_target.is_absolute() {
            raw_target.to_path_buf()
        } else {
            self.root.join(raw_target)
        };
        ensure_no_symlink_components(&self.root, &candidate)?;
        ensure_no_hardlink_alias(&self.root, &candidate)?;
        let resolved = normalize_absolute_path(&candidate);
        match resolved.strip_prefix(&self.root) {
            Ok(relative) => {
                let relative_text = path_to_posix(relative);
                if is_internal_path(relative) {
                    return Err(ReviewControlError::InternalPath {
                        target: relative_text,
                    });
                }
                if !policy.is_allowed(&resolved) {
                    return Err(ReviewControlError::BlockedByPolicy {
                        target: relative_text,
                    });
                }
                Ok(resolved)
            }
            Err(_) => Ok(resolved),
        }
    }

    fn relative_target(&self, path: &Path) -> String {
        match path.strip_prefix(&self.root) {
            Ok(relative) => path_to_posix(relative),
            Err(_) => path.to_string_lossy().replace('\\', "/"),
        }
    }
}

fn is_internal_path(relative: &Path) -> bool {
    relative
        .components()
        .next()
        .is_some_and(|component| component.as_os_str() == PROJECT_METADATA_DIR_NAME)
}

fn path_to_posix(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}
