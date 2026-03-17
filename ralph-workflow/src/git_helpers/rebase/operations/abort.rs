// Core rebase operations: abort.

/// Abort the current rebase operation.
///
/// This cleans up the rebase state and returns the repository to its
/// pre-rebase condition.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn abort_rebase(executor: &dyn crate::executor::ProcessExecutor) -> io::Result<()> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    abort_rebase_impl(&repo, executor)
}

/// Implementation of `abort_rebase`.
fn abort_rebase_impl(
    repo: &git2::Repository,
    executor: &dyn crate::executor::ProcessExecutor,
) -> io::Result<()> {
    // Check if a rebase is in progress
    let state = repo.state();
    if state != git2::RepositoryState::Rebase
        && state != git2::RepositoryState::RebaseMerge
        && state != git2::RepositoryState::RebaseInteractive
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "No rebase in progress",
        ));
    }

    // Use git CLI for abort via executor
    let output = executor.execute("git", &["rebase", "--abort"], &[], None)?;

    let is_success = output.status.success();
    if is_success {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "Failed to abort rebase: {}",
            output.stderr
        )))
    }
}
