//! Basic git repository operations.
//!
//! Provides fundamental git operations used throughout the application:
//!
//! - Repository detection and root path resolution
//! - Working tree status snapshots (porcelain format)
//! - Staging and committing changes
//! - Diff generation for commit messages
//!
//! Operations use libgit2 directly to avoid CLI dependencies and work
//! even when git is not installed.

mod commit;
mod diff;
mod diff_review;
mod discovery;
mod exclude;
mod snapshot;

use crate::git_helpers::git2_to_io_error;

pub use commit::{
    git_add_all_in_repo, git_add_specific_in_repo, git_commit_in_repo, CommitResultFallback,
};
pub use diff::{
    get_git_diff_for_review_with_workspace, get_git_diff_from_start,
    get_git_diff_from_start_with_workspace, git_diff, git_diff_from, git_diff_from_in_repo,
    git_diff_in_repo,
};
pub use diff_review::{DiffReviewContent, DiffTruncationLevel};
pub(super) use discovery::get_hooks_dir_from;
pub(super) use discovery::ralph_git_dir;
pub(super) use discovery::{
    ensure_ralph_git_dir, normalize_protection_scope_path, quarantine_path_in_place,
    sanitize_ralph_git_dir_at,
};
pub use discovery::{
    get_repo_root, get_repo_root_at, require_git_repo, require_git_repo_at, ProtectionScope,
};
pub use discovery::{resolve_protection_scope, resolve_protection_scope_from};
pub use exclude::ensure_local_excludes;
pub use snapshot::{git_snapshot, git_snapshot_in_repo, parse_git_status_paths};

/// Get the git repository root for an explicit repository path.
///
/// This avoids accidentally discovering a different repository when the process
/// current working directory is not inside `repo_root`.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_repo_root_in_repo(repo_root: &std::path::Path) -> std::io::Result<std::path::PathBuf> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    repo.workdir().map(std::path::PathBuf::from).ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "No workdir for repository")
    })
}

#[cfg(test)]
mod tests;
