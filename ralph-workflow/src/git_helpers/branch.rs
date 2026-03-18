//! Git branch detection and default branch resolution.
//!
//! This module provides functionality to:
//! - Get the current branch name
//! - Check if we're on a main/master branch
//! - Detect the default branch from origin/HEAD
//! - Ensure we're on a feature branch when needed
//!
//! Uses libgit2 directly for all operations.

#![deny(unsafe_code)]

use std::io;
use std::path::Path;

use super::git2_to_io_error;

/// Check if the current branch is "main" or "master".
///
/// This is used to determine if we're on a protected branch where
/// rebasing should be skipped.
///
/// # Returns
///
/// Returns `Ok(true)` if on main/master, `Ok(false)` if on another branch,
/// or an error if the branch cannot be determined.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn is_main_or_master_branch() -> io::Result<bool> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    is_main_or_master_branch_impl(&repo)
}

/// Implementation of `is_main_or_master_branch`.
fn is_main_or_master_branch_impl(repo: &git2::Repository) -> io::Result<bool> {
    let head = repo.head().map_err(|e| git2_to_io_error(&e))?;

    // Get the branch name from the reference name
    // HEAD is usually a symbolic reference like "refs/heads/main"
    let reference_name = head.shorthand().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            "Could not determine branch name from HEAD",
        )
    })?;

    Ok(reference_name == "main" || reference_name == "master")
}

/// Get the default branch name from the repository.
///
/// This function attempts to detect the default branch by:
/// 1. Checking `refs/remotes/origin/HEAD` (the origin's default branch)
/// 2. Falling back to checking if "main" or "master" exists locally
/// 3. Defaulting to "main" as a last resort
///
/// # Returns
///
/// Returns `Ok(String)` with the default branch name (e.g., "main", "master"),
/// or an error if the repository cannot be opened.
///
/// **Note:** This function uses the current working directory to discover the repo.
/// For explicit path control, use [`get_default_branch_at`] instead.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_default_branch() -> io::Result<String> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    Ok(get_default_branch_impl(&repo))
}

/// Get the default branch name from a specific repository path.
///
/// # Arguments
///
/// * `repo_root` - Path to the repository root
///
/// # Returns
///
/// Returns `Ok(String)` with the default branch name (e.g., "main", "master"),
/// or an error if the repository cannot be opened.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_default_branch_at(repo_root: &Path) -> io::Result<String> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    Ok(get_default_branch_impl(&repo))
}

/// Pure policy: determine the default branch name from git repository state.
fn determine_default_branch(repo: &git2::Repository) -> String {
    resolve_default_branch_from_origin(repo)
        .or_else(|| resolve_default_branch_from_local(repo))
        .unwrap_or_else(|| "main".to_string())
}

fn resolve_default_branch_from_origin(repo: &git2::Repository) -> Option<String> {
    let origin_head = repo.find_reference("refs/remotes/origin/HEAD").ok()?;
    let target = origin_head.symbolic_target()?;
    target
        .strip_prefix("refs/remotes/origin/")
        .map(String::from)
}

fn resolve_default_branch_from_local(repo: &git2::Repository) -> Option<String> {
    if repo.find_branch("main", git2::BranchType::Local).is_ok() {
        return Some("main".to_string());
    }
    if repo.find_branch("master", git2::BranchType::Local).is_ok() {
        return Some("master".to_string());
    }
    None
}

/// Implementation of `get_default_branch`.
fn get_default_branch_impl(repo: &git2::Repository) -> String {
    determine_default_branch(repo)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_main_or_master_branch_returns_result() {
        // Test that the function returns a Result
        let result = is_main_or_master_branch();
        // We don't assert success/failure since it depends on git state
        let _ = result;
    }

    #[test]
    fn test_get_default_branch_returns_result() {
        // Test that the function returns a Result
        let result = get_default_branch();
        // We don't assert success/failure since it depends on git state
        let _ = result;
    }
}
