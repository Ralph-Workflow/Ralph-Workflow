//! Common utility functions shared across the crate.
//!
//! This module provides utility functions that are used throughout the codebase:
//! - Shell command parsing
//! - Text truncation for display
//! - Secret redaction for logging
//! - External tool detection

pub mod domain_types;
pub mod utils;

// Re-export commonly used utility functions
pub use utils::{format_argv_for_log, split_command, truncate_text};
