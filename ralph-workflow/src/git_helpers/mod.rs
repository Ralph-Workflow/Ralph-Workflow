//! Git Helper Functions
//!
//! Provides git hooks management, a git wrapper for blocking commits during the
//! agent phase, and basic repository utilities.
//!
//! Core git operations use libgit2 directly. Agent-phase defense-in-depth may also
//! install a temporary PATH wrapper that intercepts the git CLI when available.
//!
//! # Module Structure
//!
//! - `hooks` - Git hooks installation and removal
//! - [`identity`] - Git identity resolution with comprehensive fallback chain
//! - `repo` - Basic git repository operations (add, commit, snapshot)
//! - `start_commit` - Starting commit tracking for incremental diffs
//! - `review_baseline` - Per-review-cycle baseline tracking
//! - `wrapper` - Agent phase git wrapper for safe concurrent execution
//! - [`branch`] - Branch detection and default branch resolution
//! - `rebase` - Rebase operations with fault tolerance

#![deny(unsafe_code)]

use std::io;

/// Convert git2 errors to `std::io` errors for consistent error handling.
#[cfg(any(test, feature = "test-utils"))]
#[must_use]
pub fn git2_to_io_error(err: &git2::Error) -> io::Error {
    git2_to_io_error_impl(err)
}

#[cfg(not(any(test, feature = "test-utils")))]
pub(crate) fn git2_to_io_error(err: &git2::Error) -> io::Error {
    git2_to_io_error_impl(err)
}

fn git2_to_io_error_impl(err: &git2::Error) -> io::Error {
    // Fall back to mapping git2 error codes to a best-effort io::ErrorKind.
    let kind = match err.code() {
        git2::ErrorCode::NotFound | git2::ErrorCode::UnbornBranch => io::ErrorKind::NotFound,
        git2::ErrorCode::Exists => io::ErrorKind::AlreadyExists,
        git2::ErrorCode::Auth | git2::ErrorCode::Certificate => io::ErrorKind::PermissionDenied,
        git2::ErrorCode::Invalid => io::ErrorKind::InvalidInput,
        git2::ErrorCode::Eof => io::ErrorKind::UnexpectedEof,
        _ => io::ErrorKind::Other,
    };

    io::Error::new(kind, err.to_string())
}

pub mod branch;
#[cfg(any(test, feature = "test-utils"))]
pub mod hooks;
#[cfg(not(any(test, feature = "test-utils")))]
mod hooks;
pub mod identity;
mod rebase;

#[cfg(any(test, feature = "test-utils"))]
pub mod rebase_checkpoint;

#[cfg(any(test, feature = "test-utils"))]
pub mod rebase_state_machine;

mod repo;

/// # Errors
///
/// Returns an error if the git repository cannot be found or hooks directory cannot be determined.
pub fn get_hooks_dir() -> io::Result<std::path::PathBuf> {
    repo::get_hooks_dir_from(std::path::Path::new("."))
}

pub(crate) fn get_hooks_dir_in_repo(repo_root: &std::path::Path) -> io::Result<std::path::PathBuf> {
    repo::get_hooks_dir_from(repo_root)
}
mod review_baseline;
mod start_commit;
mod wrapper;

#[cfg(any(test, feature = "test-utils"))]
pub use branch::get_default_branch_at;
pub use branch::{get_default_branch, is_main_or_master_branch};
#[cfg(any(test, feature = "test-utils"))]
pub use hooks::{file_contains_marker_with_workspace, verify_hook_integrity_with_workspace};
pub use hooks::{
    install_hooks_in_repo, reinstall_hooks_if_tampered, uninstall_hooks, uninstall_hooks_in_repo,
    uninstall_hooks_silent_in_hooks_dir, verify_hooks_removed,
};
pub use hooks::{HOOK_MARKER, RALPH_HOOK_NAMES};
pub use rebase::{
    abort_rebase, continue_rebase, get_conflict_markers_for_file, get_conflicted_files,
    rebase_in_progress, rebase_onto, RebaseResult,
};

// Types that are part of the public API but not used in binary
#[cfg(any(test, feature = "test-utils"))]
pub use rebase::{CleanupResult, ConcurrentOperation};

#[cfg(any(test, feature = "test-utils"))]
pub use rebase::{
    attempt_automatic_recovery, cleanup_stale_rebase_state, detect_concurrent_git_operations,
    is_dirty_tree_cli, rebase_in_progress_cli, validate_rebase_preconditions,
    verify_rebase_completed,
};

pub use rebase::RebaseErrorKind;

#[cfg(any(test, feature = "test-utils"))]
pub use rebase_checkpoint::RebasePhase;

#[cfg(any(test, feature = "test-utils"))]
pub use rebase_state_machine::{RebaseLock, RebaseStateMachine};
pub use repo::{
    ensure_local_excludes, get_git_diff_for_review_with_workspace, get_git_diff_from_start,
    get_git_diff_from_start_with_workspace, get_repo_root, git_add_all, git_add_all_in_repo,
    git_add_specific_in_repo, git_commit, git_commit_in_repo, git_diff, git_diff_from,
    git_diff_in_repo, git_snapshot, git_snapshot_in_repo, parse_git_status_paths, require_git_repo,
    CommitResultFallback, DiffReviewContent, DiffTruncationLevel,
};

#[cfg(any(test, feature = "test-utils"))]
pub use review_baseline::load_review_baseline_with_workspace;
pub use review_baseline::update_review_baseline_with_workspace;
pub use review_baseline::{
    get_baseline_summary, get_review_baseline_info, load_review_baseline, update_review_baseline,
    ReviewBaseline,
};
#[cfg(any(test, feature = "test-utils"))]
pub use start_commit::load_start_point_with_workspace;
pub use start_commit::{
    get_current_head_oid, get_current_head_oid_at, get_start_commit_summary, load_start_point,
    reset_start_commit, save_start_commit, save_start_commit_with_workspace, StartPoint,
};
pub use wrapper::{
    capture_head_oid, cleanup_agent_phase_silent, cleanup_agent_phase_silent_at,
    cleanup_orphaned_marker, cleanup_orphaned_wrapper_at, detect_unauthorized_commit,
    disable_git_wrapper, end_agent_phase, end_agent_phase_in_repo, ensure_agent_phase_protections,
    start_agent_phase, start_agent_phase_in_repo, try_remove_ralph_dir, verify_ralph_dir_removed,
    verify_wrapper_cleaned, GitHelpers, ProtectionCheckResult,
};

// Workspace-aware variants (used by tests and by code paths that must operate
// without requiring a real git repository).
pub use wrapper::{
    cleanup_orphaned_marker_with_workspace, create_marker_with_workspace,
    marker_exists_with_workspace, remove_marker_with_workspace,
};

#[cfg(any(test, feature = "test-utils"))]
pub use wrapper::set_agent_phase_paths_for_test;

// Re-export checkpoint and recovery action for tests only
#[cfg(any(test, feature = "test-utils"))]
pub use rebase_checkpoint::RebaseCheckpoint;

#[cfg(any(test, feature = "test-utils"))]
pub use rebase_state_machine::RecoveryAction;

#[cfg(test)]
mod tests;
