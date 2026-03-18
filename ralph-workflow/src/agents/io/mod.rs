// Boundary module for I/O operations in the agents module.
// This module contains production implementations of filesystem, network, and runtime traits.

pub mod cache_environment;
pub mod ccs_environment;
pub mod ccs_filesystem;
pub mod network;
pub mod runtime;

// Re-export from ccs_env for convenience (traits are defined there)
pub use crate::agents::ccs_env::{CcsEnvironment, CcsFilesystem};

// Re-export cache trait
pub use cache_environment::CacheEnvironment;

// Re-export production implementations
pub use cache_environment::RealCacheEnvironment;
pub use ccs_environment::RealCcsEnvironment;
pub use ccs_filesystem::RealCcsFilesystem;
pub use network::{fetch_api_catalog_json, get_env_var};
pub use runtime::{production_timer, ProductionRetryTimer, RetryTimerProvider};
