mod broker_state_store;
mod file_lock;
mod path_guard;
mod path_utils;
mod policy_state_store;
mod replace;
mod review_state_store;
mod workspace_index_state_store;

pub use path_guard::{PathGuardError, ensure_no_hardlink_alias, ensure_no_symlink_components};
pub use path_utils::normalize_absolute_path;
pub use policy_state_store::{PolicyStateStore, StoreError};
pub use broker_state_store::{BrokerStateSnapshot, BrokerStateStore};
pub use review_state_store::{ReviewStateSnapshot, ReviewStateStore};
pub use workspace_index_state_store::WorkspaceIndexStateStore;

pub const PROJECT_METADATA_DIR_NAME: &str = ".ai-ide";
pub const POLICY_STATE_FILENAME: &str = "policy.json";
