//! File system state capture and validation for checkpoints.
//!
//! This module provides functionality for capturing and validating state
//! of key files in repository to enable idempotent recovery.

use crate::checkpoint::execution_history::FileSnapshot;
use crate::workspace::Workspace;
use crate::{ProcessExecutor, RealProcessExecutor};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;

/// File system state snapshot for key files.
///
/// Captures the state of important files that affect pipeline execution.
/// This enables validation on resume to detect unexpected changes.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct FileSystemState {
    /// Snapshots of tracked files
    pub files: HashMap<String, FileSnapshot>,
    /// Git HEAD commit OID (if available)
    pub git_head_oid: Option<String>,
    /// Git branch name (if available)
    pub git_branch: Option<String>,
    /// Git status output (porcelain format) for tracking staged/unstaged changes
    pub git_status: Option<String>,
    /// List of modified files from git diff
    pub git_modified_files: Option<Vec<String>>,
}

include!("file_state/capture.rs");
include!("file_state/validation.rs");
include!("file_state/error.rs");
include!("file_state/recovery.rs");

#[cfg(test)]
include!("file_state/tests.rs");
