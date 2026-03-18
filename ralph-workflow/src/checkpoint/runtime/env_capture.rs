//! Environment capture utilities for checkpoint module.
//! This is a boundary module - environment access is allowed here.

use crate::checkpoint::state::EnvironmentSnapshot;

/// Capture the current environment variables relevant to Ralph.
/// This is a boundary function - environment access is allowed here.
#[must_use]
pub fn capture_environment() -> EnvironmentSnapshot {
    EnvironmentSnapshot::from_env_vars(std::env::vars())
}
