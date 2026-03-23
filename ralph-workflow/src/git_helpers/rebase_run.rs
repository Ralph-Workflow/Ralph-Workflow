/// Perform a rebase onto the specified upstream branch.
///
/// This function rebases the current branch onto the specified upstream branch.
/// It handles the full rebase process including conflict detection and
/// classifies all known failure modes.
///
/// # Arguments
///
/// * `upstream_branch` - The branch to rebase onto (e.g., "main", "origin/main")
/// * `executor` - Process executor for dependency injection
///
/// # Returns
///
/// Returns `Ok(RebaseResult)` indicating the outcome, or an error if:
/// - The repository cannot be opened
/// - The rebase operation fails in an unexpected way
///
/// # Edge Cases Handled
///
/// - Empty repository (no commits) - Returns `Ok(RebaseResult::NoOp)` with reason
/// - Unborn branch - Returns `Ok(RebaseResult::NoOp)` with reason
/// - Already up-to-date - Returns `Ok(RebaseResult::NoOp)` with reason
/// - Unrelated branches (no shared ancestor) - Returns `Ok(RebaseResult::NoOp)` with reason
/// - On main/master branch - Returns `Ok(RebaseResult::NoOp)` with reason
/// - Conflicts during rebase - Returns `Ok(RebaseResult::Conflicts)` or `Failed` with error kind
/// - Other failures - Returns `Ok(RebaseResult::Failed)` with appropriate error kind
///
/// # Note
///
/// This function uses git CLI for rebase operations as libgit2's rebase API
/// has limitations and complexity that make it unreliable for production use.
/// The git CLI is more robust and better tested for rebase operations.
///
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn rebase_onto(
    upstream_branch: &str,
    executor: &dyn crate::executor::ProcessExecutor,
) -> io::Result<RebaseResult> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    rebase_onto_impl(&repo, upstream_branch, executor)
}

/// Implementation of `rebase_onto`.
fn rebase_onto_impl(
    repo: &git2::Repository,
    upstream_branch: &str,
    executor: &dyn crate::executor::ProcessExecutor,
) -> io::Result<RebaseResult> {
    // Check if we have any commits

    match repo.head() {
        Ok(_) => {}
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => {
            // No commits yet - nothing to rebase
            return Ok(RebaseResult::NoOp {
                reason: "Repository has no commits yet (unborn branch)".to_string(),
            });
        }
        Err(e) => return Err(git2_to_io_error(&e)),
    }

    // Get the upstream branch to ensure it exists
    let Ok(upstream_object) = repo.revparse_single(upstream_branch) else {
        return Ok(RebaseResult::Failed(RebaseErrorKind::InvalidRevision {
            revision: upstream_branch.to_string(),
        }));
    };

    let upstream_commit = upstream_object
        .peel_to_commit()
        .map_err(|e| git2_to_io_error(&e))?;

    // Get our branch commit
    let head = repo.head().map_err(|e| git2_to_io_error(&e))?;
    let head_commit = head.peel_to_commit().map_err(|e| git2_to_io_error(&e))?;

    // Check if we're already up-to-date
    if repo
        .graph_descendant_of(head_commit.id(), upstream_commit.id())
        .map_err(|e| git2_to_io_error(&e))?
    {
        // Already up-to-date
        return Ok(RebaseResult::NoOp {
            reason: "Branch is already up-to-date with upstream".to_string(),
        });
    }

    // Check if branches share a common ancestor
    // If merge_base fails with NotFound, branches are unrelated
    match repo.merge_base(head_commit.id(), upstream_commit.id()) {
        Err(e)
            if e.class() == git2::ErrorClass::Reference
                && e.code() == git2::ErrorCode::NotFound =>
        {
            // Branches are unrelated - no shared history
            return Ok(RebaseResult::NoOp {
                reason: format!(
                    "No common ancestor between current branch and '{upstream_branch}' (unrelated branches)"
                ),
            });
        }
        Err(e) => return Err(git2_to_io_error(&e)),
        Ok(_) => {}
    }

    // Check if we're on main/master or in a detached HEAD state
    let Some(branch_name) = head.shorthand() else {
        // Detached HEAD state - rebase is not applicable
        return Ok(RebaseResult::NoOp {
            reason: "HEAD is detached (not on any branch), rebase not applicable".to_string(),
        });
    };

    if branch_name == "main" || branch_name == "master" {
        return Ok(RebaseResult::NoOp {
            reason: format!("Already on '{branch_name}' branch, rebase not applicable"),
        });
    }

    // Use git CLI for rebase via executor - more reliable than libgit2
    let output = executor.execute("git", &["rebase", upstream_branch], &[], None)?;

    let is_success = output.succeeded();
    if is_success {
        Ok(RebaseResult::Success)
    } else {
        let stderr = &output.stderr;
        let stdout = &output.stdout;

        // Use classify_rebase_error to determine specific failure mode
        let error_kind = classify_rebase_error(stderr, stdout);
        Ok(classify_rebase_result(error_kind, stderr))
    }
}

fn classify_rebase_result(error_kind: RebaseErrorKind, stderr: &str) -> RebaseResult {
    match error_kind {
        RebaseErrorKind::ContentConflict { .. } => match get_conflicted_files() {
            Ok(files) if files.is_empty() => {
                if let RebaseErrorKind::ContentConflict { files } = error_kind {
                    RebaseResult::Conflicts(files)
                } else {
                    RebaseResult::Conflicts(vec![])
                }
            }
            Ok(files) => RebaseResult::Conflicts(files),
            Err(_) => RebaseResult::Conflicts(vec![]),
        },
        RebaseErrorKind::Unknown { .. } => {
            if stderr.contains("up to date") {
                RebaseResult::NoOp {
                    reason: "Branch is already up-to-date with upstream".to_string(),
                }
            } else {
                RebaseResult::Failed(error_kind)
            }
        }
        _ => RebaseResult::Failed(error_kind),
    }
}
