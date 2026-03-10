use std::io;
use std::path::{Path, PathBuf};

use crate::git_helpers::git2_to_io_error;

/// Returns the path to the `ralph` subdirectory inside the git metadata directory.
///
/// This directory holds Ralph's runtime enforcement state (marker, track file, head-oid).
/// It is inside `.git/` (or the actual git dir for worktrees) and is therefore invisible
/// to working-tree scans.
///
/// Falls back to `repo_root/.git/ralph` if libgit2 discovery fails (e.g., plain temp
/// directories used in unit tests).
pub fn ralph_git_dir(repo_root: &Path) -> PathBuf {
    if let Ok(hooks_dir) = get_hooks_dir_from(repo_root) {
        if let Some(git_dir) = hooks_dir.parent() {
            return git_dir.join("ralph");
        }
    }
    // Fallback: assume standard .git directory layout.
    repo_root.join(".git").join("ralph")
}

/// Check if we're in a git repository.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn require_git_repo() -> io::Result<()> {
    git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    Ok(())
}

/// Get the git repository root.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_repo_root() -> io::Result<PathBuf> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    repo.workdir()
        .map(PathBuf::from)
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No workdir for repository"))
}

/// Get the git hooks directory path.
///
/// Returns the path to the hooks directory inside .git (or the equivalent
/// for worktrees and other configurations).
pub fn get_hooks_dir() -> io::Result<PathBuf> {
    get_hooks_dir_from(Path::new("."))
}

pub fn get_hooks_dir_from(discovery_root: &Path) -> io::Result<PathBuf> {
    let repo = git2::Repository::discover(discovery_root).map_err(|e| git2_to_io_error(&e))?;
    Ok(repo.path().join("hooks"))
}
