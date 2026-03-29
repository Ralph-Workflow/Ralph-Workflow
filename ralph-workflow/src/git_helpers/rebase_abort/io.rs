// git_helpers/rebase_abort/io.rs — boundary module for core rebase operations: abort.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Core rebase operations: abort.

/// Abort the current rebase operation using explicit repo root.
///
/// This cleans up the rebase state and returns the repository to its
/// pre-rebase condition.
///
/// # Arguments
///
/// * `repo_root` - Path to the repository root
/// * `executor` - Process executor for dependency injection
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn abort_rebase_at(
    repo_root: &std::path::Path,
    executor: &dyn crate::executor::ProcessExecutor,
) -> io::Result<()> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    abort_rebase_impl(&repo, executor)
}

/// Abort the current rebase operation.
///
/// This cleans up the rebase state and returns the repository to its
/// pre-rebase condition.
///
/// # Arguments
///
/// * `executor` - Process executor for dependency injection
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn abort_rebase(executor: &dyn crate::executor::ProcessExecutor) -> io::Result<()> {
    let repo_root = std::env::current_dir()?;
    abort_rebase_at(&repo_root, executor)
}

/// Implementation of `abort_rebase`.
fn abort_rebase_impl(
    repo: &git2::Repository,
    executor: &dyn crate::executor::ProcessExecutor,
) -> io::Result<()> {
    if !is_rebase_in_progress(repo) {
        return Err(not_in_rebase_error());
    }

    let output = executor.execute("git", &["rebase", "--abort"], &[], None)?;

    if output.succeeded() {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "Failed to abort rebase: {}",
            output.stderr
        )))
    }
}

fn is_rebase_in_progress(repo: &git2::Repository) -> bool {
    let state = repo.state();
    state == git2::RepositoryState::Rebase
        || state == git2::RepositoryState::RebaseMerge
        || state == git2::RepositoryState::RebaseInteractive
}

fn not_in_rebase_error() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, "No rebase in progress")
}
