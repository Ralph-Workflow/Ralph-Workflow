// Error type definitions, classification, and parsing for rebase operations.
//
// This file contains:
// - RebaseErrorKind enum with all failure mode categories
// - RebaseResult enum for operation outcomes
// - Error classification functions for Git CLI output parsing
// - Helper functions for extracting information from error messages

/// Detailed classification of rebase failure modes.
///
/// This enum categorizes all known Git rebase failure modes as documented
/// in the requirements. Each variant represents a specific category of
/// failure that may occur during a rebase operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RebaseErrorKind {
    // Category 1: Rebase Cannot Start
    /// Invalid or unresolvable revisions (branch doesn't exist, invalid ref, etc.)
    InvalidRevision { revision: String },

    /// Dirty working tree or index (unstaged or staged changes present)
    DirtyWorkingTree,

    /// Concurrent or in-progress Git operations (rebase, merge, cherry-pick, etc.)
    ConcurrentOperation { operation: String },

    /// Repository integrity or storage failures (missing/corrupt objects, disk full, etc.)
    RepositoryCorrupt { details: String },

    /// Environment or configuration failures (missing user.name, editor unavailable, etc.)
    EnvironmentFailure { reason: String },

    /// Hook-triggered aborts (pre-rebase hook rejected the operation)
    HookRejection { hook_name: String },

    // Category 2: Rebase Stops (Interrupted)
    /// Content conflicts (textual merge conflicts, add/add, modify/delete, etc.)
    ContentConflict { files: Vec<String> },

    /// Patch application failures (patch does not apply, context mismatch, etc.)
    PatchApplicationFailed { reason: String },

    /// Interactive todo-driven stops (edit, reword, break, exec commands)
    InteractiveStop { command: String },

    /// Empty or redundant commits (patch results in no changes)
    EmptyCommit,

    /// Autostash and stash reapplication failures
    AutostashFailed { reason: String },

    /// Commit creation failures mid-rebase (hook failures, signing failures, etc.)
    CommitCreationFailed { reason: String },

    /// Reference update failures (cannot lock branch ref, concurrent ref update, etc.)
    ReferenceUpdateFailed { reason: String },

    // Category 3: Post-Rebase Failures
    /// Post-rebase validation failures (tests failing, build failures, etc.)
    #[cfg(any(test, feature = "test-utils"))]
    ValidationFailed { reason: String },

    // Category 4: Interrupted/Corrupted State
    /// Process termination (agent crash, OS kill signal, CI timeout, etc.)
    #[cfg(any(test, feature = "test-utils"))]
    ProcessTerminated { reason: String },

    /// Incomplete or inconsistent rebase metadata
    #[cfg(any(test, feature = "test-utils"))]
    InconsistentState { details: String },

    // Category 5: Unknown
    /// Undefined or unknown failure modes
    Unknown { details: String },
}

impl RebaseErrorKind {
    /// Returns a human-readable description of the error.
    #[must_use]
    pub fn description(&self) -> String {
        describe_rebase_error_kind(self)
    }
}

fn describe_invalid_revision(revision: &str) -> String {
    format!("Invalid or unresolvable revision: '{revision}'")
}

fn describe_dirty_working_tree() -> String {
    "Working tree has uncommitted changes".to_string()
}

fn describe_concurrent_operation(operation: &str) -> String {
    format!("Concurrent Git operation in progress: {operation}")
}

fn describe_repository_corrupt(details: &str) -> String {
    format!("Repository integrity issue: {details}")
}

fn describe_environment_failure(reason: &str) -> String {
    format!("Environment or configuration failure: {reason}")
}

fn describe_hook_rejection(hook_name: &str) -> String {
    format!("Hook '{hook_name}' rejected the operation")
}

fn describe_content_conflict(file_count: usize) -> String {
    format!("Merge conflicts in {file_count} file(s)",)
}

fn describe_patch_application_failed(reason: &str) -> String {
    format!("Patch application failed: {reason}")
}

fn describe_interactive_stop(command: &str) -> String {
    format!("Interactive rebase stopped at command: {command}")
}

fn describe_empty_commit() -> String {
    "Empty or redundant commit".to_string()
}

fn describe_autostash_failed(reason: &str) -> String {
    format!("Autostash failed: {reason}")
}

fn describe_commit_creation_failed(reason: &str) -> String {
    format!("Commit creation failed: {reason}")
}

fn describe_reference_update_failed(reason: &str) -> String {
    format!("Reference update failed: {reason}")
}

#[cfg(any(test, feature = "test-utils"))]
fn describe_validation_failed(reason: &str) -> String {
    format!("Post-rebase validation failed: {reason}")
}

#[cfg(any(test, feature = "test-utils"))]
fn describe_process_terminated(reason: &str) -> String {
    format!("Rebase process terminated: {reason}")
}

#[cfg(any(test, feature = "test-utils"))]
fn describe_inconsistent_state(details: &str) -> String {
    format!("Inconsistent rebase state: {details}")
}

fn describe_unknown(details: &str) -> String {
    format!("Unknown rebase error: {details}")
}

fn describe_rebase_error_kind(kind: &RebaseErrorKind) -> String {
    match kind {
        RebaseErrorKind::InvalidRevision { revision } => describe_invalid_revision(revision),
        RebaseErrorKind::DirtyWorkingTree => describe_dirty_working_tree(),
        RebaseErrorKind::ConcurrentOperation { operation } => {
            describe_concurrent_operation(operation)
        }
        RebaseErrorKind::RepositoryCorrupt { details } => describe_repository_corrupt(details),
        RebaseErrorKind::EnvironmentFailure { reason } => describe_environment_failure(reason),
        RebaseErrorKind::HookRejection { hook_name } => describe_hook_rejection(hook_name),
        RebaseErrorKind::ContentConflict { files } => describe_content_conflict(files.len()),
        RebaseErrorKind::PatchApplicationFailed { reason } => {
            describe_patch_application_failed(reason)
        }
        RebaseErrorKind::InteractiveStop { command } => describe_interactive_stop(command),
        RebaseErrorKind::EmptyCommit => describe_empty_commit(),
        RebaseErrorKind::AutostashFailed { reason } => describe_autostash_failed(reason),
        RebaseErrorKind::CommitCreationFailed { reason } => describe_commit_creation_failed(reason),
        RebaseErrorKind::ReferenceUpdateFailed { reason } => {
            describe_reference_update_failed(reason)
        }
        #[cfg(any(test, feature = "test-utils"))]
        RebaseErrorKind::ValidationFailed { reason } => describe_validation_failed(reason),
        #[cfg(any(test, feature = "test-utils"))]
        RebaseErrorKind::ProcessTerminated { reason } => describe_process_terminated(reason),
        #[cfg(any(test, feature = "test-utils"))]
        RebaseErrorKind::InconsistentState { details } => describe_inconsistent_state(details),
        RebaseErrorKind::Unknown { details } => describe_unknown(details),
    }
}

impl RebaseErrorKind {
    /// Returns whether this error can potentially be recovered automatically.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn is_recoverable(&self) -> bool {
        match self {
            // These are generally recoverable with automatic retry or cleanup
            Self::ConcurrentOperation { .. } => true,
            #[cfg(any(test, feature = "test-utils"))]
            Self::ProcessTerminated { .. } | Self::InconsistentState { .. } => true,

            // These require manual conflict resolution
            Self::ContentConflict { .. } => true,

            // These are generally not recoverable without manual intervention
            Self::InvalidRevision { .. }
            | Self::DirtyWorkingTree
            | Self::RepositoryCorrupt { .. }
            | Self::EnvironmentFailure { .. }
            | Self::HookRejection { .. }
            | Self::PatchApplicationFailed { .. }
            | Self::InteractiveStop { .. }
            | Self::EmptyCommit
            | Self::AutostashFailed { .. }
            | Self::CommitCreationFailed { .. }
            | Self::ReferenceUpdateFailed { .. } => false,
            #[cfg(any(test, feature = "test-utils"))]
            Self::ValidationFailed { .. } => false,
            Self::Unknown { .. } => false,
        }
    }

    /// Returns the category number (1-5) for this error.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn category(&self) -> u8 {
        match self {
            Self::InvalidRevision { .. }
            | Self::DirtyWorkingTree
            | Self::ConcurrentOperation { .. }
            | Self::RepositoryCorrupt { .. }
            | Self::EnvironmentFailure { .. }
            | Self::HookRejection { .. } => 1,

            Self::ContentConflict { .. }
            | Self::PatchApplicationFailed { .. }
            | Self::InteractiveStop { .. }
            | Self::EmptyCommit
            | Self::AutostashFailed { .. }
            | Self::CommitCreationFailed { .. }
            | Self::ReferenceUpdateFailed { .. } => 2,

            #[cfg(any(test, feature = "test-utils"))]
            Self::ValidationFailed { .. } => 3,

            #[cfg(any(test, feature = "test-utils"))]
            Self::ProcessTerminated { .. } | Self::InconsistentState { .. } => 4,

            Self::Unknown { .. } => 5,
        }
    }
}

impl std::fmt::Display for RebaseErrorKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.description())
    }
}

impl std::error::Error for RebaseErrorKind {}

/// Result of a rebase operation.
///
/// This enum represents the possible outcomes of a rebase operation,
/// including success, conflicts (recoverable), no-op (not applicable),
/// and specific failure modes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RebaseResult {
    /// Rebase completed successfully.
    Success,

    /// Rebase had conflicts that need resolution.
    Conflicts(Vec<String>),

    /// No rebase was needed (already up-to-date, not applicable, etc.).
    NoOp { reason: String },

    /// Rebase failed with a specific error kind.
    Failed(RebaseErrorKind),
}

impl RebaseResult {
    /// Returns whether the rebase was successful.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn is_success(&self) -> bool {
        matches!(self, Self::Success)
    }

    /// Returns whether the rebase had conflicts (needs resolution).
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn has_conflicts(&self) -> bool {
        matches!(self, Self::Conflicts(_))
    }

    /// Returns whether the rebase was a no-op (not applicable).
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn is_noop(&self) -> bool {
        matches!(self, Self::NoOp { .. })
    }

    /// Returns whether the rebase failed.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn is_failed(&self) -> bool {
        matches!(self, Self::Failed(_))
    }

    /// Returns the conflict files if this result contains conflicts.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn conflict_files(&self) -> Option<&[String]> {
        match self {
            Self::Conflicts(files) | Self::Failed(RebaseErrorKind::ContentConflict { files }) => {
                Some(files)
            }
            _ => None,
        }
    }

    /// Returns the error kind if this result is a failure.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn error_kind(&self) -> Option<&RebaseErrorKind> {
        match self {
            Self::Failed(kind) => Some(kind),
            _ => None,
        }
    }

    /// Returns the no-op reason if this result is a no-op.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn noop_reason(&self) -> Option<&str> {
        match self {
            Self::NoOp { reason } => Some(reason),
            _ => None,
        }
    }
}
