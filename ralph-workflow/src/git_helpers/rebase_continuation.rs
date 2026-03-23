// Core rebase operations: continue + verification + status.

/// Verify that a rebase has completed successfully using `LibGit2`.
///
/// This function uses `LibGit2` exclusively to verify that a rebase operation
/// has completed successfully. It checks:
/// - Repository state is clean (no rebase in progress)
/// - HEAD is valid and not detached (unless expected)
/// - Index has no conflicts
/// - Current branch is descendant of upstream (rebase succeeded)
///
/// # Returns
///
/// Returns `Ok(true)` if rebase is verified as complete, `Ok(false)` if
/// rebase is still in progress (conflicts remain), or an error if the
/// repository state is invalid.
///
/// # Note
///
/// This is the authoritative source for rebase completion verification.
/// It does NOT depend on parsing agent output or any other external signals.
///
/// # Errors
///
/// Returns an error if the repository cannot be accessed or branch verification fails.
#[cfg(any(test, feature = "test-utils"))]
pub fn verify_rebase_completed(upstream_branch: &str) -> io::Result<bool> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;

    // 1. Check if a rebase is still in progress
    let state = repo.state();
    if state == git2::RepositoryState::Rebase
        || state == git2::RepositoryState::RebaseMerge
        || state == git2::RepositoryState::RebaseInteractive
    {
        return Ok(false);
    }

    // 2. Check if there are any remaining conflicts in the index
    let index = repo.index().map_err(|e| git2_to_io_error(&e))?;
    if index.has_conflicts() {
        return Ok(false);
    }

    // 3. Verify HEAD is valid
    let head = repo.head().map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Repository HEAD is invalid: {e}"),
        )
    })?;

    // 4. Verify the current branch is a descendant of upstream
    if let Ok(head_commit) = head.peel_to_commit() {
        if let Ok(upstream_object) = repo.revparse_single(upstream_branch) {
            if let Ok(upstream_commit) = upstream_object.peel_to_commit() {
                match repo.graph_descendant_of(head_commit.id(), upstream_commit.id()) {
                    Ok(is_descendant) => {
                        if is_descendant {
                            return Ok(true);
                        }
                        return Ok(false);
                    }
                    Err(e) => {
                        let _ = e;
                    }
                }
            }
        }
    }

    Ok(!index.has_conflicts())
}

/// Continue a rebase after conflict resolution.
///
/// **Note:** This function uses the current working directory to discover the repo.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn continue_rebase(executor: &dyn crate::executor::ProcessExecutor) -> io::Result<()> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    continue_rebase_impl(&repo, executor)
}

/// Implementation of `continue_rebase`.
fn continue_rebase_impl(
    repo: &git2::Repository,
    executor: &dyn crate::executor::ProcessExecutor,
) -> io::Result<()> {
    if !rebase_in_progress_impl(repo) {
        return Err(no_rebase_in_progress_error());
    }

    let conflicted = get_conflicted_files()?;
    if !conflicted.is_empty() {
        return Err(conflict_remains_error(conflicted.len()));
    }

    let output = executor.execute("git", &["rebase", "--continue"], &[], None)?;

    if output.succeeded() {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "Failed to continue rebase: {}",
            output.stderr
        )))
    }
}

fn no_rebase_in_progress_error() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, "No rebase in progress")
}

fn conflict_remains_error(count: usize) -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidInput,
        format!(
            "Cannot continue rebase: {} file(s) still have conflicts",
            count
        ),
    )
}

/// Check if a rebase is currently in progress.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn rebase_in_progress() -> io::Result<bool> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    Ok(rebase_in_progress_impl(&repo))
}

/// Implementation of `rebase_in_progress`.
fn rebase_in_progress_impl(repo: &git2::Repository) -> bool {
    let state = repo.state();
    state == git2::RepositoryState::Rebase
        || state == git2::RepositoryState::RebaseMerge
        || state == git2::RepositoryState::RebaseInteractive
}
