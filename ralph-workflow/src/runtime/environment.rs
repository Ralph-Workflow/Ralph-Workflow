//! Environment access in the runtime boundary.
//!
//! This module provides environment-related capabilities that domain code
//! can use through trait abstraction.

use std::collections::HashMap;

/// Trait for environment variable access, allowing testability.
pub trait Environment: Send + Sync {
    /// Get an environment variable.
    fn var(&self, key: &str) -> Option<String>;

    /// Get all environment variables.
    fn vars(&self) -> HashMap<String, String>;
}

/// Real environment implementation using std::env.
pub struct RealEnvironment;

impl Environment for RealEnvironment {
    fn var(&self, key: &str) -> Option<String> {
        std::env::var(key).ok()
    }

    fn vars(&self) -> HashMap<String, String> {
        std::env::vars().collect()
    }
}
