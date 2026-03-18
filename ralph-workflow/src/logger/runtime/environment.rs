//! Environment and terminal access for color detection.
//!
//! This module satisfies the dylint boundary-module check for code that accesses
//! environment variables and terminal state.

use std::io::IsTerminal;

/// Environment abstraction for color detection.
///
/// This trait enables testing color detection logic without modifying
/// real environment variables (which would cause test interference).
pub trait ColorEnvironment {
    /// Get an environment variable value.
    fn get_var(&self, name: &str) -> Option<String>;
    /// Check if stdout is a terminal.
    fn is_terminal(&self) -> bool;
}

/// Real environment implementation for production use.
pub struct RealColorEnvironment;

impl ColorEnvironment for RealColorEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn is_terminal(&self) -> bool {
        std::io::stdout().is_terminal()
    }
}
