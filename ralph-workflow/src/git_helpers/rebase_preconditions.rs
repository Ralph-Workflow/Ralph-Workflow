// Core rebase operations: precondition validation, rebase execution, and lifecycle management.
//
// This file contains:
// - Precondition validation (shallow clone, worktree, submodule, sparse checkout checks)
// - rebase_onto - main rebase execution function
// - abort_rebase - abort an in-progress rebase
// - continue_rebase - continue after conflict resolution
// - get_conflicted_files - retrieve list of conflicted files
// - verify_rebase_completed - verify rebase completion status
// - rebase_in_progress - check if rebase is in progress

/// Validate preconditions before starting a rebase operation.
///
/// This function performs Category 1 (pre-start) validation checks to ensure
/// the repository is in a valid state for rebasing. It checks for common
/// issues that would cause a rebase to fail immediately.
///
/// # Arguments
///
/// * `executor` - Process executor for dependency injection
///
/// # Returns
///
/// Returns `Ok(())` if all preconditions are met, or an error with a
/// descriptive message if validation fails.
///
/// # Validation Checks
///
/// - Repository integrity (valid HEAD, index, object database)
/// - No concurrent Git operations (merge, rebase, cherry-pick, etc.)
/// - Git identity is configured (user.name and user.email)
/// - Working tree is not dirty (no unstaged or staged changes)
/// - Not a shallow clone (shallow clones have limited history)
/// - No worktree conflicts (branch not checked out elsewhere)
/// - Submodules are initialized and in a valid state
/// - Sparse checkout is properly configured (if enabled)
///
/// # Example
///
/// ```ignore
/// use ralph_workflow::git_helpers::rebase::validate_rebase_preconditions;
///
/// match validate_rebase_preconditions(&executor) {
///     Ok(()) => println!("All preconditions met, safe to rebase"),
///     Err(e) => eprintln!("Cannot rebase: {e}"),
/// }
/// ```
///
/// # Errors
///
/// Returns an error if preconditions are not met or validation fails.
#[cfg(any(test, feature = "test-utils"))]
pub fn validate_rebase_preconditions(
    executor: &dyn crate::executor::ProcessExecutor,
) -> io::Result<()> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;

    // 1. Check repository integrity
    validate_git_state()?;

    // 2. Check for concurrent Git operations
    if let Some(concurrent_op) = detect_concurrent_git_operations()? {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "Cannot start rebase: {} already in progress. \
                 Please complete or abort the current operation first.",
                concurrent_op.description()
            ),
        ));
    }

    // 3. Check Git identity configuration
    let config = repo.config().map_err(|e| git2_to_io_error(&e))?;

    let user_name = config.get_string("user.name");
    let user_email = config.get_string("user.email");

    if user_name.is_err() && user_email.is_err() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Git identity is not configured. Please set user.name and user.email:\n  \
             git config --global user.name \"Your Name\"\n  \
             git config --global user.email \"you@example.com\"",
        ));
    }

    // 4. Check for dirty working tree using Git CLI via executor
    let status_output = executor.execute("git", &["status", "--porcelain"], &[], None)?;

    if status_output.status.success() {
        let stdout = status_output.stdout.trim();
        if !stdout.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Working tree is not clean. Please commit or stash changes before rebasing.",
            ));
        }
    } else {
        // If git status fails, try with libgit2 as fallback
        let statuses = repo.statuses(None).map_err(|e| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("Failed to check working tree status: {e}"),
            )
        })?;

        if !statuses.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Working tree is not clean. Please commit or stash changes before rebasing.",
            ));
        }
    }

    // 5. Check for shallow clone (limited history)
    check_shallow_clone()?;

    // 6. Check for worktree conflicts (branch checked out in another worktree)
    check_worktree_conflicts()?;

    // 7. Check submodule state (if submodules exist)
    check_submodule_state()?;

    // 8. Check sparse checkout configuration (if enabled)
    check_sparse_checkout_state()?;

    Ok(())
}

/// Check if the repository is a shallow clone.
///
/// Shallow clones have limited history and may not have all the commits
/// needed for a successful rebase.
///
/// # Returns
///
/// Returns `Ok(())` if the repository is a full clone, or an error if
/// it's a shallow clone.
#[cfg(any(test, feature = "test-utils"))]
fn check_shallow_clone() -> io::Result<()> {
    use std::fs;

    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    let git_dir = repo.path();

    // Check for shallow marker file
    let shallow_file = git_dir.join("shallow");
    if shallow_file.exists() {
        // This is a shallow clone - read the file to see how many commits we have
        let content = fs::read_to_string(&shallow_file).unwrap_or_default();
        let line_count = content.lines().count();

        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "Repository is a shallow clone with {line_count} commits. \
                 Rebasing may fail due to missing history. \
                 Consider running: git fetch --unshallow"
            ),
        ));
    }

    Ok(())
}

/// Check if the current branch is checked out in another worktree.
///
/// Git does not allow a branch to be checked out in multiple worktrees
/// simultaneously.
///
/// # Returns
///
/// Returns `Ok(())` if the branch is not checked out elsewhere, or an
/// error if there's a worktree conflict.
#[cfg(any(test, feature = "test-utils"))]
fn check_worktree_conflicts() -> io::Result<()> {
    use std::fs;

    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;

    // Get current branch name
    let head = repo.head().map_err(|e| git2_to_io_error(&e))?;
    let branch_name = match head.shorthand() {
        Some(name) if head.is_branch() => name,
        _ => return Ok(()), // Detached HEAD or unborn branch - skip check
    };

    let git_dir = repo.path();
    let worktrees_dir = git_dir.join("worktrees");

    if !worktrees_dir.exists() {
        return Ok(());
    }

    // Check each worktree to see if our branch is checked out there
    let entries = fs::read_dir(&worktrees_dir).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Failed to read worktrees directory: {e}"),
        )
    })?;

    for entry in entries.flatten() {
        let worktree_path = entry.path();
        let worktree_head = worktree_path.join("HEAD");

        if worktree_head.exists() {
            if let Ok(content) = fs::read_to_string(&worktree_head) {
                // Check if this worktree has our branch checked out
                if content.contains(&format!("refs/heads/{branch_name}")) {
                    // Extract worktree name from path
                    let worktree_name = worktree_path
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("unknown");

                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        format!(
                            "Branch '{branch_name}' is already checked out in worktree '{worktree_name}'. \
                             Use 'git worktree add' to create a new worktree for this branch."
                        ),
                    ));
                }
            }
        }
    }

    Ok(())
}

/// Check if submodules are in a valid state.
///
/// Submodules should be initialized and updated before rebasing to avoid
/// conflicts and errors.
///
/// # Returns
///
/// Returns `Ok(())` if submodules are in a valid state or no submodules
/// exist, or an error if there are submodule issues.
#[cfg(any(test, feature = "test-utils"))]
fn check_submodule_state() -> io::Result<()> {
    use std::fs;

    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    let git_dir = repo.path();

    // Check if .gitmodules exists
    let workdir = repo.workdir().unwrap_or(git_dir);
    let gitmodules_path = workdir.join(".gitmodules");

    if !gitmodules_path.exists() {
        return Ok(()); // No submodules
    }

    // We have submodules - check for common issues
    let modules_dir = git_dir.join("modules");
    if !modules_dir.exists() {
        // .gitmodules exists but .git/modules doesn't - submodules not initialized
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Submodules are not initialized. Run: git submodule update --init --recursive",
        ));
    }

    // Check for orphaned submodule references (common issue after rebasing)
    let gitmodules_content = fs::read_to_string(&gitmodules_path).unwrap_or_default();
    let submodule_count = gitmodules_content.matches("path = ").count();

    if submodule_count > 0 {
        // Verify each submodule directory exists
        for line in gitmodules_content.lines() {
            if line.contains("path = ") {
                if let Some(path) = line.split("path = ").nth(1) {
                    let submodule_path = workdir.join(path.trim());
                    if !submodule_path.exists() {
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidInput,
                            format!(
                                "Submodule '{}' is not initialized. Run: git submodule update --init --recursive",
                                path.trim()
                            ),
                        ));
                    }
                }
            }
        }
    }

    Ok(())
}

/// Check if sparse checkout is properly configured.
///
/// Sparse checkout can cause issues during rebase if files outside the
/// sparse checkout cone are modified.
///
/// # Returns
///
/// Returns `Ok(())` if sparse checkout is not enabled or is properly
/// configured, or an error if there are issues.
#[cfg(any(test, feature = "test-utils"))]
fn check_sparse_checkout_state() -> io::Result<()> {
    use std::fs;

    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    let git_dir = repo.path();

    // Check if sparse checkout is enabled
    let config = repo.config().map_err(|e| git2_to_io_error(&e))?;

    let sparse_checkout = config.get_bool("core.sparseCheckout");
    let sparse_checkout_cone = config.get_bool("extensions.sparseCheckoutCone");

    match (sparse_checkout, sparse_checkout_cone) {
        (Ok(true), _) | (_, Ok(true)) => {
            // Sparse checkout is enabled - check if it's properly configured
            let info_sparse_dir = git_dir.join("info").join("sparse-checkout");

            if !info_sparse_dir.exists() {
                // Sparse checkout enabled but no config file - this is a problem
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "Sparse checkout is enabled but not configured. \
                     Run: git sparse-checkout init",
                ));
            }

            // Verify the sparse-checkout file has content
            if let Ok(content) = fs::read_to_string(&info_sparse_dir) {
                if content.trim().is_empty() {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "Sparse checkout configuration is empty. \
                         Run: git sparse-checkout set <patterns>",
                    ));
                }
            }

            // Sparse checkout is enabled - warn but don't fail
            // Rebase should work with sparse checkout, but conflicts may occur
            // for files outside the sparse checkout cone
            // We return Ok to allow the operation, but the caller should be aware
        }
        _ => {
            // Config not set - sparse checkout not enabled, or other case
        }
    }

    Ok(())
}

