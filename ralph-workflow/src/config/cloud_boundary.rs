//! Cloud configuration loading from environment.
//!
//! This is a **boundary module** that loads cloud configuration from the process environment.
//! It follows the boundary pattern: pure parsing logic is in `cloud.rs`, environment access here.

use super::cloud::{CloudConfig, GitRemoteConfig};

/// Load cloud configuration from the process environment.
///
/// This is a boundary function that reads environment variables
/// and passes them to the pure `from_env_fn` parser.
#[must_use]
pub fn load_cloud_config_from_env() -> CloudConfig {
    CloudConfig::from_env_fn(|k| std::env::var(k).ok())
}

/// Load git remote configuration from the process environment.
///
/// This is a boundary function that reads environment variables
/// and passes them to the pure `from_env_fn` parser.
#[must_use]
pub fn load_git_remote_config_from_env() -> GitRemoteConfig {
    GitRemoteConfig::from_env_fn(|k| std::env::var(k).ok())
}
