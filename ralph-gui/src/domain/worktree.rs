//! Pure domain helpers for worktree operations.

use std::path::Path;

/// Validate worktree naming convention.
///
/// Returns true if the name matches `wt-[number]-[name]`.
pub fn validate_worktree_name(name: &str) -> bool {
    name.starts_with("wt-")
        && name.len() > 5
        && name[3..].chars().next().is_some_and(|c| c.is_ascii_digit())
        && name.contains('-')
}

/// Validate that a branch name is valid for worktree creation.
pub fn validate_branch_name(branch: &str) -> bool {
    !branch.is_empty() && !branch.contains('/')
}

/// Compute the worktree path given the repo path and name.
pub fn compute_worktree_path(
    repo_path: &Path,
    name: &str,
    base_path: Option<&Path>,
) -> std::path::PathBuf {
    let parent_dir = base_path
        .and_then(|p| p.parent())
        .or_else(|| repo_path.parent());

    match parent_dir {
        Some(parent) => parent.join(name),
        None => std::path::PathBuf::from(name),
    }
}

/// Check if a path has an active run by looking for the run.lock file.
pub fn path_has_active_run(path: &Path) -> bool {
    path.join(".agent").join("tmp").join("run.lock").exists()
}

/// Get the main worktree path from the repo.
pub fn get_main_worktree_path(repo_path: &Path, repo_workdir: Option<&Path>) -> String {
    repo_workdir.map_or_else(
        || repo_path.to_string_lossy().into_owned(),
        |p| p.to_string_lossy().to_string(),
    )
}
