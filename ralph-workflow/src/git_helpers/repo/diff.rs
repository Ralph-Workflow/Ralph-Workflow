// Git diff operations.
//
// Thin I/O primitives live in `diff/io.rs` (boundary module).
// This module contains orchestration logic that calls those primitives.

mod io;

use std::path::Path;

use crate::git_helpers::git_oid_to_git2_oid;
use crate::workspace::Workspace;

/// Get the diff of all changes (unstaged and staged).
///
/// Returns a formatted diff string suitable for LLM analysis.
/// This is similar to `git diff HEAD`.
///
/// Handles the case of an empty repository (no commits yet) by
/// diffing against an empty tree using a read-only approach.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_diff() -> std::io::Result<String> {
    let repo = io::discover_repo(Path::new("."))?;
    diff_against_head(&repo)
}

/// Get the diff of all changes (unstaged and staged) by discovering from an explicit path.
///
/// This avoids coupling diff generation to the process current working directory.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_diff_in_repo(repo_root: &Path) -> std::io::Result<String> {
    let repo = io::discover_repo(repo_root)?;
    diff_against_head(&repo)
}

/// Diff the current working directory against HEAD, handling the unborn-branch case.
fn diff_against_head(repo: &git2::Repository) -> std::io::Result<String> {
    match io::resolve_head_tree_oid(repo)? {
        io::HeadTreeOid::Tree(tree_oid) => io::diff_from_tree_oid_impl(repo, tree_oid),
        io::HeadTreeOid::UnbornBranch => io::diff_from_empty_tree_impl(repo),
    }
}

/// Generate a diff from a specific starting commit.
///
/// Takes a starting commit OID and generates a diff between that commit
/// and the current working tree. Returns a formatted diff string suitable
/// for LLM analysis.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_diff_from(start_oid: &str) -> std::io::Result<String> {
    let repo = io::discover_repo(Path::new("."))?;
    let oid = git2::Oid::from_str(start_oid).map_err(|_| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("Invalid commit OID: {start_oid}"),
        )
    })?;
    io::diff_from_oid_impl(&repo, oid)
}

/// Get the git diff from the starting commit.
///
/// Uses the saved starting commit from `.agent/start_commit` to generate
/// an incremental diff. Falls back to diffing from HEAD if no start commit
/// file exists.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_git_diff_from_start() -> std::io::Result<String> {
    use crate::git_helpers::start_commit::{load_start_point, save_start_commit, StartPoint};

    // Ensure a valid starting point exists. This is expected to persist across runs,
    // but we also repair missing/corrupt files opportunistically for robustness.
    save_start_commit()?;

    let repo = io::discover_repo(Path::new("."))?;

    match load_start_point()? {
        StartPoint::Commit(oid) => {
            let git2_oid = git_oid_to_git2_oid(&oid)?;
            io::diff_from_oid_impl(&repo, git2_oid)
        }
        StartPoint::EmptyRepo => io::diff_from_empty_tree_impl(&repo),
    }
}

/// Get the git diff from the starting commit (workspace-aware).
///
/// This uses `.agent/start_commit` as the baseline and generates a diff between that baseline
/// and the current state on disk, including staged + unstaged changes and untracked files.
///
/// Unlike [`get_git_diff_from_start`], this does not rely on the process CWD.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_git_diff_from_start_with_workspace(
    workspace: &dyn Workspace,
) -> std::io::Result<String> {
    use crate::git_helpers::start_commit::{
        load_start_point_with_workspace, save_start_commit_with_workspace, StartPoint,
    };

    // Fast path: if the workspace has no on-disk .git, refuse to emit a diff.
    // This ensures MemoryWorkspace and other in-memory workspaces never accidentally
    // leak into the process CWD's git repository.
    if !workspace.exists(std::path::Path::new(".git")) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "Workspace has no on-disk git repository",
        ));
    }

    let repo = io::discover_repo(Path::new("."))?;

    // Ensure a valid start point exists. This is expected to persist across runs, but we also
    // repair missing/corrupt files opportunistically for robustness.
    save_start_commit_with_workspace(workspace, &repo)?;

    match load_start_point_with_workspace(workspace, &repo)? {
        StartPoint::Commit(oid) => {
            let git2_oid = git_oid_to_git2_oid(&oid).map_err(|err| {
                std::io::Error::new(std::io::ErrorKind::InvalidData, err.to_string())
            })?;
            io::diff_from_oid_impl(&repo, git2_oid)
        }
        StartPoint::EmptyRepo => io::diff_from_empty_tree_impl(&repo),
    }
}

/// Get the diff content that should be shown to reviewers.
///
/// Baseline selection:
/// - If `.agent/review_baseline.txt` is set, diff from that commit.
/// - Otherwise, diff from `.agent/start_commit` (the initial pipeline baseline).
///
/// The diff is always generated against the current state on disk (staged + unstaged + untracked).
///
/// Returns `(diff, baseline_oid_for_prompts)` where `baseline_oid_for_prompts` is the commit hash
/// to mention in fallback instructions (or empty for empty repo baseline).
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_git_diff_for_review_with_workspace(
    workspace: &dyn Workspace,
) -> std::io::Result<(String, String)> {
    use crate::git_helpers::review_baseline::{
        load_review_baseline_with_workspace, ReviewBaseline,
    };
    use crate::git_helpers::start_commit::{
        load_start_point_with_workspace, save_start_commit_with_workspace, StartPoint,
    };

    // NOTE: We discover the repo from CWD here because the `ReviewBaseline` and `start_commit`
    // files live in the injected Workspace, but the diff itself must be generated from the real
    // on-disk git repository.
    let repo = io::discover_repo(Path::new("."))?;

    let baseline = load_review_baseline_with_workspace(workspace).unwrap_or(ReviewBaseline::NotSet);
    match baseline {
        ReviewBaseline::Commit(oid) => {
            let diff = io::diff_from_oid_impl(&repo, oid)?;
            Ok((diff, oid.to_string()))
        }
        ReviewBaseline::NotSet => {
            // Ensure a valid start point exists.
            save_start_commit_with_workspace(workspace, &repo)?;

            match load_start_point_with_workspace(workspace, &repo)? {
                StartPoint::Commit(oid) => {
                    let git2_oid = git_oid_to_git2_oid(&oid)?;
                    let diff = io::diff_from_oid_impl(&repo, git2_oid)?;
                    Ok((diff, oid.to_string()))
                }
                StartPoint::EmptyRepo => Ok((io::diff_from_empty_tree_impl(&repo)?, String::new())),
            }
        }
    }
}
