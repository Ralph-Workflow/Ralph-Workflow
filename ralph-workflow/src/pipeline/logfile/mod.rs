//! Unified log file path management.
//!
//! This module provides utilities for building log file paths. There are two
//! naming conventions supported:
//!
//! 1. **Legacy naming** (pre-per-run-logging):
//!    - `{prefix}_{agent}_{model_index}.log` (and with attempt suffix)
//!    - Used for special-purpose logs like commit generation and conflict resolution
//!    - These logs are written outside the per-run log directory structure
//!
//! 2. **Simplified naming** (per-run-logging):
//!    - `{phase}_{index}.log` (with optional `_aN` attempt suffix)
//!    - Used for main pipeline phase agent logs
//!    - These logs are written under `.agent/logs-<run_id>/agents/`
//!
//! The legacy naming functions (`build_logfile_path`, `build_logfile_path_with_attempt`)
//! are kept for special cases where agent identity in the filename is useful (e.g., commit
//! message generation logs, conflict resolution logs) and for backward compatibility with
//! tooling that may parse legacy log filenames.
//!
//! ## Module Organization
//!
//! - `naming` - Log file name construction utilities
//! - `attempt_discovery` - Determine next attempt index for log files
//! - `extraction` - Parse agent names from log file paths
//! - `lookup` - Find and read the most recent log files

mod attempt_discovery;
mod extraction;
mod lookup;
mod naming;

#[cfg(test)]
mod tests;

// Re-export public API
pub use attempt_discovery::{next_logfile_attempt_index, next_simplified_logfile_attempt_index};
pub use extraction::extract_agent_name_from_logfile;
pub use lookup::{find_most_recent_logfile, read_most_recent_logfile};
pub use naming::{build_logfile_path, build_logfile_path_with_attempt, sanitize_agent_name};
