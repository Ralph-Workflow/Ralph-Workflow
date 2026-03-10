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
mod snapshot;

pub use commit::{
    git_add_all, git_add_all_in_repo, git_add_specific_in_repo, git_commit, git_commit_in_repo,
    CommitResultFallback,
};
pub use diff::{
    get_git_diff_for_review_with_workspace, get_git_diff_from_start,
    get_git_diff_from_start_with_workspace, git_diff, git_diff_from, git_diff_in_repo,
};
pub use diff_review::{DiffReviewContent, DiffTruncationLevel};
pub use discovery::get_hooks_dir_from;
pub use discovery::{get_hooks_dir, get_repo_root, require_git_repo};
pub use snapshot::{git_snapshot, git_snapshot_in_repo};

#[cfg(test)]
mod tests;
