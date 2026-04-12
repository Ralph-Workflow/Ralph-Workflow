// Concurrent operation detection and state cleanup for rebase operations.
//
// This file contains:
// - ConcurrentOperation enum for detecting blocking git operations
// - Functions for detecting concurrent operations
// - CleanupResult and cleanup functions for stale state
// - Automatic recovery mechanisms
// - Git state validation utilities

/// Type of concurrent Git operation detected.
///
/// Represents the various Git operations that may be in progress
/// and would block a rebase from starting.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg(any(test, feature = "test-utils"))]
pub enum ConcurrentOperation {
    /// A rebase is already in progress.
    Rebase,
    /// A merge is in progress.
    Merge,
    /// A cherry-pick is in progress.
    CherryPick,
    /// A revert is in progress.
    Revert,
    /// A bisect is in progress.
    Bisect,
    /// Another Git process is holding locks.
    OtherGitProcess,
    /// Unknown concurrent operation.
    Unknown(String),
}

#[cfg(any(test, feature = "test-utils"))]
impl ConcurrentOperation {
    /// Returns a human-readable description of the operation.
    #[must_use]
    pub fn description(&self) -> String {
        match self {
            Self::Rebase => "rebase".to_string(),
            Self::Merge => "merge".to_string(),
            Self::CherryPick => "cherry-pick".to_string(),
            Self::Revert => "revert".to_string(),
            Self::Bisect => "bisect".to_string(),
            Self::OtherGitProcess => "another Git process".to_string(),
            Self::Unknown(s) => format!("unknown operation: {s}"),
        }
    }
}

/// Detect concurrent Git operations that would block a rebase.
///
/// This function performs a comprehensive check for any in-progress Git
/// operations that would prevent a rebase from starting. It checks for:
/// - Rebase in progress (`.git/rebase-apply` or `.git/rebase-merge`)
/// - Merge in progress (`.git/MERGE_HEAD`)
/// - Cherry-pick in progress (`.git/CHERRY_PICK_HEAD`)
/// - Revert in progress (`.git/REVERT_HEAD`)
/// - Bisect in progress (`.git/BISECT_*`)
/// - Lock files held by other processes
///
/// # Returns
///
/// Returns `Ok(None)` if no concurrent operations are detected,
/// or `Ok(Some(operation))` with the type of operation detected.
/// Returns an error if unable to check the repository state.
///
/// # Errors
///
/// Returns an error if the git repository cannot be accessed or filesystem operations fail.
#[cfg(any(test, feature = "test-utils"))]
pub fn detect_concurrent_git_operations() -> io::Result<Option<ConcurrentOperation>> {
    let repo_root = std::env::current_dir()?;
    detect_concurrent_git_operations_at(&repo_root)
}

/// Detect concurrent Git operations that would block a rebase using explicit repo root.
///
/// This is the explicit path variant that should be used in tests and
/// production code where the repository path is known.
#[cfg(any(test, feature = "test-utils"))]
pub fn detect_concurrent_git_operations_at(
    repo_root: &std::path::Path,
) -> io::Result<Option<ConcurrentOperation>> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    detect_concurrent_git_operations_impl(&repo)
}

#[cfg(any(test, feature = "test-utils"))]
fn detect_concurrent_git_operations_impl(
    repo: &git2::Repository,
) -> io::Result<Option<ConcurrentOperation>> {
    use std::fs;

    let git_dir = repo.path();

    // Check for rebase in progress (multiple possible state directories)
    let rebase_merge = git_dir.join(REBASE_MERGE_DIR);
    let rebase_apply = git_dir.join(REBASE_APPLY_DIR);
    if rebase_merge.exists() || rebase_apply.exists() {
        return Ok(Some(ConcurrentOperation::Rebase));
    }

    // Check for merge in progress
    let merge_head = git_dir.join("MERGE_HEAD");
    if merge_head.exists() {
        return Ok(Some(ConcurrentOperation::Merge));
    }

    // Check for cherry-pick in progress
    let cherry_pick_head = git_dir.join("CHERRY_PICK_HEAD");
    if cherry_pick_head.exists() {
        return Ok(Some(ConcurrentOperation::CherryPick));
    }

    // Check for revert in progress
    let revert_head = git_dir.join("REVERT_HEAD");
    if revert_head.exists() {
        return Ok(Some(ConcurrentOperation::Revert));
    }

    // Check for bisect in progress (multiple possible state files)
    let bisect_log = git_dir.join("BISECT_LOG");
    let bisect_start = git_dir.join("BISECT_START");
    let bisect_names = git_dir.join("BISECT_NAMES");
    if bisect_log.exists() || bisect_start.exists() || bisect_names.exists() {
        return Ok(Some(ConcurrentOperation::Bisect));
    }

    // Check for lock files that might indicate concurrent operations
    let index_lock = git_dir.join("index.lock");
    let packed_refs_lock = git_dir.join("packed-refs.lock");
    let head_lock = git_dir.join("HEAD.lock");
    if index_lock.exists() || packed_refs_lock.exists() || head_lock.exists() {
        // Lock files might be stale, so we'll report as "other Git process"
        // The caller can decide whether to wait or clean up
        return Ok(Some(ConcurrentOperation::OtherGitProcess));
    }

    // Check for any other state files we might have missed
    let result: io::Result<Option<ConcurrentOperation>> = fs::read_dir(git_dir)
        .map_err(io::Error::other)?
        .flatten()
        .try_fold(None, |acc, entry| {
            if acc.is_some() {
                return Ok(acc);
            }
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if name_str.contains("REBASE")
                || name_str.contains("MERGE")
                || name_str.contains("CHERRY")
            {
                return Ok(Some(ConcurrentOperation::Unknown(name_str.to_string())));
            }
            Ok(acc)
        });

    result
}

/// Check if a rebase is currently in progress using Git CLI.
///
/// This is a fallback function that uses Git CLI to detect rebase state
/// when libgit2 may not accurately report it.
///
/// # Returns
///
/// Returns `Ok(true)` if a rebase is in progress, `Ok(false)` otherwise.
///
/// # Errors
///
/// Returns an error if the git command fails to execute.
#[cfg(any(test, feature = "test-utils"))]
pub fn rebase_in_progress_cli(executor: &dyn crate::executor::ProcessExecutor) -> io::Result<bool> {
    let output = executor.execute("git", &["status", "--porcelain"], &[], None)?;
    Ok(output.stdout.contains("rebasing"))
}

/// Result of cleaning up stale rebase state.
///
/// Provides information about what was cleaned up during the operation.
#[derive(Debug, Clone, Default)]
#[cfg(any(test, feature = "test-utils"))]
pub struct CleanupResult {
    /// List of state files that were cleaned up
    pub cleaned_paths: Vec<String>,
    /// Whether any lock files were removed
    pub locks_removed: bool,
}

#[cfg(any(test, feature = "test-utils"))]
impl CleanupResult {
    /// Returns true if any cleanup was performed.
    #[must_use]
    pub const fn has_cleanup(&self) -> bool {
        !self.cleaned_paths.is_empty() || self.locks_removed
    }

    /// Returns the number of items cleaned up.
    #[must_use]
    pub const fn count(&self) -> usize {
        self.cleaned_paths.len() + if self.locks_removed { 1 } else { 0 }
    }
}

/// Clean up stale rebase state files.
///
/// This function attempts to clean up stale rebase state that may be
/// left over from interrupted operations. It validates state before
/// removal and reports what was cleaned up.
///
/// This is a recovery mechanism for concurrent operation detection and
/// for cleaning up after interrupted rebase operations.
///
/// # Returns
///
/// Returns `Ok(CleanupResult)` with details of what was cleaned up,
/// or an error if cleanup failed catastrophically.
///
/// # Errors
///
/// Returns an error if the git repository cannot be accessed or filesystem operations fail.
#[cfg(any(test, feature = "test-utils"))]
pub fn cleanup_stale_rebase_state() -> io::Result<CleanupResult> {
    let repo_root = std::env::current_dir()?;
    cleanup_stale_rebase_state_at(&repo_root)
}

/// Clean up stale rebase state files using explicit repo root.
///
/// This is the explicit path variant that should be used in tests and
/// production code where the repository path is known.
#[cfg(any(test, feature = "test-utils"))]
pub fn cleanup_stale_rebase_state_at(repo_root: &std::path::Path) -> io::Result<CleanupResult> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    cleanup_stale_rebase_state_impl(&repo)
}

#[cfg(any(test, feature = "test-utils"))]
fn cleanup_stale_rebase_state_impl(repo: &git2::Repository) -> io::Result<CleanupResult> {
    use std::fs;

    let git_dir = repo.path();

    // List of possible stale rebase state files/directories
    let stale_paths = [
        (REBASE_APPLY_DIR, "rebase-apply directory"),
        (REBASE_MERGE_DIR, "rebase-merge directory"),
        ("MERGE_HEAD", "merge state"),
        ("MERGE_MSG", "merge message"),
        ("CHERRY_PICK_HEAD", "cherry-pick state"),
        ("REVERT_HEAD", "revert state"),
        ("COMMIT_EDITMSG", "commit message"),
    ];

    let lock_files = ["index.lock", "packed-refs.lock", "HEAD.lock"];

    let cleaned_paths: Vec<String> = stale_paths
        .iter()
        .filter_map(|(path_name, description)| {
            let full_path = git_dir.join(path_name);
            if full_path.exists() {
                let is_valid = validate_state_file(&full_path);
                if !is_valid.unwrap_or(true) {
                    let removed = if full_path.is_dir() {
                        fs::remove_dir_all(&full_path)
                            .map(|()| true)
                            .unwrap_or(false)
                    } else {
                        fs::remove_file(&full_path).map(|()| true).unwrap_or(false)
                    };

                    if removed {
                        return Some(format!("{path_name} ({description})"));
                    }
                }
            }
            None
        })
        .chain(lock_files.iter().filter_map(|lock_file| {
            let lock_path = git_dir.join(lock_file);
            if lock_path.exists() && fs::remove_file(&lock_path).is_ok() {
                Some(format!("{lock_file} (lock file)"))
            } else {
                None
            }
        }))
        .collect();

    let locks_removed = !lock_files
        .iter()
        .filter(|lock_file| {
            let lock_path = git_dir.join(lock_file);
            lock_path.exists() && fs::remove_file(&lock_path).is_ok()
        })
        .count()
        == 0;

    Ok(CleanupResult {
        cleaned_paths,
        locks_removed,
    })
}

/// Validate a Git state file for corruption.
///
/// Checks if a state file is valid before attempting to remove it.
/// This prevents accidental removal of valid in-progress operations.
///
/// # Arguments
///
/// * `path` - Path to the state file to validate
///
/// # Returns
///
/// Returns `Ok(true)` if the state appears valid, `Ok(false)` if invalid,
/// or an error if validation failed.
#[cfg(any(test, feature = "test-utils"))]
fn validate_state_file(path: &Path) -> io::Result<bool> {
    use std::fs;

    if !path.exists() {
        return Ok(false);
    }

    // For directories, check if they contain required files
    if path.is_dir() {
        // A valid rebase directory should have at least some files
        let entries = fs::read_dir(path)?;
        let has_content = entries.count() > 0;
        return Ok(has_content);
    }

    // For files, check if they're readable and non-empty
    if path.is_file() {
        let metadata = fs::metadata(path)?;
        if metadata.len() == 0 {
            // Empty state file is invalid
            return Ok(false);
        }
        // Try to read a small amount to verify file integrity
        let _ = fs::read(path)?;
        return Ok(true);
    }

    Ok(false)
}

/// Attempt automatic recovery from a rebase failure.
///
/// This function implements an escalation strategy for recovering from
/// rebase failures, trying multiple approaches before giving up:
///
/// **Level 1 - Clean state retry**: Reset to clean state and retry
/// **Level 2 - Lock file removal**: Remove stale lock files
/// **Level 3 - Abort and restart**: Abort current rebase and restart from checkpoint
///
/// # Arguments
///
/// * `executor` - Process executor for dependency injection
/// * `error_kind` - The error that occurred
/// * `phase` - The current rebase phase
/// * `phase_error_count` - Number of errors in the current phase
///
/// # Returns
///
/// Returns `Ok(true)` if automatic recovery succeeded and operation can continue,
/// `Ok(false)` if recovery was attempted but operation should still abort,
/// or an error if recovery itself failed.
///
/// # Errors
///
/// Returns an error if recovery operations fail.
#[cfg(any(test, feature = "test-utils"))]
pub fn attempt_automatic_recovery(
    executor: &dyn crate::executor::ProcessExecutor,
    error_kind: &RebaseErrorKind,
    phase: &crate::git_helpers::rebase_checkpoint::RebasePhase,
    phase_error_count: u32,
) -> io::Result<bool> {
    let repo_root = std::env::current_dir()?;
    attempt_automatic_recovery_at(&repo_root, executor, error_kind, phase, phase_error_count)
}

/// Attempt automatic recovery from a rebase failure using explicit repo root.
///
/// This is the explicit path variant that should be used in tests and
/// production code where the repository path is known.
#[cfg(any(test, feature = "test-utils"))]
pub fn attempt_automatic_recovery_at(
    repo_root: &std::path::Path,
    executor: &dyn crate::executor::ProcessExecutor,
    error_kind: &RebaseErrorKind,
    phase: &crate::git_helpers::rebase_checkpoint::RebasePhase,
    phase_error_count: u32,
) -> io::Result<bool> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    attempt_automatic_recovery_impl(&repo, executor, error_kind, phase, phase_error_count)
}

#[cfg(any(test, feature = "test-utils"))]
fn attempt_automatic_recovery_impl(
    repo: &git2::Repository,
    executor: &dyn crate::executor::ProcessExecutor,
    error_kind: &RebaseErrorKind,
    phase: &crate::git_helpers::rebase_checkpoint::RebasePhase,
    phase_error_count: u32,
) -> io::Result<bool> {
    // Don't attempt recovery for fatal errors
    match error_kind {
        RebaseErrorKind::InvalidRevision { .. }
        | RebaseErrorKind::DirtyWorkingTree
        | RebaseErrorKind::RepositoryCorrupt { .. }
        | RebaseErrorKind::EnvironmentFailure { .. }
        | RebaseErrorKind::HookRejection { .. }
        | RebaseErrorKind::InteractiveStop { .. }
        | RebaseErrorKind::Unknown { .. } => {
            return Ok(false);
        }
        _ => {}
    }

    let max_attempts = phase.max_recovery_attempts();
    if phase_error_count >= max_attempts {
        return Ok(false);
    }

    // Level 1: Try cleaning stale state
    if cleanup_stale_rebase_state_impl(repo).is_ok() {
        // If we cleaned something, try to validate the repo is in a good state
        if validate_git_state_impl(repo).is_ok() {
            return Ok(true);
        }
    }

    // Level 2: Try removing lock files
    let git_dir = repo.path();
    let lock_files = ["index.lock", "packed-refs.lock", "HEAD.lock"];
    let removed_any = lock_files.iter().any(|lock_file| {
        let lock_path = git_dir.join(lock_file);
        lock_path.exists() && std::fs::remove_file(&lock_path).is_ok()
    });

    if removed_any && validate_git_state_impl(repo).is_ok() {
        return Ok(true);
    }

    // Level 3: For concurrent operations, try to abort the in-progress operation
    if let RebaseErrorKind::ConcurrentOperation { .. } = error_kind {
        // Try git rebase --abort via executor
        let abort_result = executor.execute("git", &["rebase", "--abort"], &[], None);

        if abort_result.is_ok() {
            // Check if state is now clean
            if validate_git_state_impl(repo).is_ok() {
                return Ok(true);
            }
        }
    }

    // Recovery attempts exhausted or failed
    Ok(false)
}

/// Validate the overall Git repository state for corruption.
///
/// Performs comprehensive checks on the repository to detect
/// corrupted state files, missing objects, or other integrity issues.
///
/// # Returns
///
/// Returns `Ok(())` if the repository state appears valid,
/// or an error describing the validation failure.
#[cfg(any(test, feature = "test-utils"))]
pub fn validate_git_state() -> io::Result<()> {
    let repo_root = std::env::current_dir()?;
    validate_git_state_at(&repo_root)
}

/// Validate the overall Git repository state for corruption using explicit repo root.
///
/// This is the explicit path variant that should be used in tests and
/// production code where the repository path is known.
#[cfg(any(test, feature = "test-utils"))]
pub fn validate_git_state_at(repo_root: &std::path::Path) -> io::Result<()> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    validate_git_state_impl(&repo)
}

#[cfg(any(test, feature = "test-utils"))]
fn validate_git_state_impl(repo: &git2::Repository) -> io::Result<()> {
    // Check if the repository head is valid
    let _ = repo.head().map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Repository HEAD is invalid: {e}"),
        )
    })?;

    // Try to access the index to verify it's not corrupted
    let _ = repo.index().map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Repository index is corrupted: {e}"),
        )
    })?;

    // Check for object database integrity by trying to access HEAD
    if let Ok(head) = repo.head() {
        if let Ok(commit) = head.peel_to_commit() {
            // Verify the commit tree is accessible
            let _ = commit.tree().map_err(|e| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("Object database corruption: {e}"),
                )
            })?;
        }
    }

    Ok(())
}

/// Detect dirty working tree using Git CLI.
///
/// This is a fallback function that uses Git CLI to detect dirty state
/// when libgit2 detection may not be sufficient.
///
/// # Arguments
///
/// * `executor` - Process executor for dependency injection
///
/// # Returns
///
/// Returns `Ok(true)` if the working tree is dirty, `Ok(false)` otherwise.
///
/// # Errors
///
/// Returns an error if the git command fails to execute.
#[cfg(any(test, feature = "test-utils"))]
pub fn is_dirty_tree_cli(executor: &dyn crate::executor::ProcessExecutor) -> io::Result<bool> {
    let output = executor.execute("git", &["status", "--porcelain"], &[], None)?;

    if output.status.success() {
        let stdout = output.stdout.trim();
        Ok(!stdout.is_empty())
    } else {
        Err(io::Error::other(format!(
            "Failed to check working tree status: {}",
            output.stderr
        )))
    }
}
