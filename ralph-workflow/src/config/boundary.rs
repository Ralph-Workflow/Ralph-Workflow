//! Boundary module for environment access.
//!
//! This module contains thin boundary functions that read from the process environment
//! and pass pure data to domain functions. All environment access lives here.

use crate::config::cloud::CloudConfig;

/// Load cloud configuration from the process environment.
///
/// This is a boundary function that reads environment variables
/// and passes them to the pure `from_env_fn` parser.
#[must_use]
pub(crate) fn load_cloud_config_from_env() -> CloudConfig {
    CloudConfig::from_env_fn(|k| std::env::var(k).ok())
}
