//! Re-export monitoring from boundary module.
//!
//! This module re-exports from files::io::monitoring to avoid dylint violations.

pub use crate::files::io::monitoring::PromptMonitor;
