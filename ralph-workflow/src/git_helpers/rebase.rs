//! Git rebase operations using libgit2 with Git CLI fallback.
//!
//! This module provides functionality to:
//! - Perform rebase operations onto a specified upstream branch
//! - Detect and report conflicts during rebase
//! - Abort an in-progress rebase
//! - Continue a rebase after conflict resolution
//! - Get lists of conflicted files
//! - Handle all rebase failure modes with fault tolerance
//!
//! # Architecture
//!
//! This module uses a hybrid approach:
//! - **libgit2**: For repository state detection, validation, and queries
//! - **Git CLI**: For the actual rebase operation (more reliable)
//! - **Fallback patterns**: For operations that may fail with libgit2
//!
//! The Git CLI is used for rebase operations because:
//! 1. Better error messages for classification
//! 2. More robust edge case handling
//! 3. Better tested across Git versions
//! 4. Supports autostash and other features reliably

#![deny(unsafe_code)]

/// Git directory name for rebase-apply state (for `git am`-style rebases).
///
/// Used by `detect_concurrent_git_operations` and `cleanup_stale_rebase_state`
/// functions which are only available with the test-utils feature.
#[cfg(any(test, feature = "test-utils"))]
const REBASE_APPLY_DIR: &str = "rebase-apply";

/// Git directory name for rebase-merge state (for interactive rebases).
///
/// Used by `detect_concurrent_git_operations` and `cleanup_stale_rebase_state`
/// functions which are only available with the test-utils feature.
#[cfg(any(test, feature = "test-utils"))]
const REBASE_MERGE_DIR: &str = "rebase-merge";

use std::path::Path;

use crate::git_helpers::git2_to_io_error;

mod io {
    pub(crate) type Result<T> = std::io::Result<T>;
    pub(crate) type Error = std::io::Error;
    pub(crate) type ErrorKind = std::io::ErrorKind;
}

include!("rebase_kinds.rs");
include!("rebase_classification.rs");
include!("conflict_detection.rs");
include!("rebase_preconditions.rs");
include!("rebase_run.rs");
include!("rebase_abort.rs");
include!("rebase_conflicts.rs");
include!("rebase_continuation.rs");
include!("rebase_tests.rs");
