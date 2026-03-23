// State types and definitions for rebase state machine.
//
// This file contains the core state machine struct and RecoveryAction enum.

/// Default maximum number of recovery attempts.
const DEFAULT_MAX_RECOVERY_ATTEMPTS: u32 = 3;

/// State machine for fault-tolerant rebase operations.
///
/// This state machine manages rebase operations with:
/// - Checkpoint-based persistence
/// - Automatic recovery from interruptions
/// - Maximum recovery attempt limits
/// - Conflict tracking
pub struct RebaseStateMachine {
    /// Current checkpoint state
    checkpoint: RebaseCheckpoint,
    /// Maximum number of recovery attempts
    max_recovery_attempts: u32,
}

impl RebaseStateMachine {
    /// Create a new state machine for a rebase operation.
    ///
    /// # Arguments
    ///
    /// * `upstream_branch` - The branch to rebase onto
    #[must_use]
    pub fn new(upstream_branch: String) -> Self {
        Self {
            checkpoint: RebaseCheckpoint::new(upstream_branch),
            max_recovery_attempts: DEFAULT_MAX_RECOVERY_ATTEMPTS,
        }
    }

    /// Load an existing state machine from checkpoint or create a new one.
    ///
    /// If a checkpoint exists, this will resume from that state.
    /// Otherwise, creates a new state machine.
    ///
    /// This method handles corrupted checkpoints by:
    /// - Attempting to load backup checkpoint
    /// - Creating a fresh state if checkpoint is completely corrupted
    ///
    /// # Arguments
    ///
    /// * `upstream_branch` - The branch to rebase onto (used if no checkpoint exists)
    ///
    /// # Returns
    ///
    /// Returns `Ok(state_machine)` if successful, or an error if loading fails.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    #[expect(clippy::print_stderr, reason = "recovery warning messages")]
    pub fn load_or_create(upstream_branch: String) -> io::Result<Self> {
        if rebase_checkpoint_exists() {
            // Try to load the primary checkpoint
            match load_rebase_checkpoint() {
                Ok(Some(checkpoint)) => {
                    // Successfully loaded checkpoint
                    Ok(Self {
                        checkpoint,
                        max_recovery_attempts: DEFAULT_MAX_RECOVERY_ATTEMPTS,
                    })
                }
                Ok(None) => {
                    // Checkpoint file exists but is empty - try backup or create fresh
                    Self::try_load_backup_or_create(upstream_branch)
                }
                Err(e) => {
                    // Checkpoint is corrupted - try backup or create fresh
                    // Log the error but attempt recovery
                    eprintln!("Warning: Failed to load checkpoint: {e}. Attempting recovery...");

                    match Self::try_load_backup_or_create(upstream_branch) {
                        Ok(sm) => {
                            // Backup loaded or fresh state created - clear corrupted checkpoint
                            let _ = clear_rebase_checkpoint();
                            Ok(sm)
                        }
                        Err(backup_err) => {
                            // Even backup failed - return original error with context
                            Err(io::Error::new(
                                io::ErrorKind::InvalidData,
                                format!(
                                    "Failed to load checkpoint ({e}) and backup ({backup_err}). \
                                     Manual intervention may be required."
                                ),
                            ))
                        }
                    }
                }
            }
        } else {
            Ok(Self::new(upstream_branch))
        }
    }

    /// Try to load a backup checkpoint or create a fresh state machine.
    ///
    /// This is called when the primary checkpoint cannot be loaded.
    ///
    /// # Arguments
    ///
    /// * `upstream_branch` - The branch to rebase onto
    ///
    /// # Returns
    ///
    /// Returns `Ok(state_machine)` with either backup loaded or fresh state.
    fn try_load_backup_or_create(upstream_branch: String) -> io::Result<Self> {
        let workspace = WorkspaceFs::new(std::env::current_dir()?);
        Ok(Self::try_load_backup_or_create_with_workspace(
            &workspace,
            upstream_branch,
        ))
    }

    /// Load backup checkpoint or create fresh state using workspace abstraction.
    ///
    /// This is the workspace-aware version for pipeline code.
    fn try_load_backup_or_create_with_workspace(
        workspace: &dyn Workspace,
        upstream_branch: String,
    ) -> Self {
        use super::rebase_checkpoint::rebase_checkpoint_backup_path;

        let backup_path_str = rebase_checkpoint_backup_path();
        let backup_path = Path::new(&backup_path_str);

        // Check if backup exists
        if workspace.exists(backup_path) {
            // Try to load the backup checkpoint directly
            match workspace.read(backup_path) {
                Ok(content) => match serde_json::from_str::<RebaseCheckpoint>(&content) {
                    Ok(checkpoint) => {
                        eprintln!("Successfully recovered from backup checkpoint");
                        return Self {
                            checkpoint,
                            max_recovery_attempts: DEFAULT_MAX_RECOVERY_ATTEMPTS,
                        };
                    }
                    Err(e) => {
                        eprintln!("Backup checkpoint is also corrupted: {e}");
                    }
                },
                Err(e) => {
                    eprintln!("Failed to read backup checkpoint file: {e}");
                }
            }
        }

        // No backup available or backup is corrupted - create fresh state
        eprintln!("Creating fresh state machine (checkpoint data lost)");
        Self::new(upstream_branch)
    }

    /// Set the maximum number of recovery attempts.
    #[must_use]
    pub const fn with_max_recovery_attempts(mut self, max: u32) -> Self {
        self.max_recovery_attempts = max;
        self
    }

    /// Transition to a new phase and save checkpoint.
    ///
    /// # Arguments
    ///
    /// * `phase` - The new phase to transition to
    ///
    /// # Returns
    ///
    /// Returns `Ok(())` if the transition succeeded, or an error if saving failed.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn transition_to(self, phase: RebasePhase) -> (Self, io::Result<()>) {
        let checkpoint = self.checkpoint.clone().with_phase(phase);
        let result = save_rebase_checkpoint(&checkpoint);
        (
            Self {
                checkpoint,
                max_recovery_attempts: self.max_recovery_attempts,
            },
            result,
        )
    }

    /// Record a conflict in a file.
    ///
    /// # Arguments
    ///
    /// * `file` - The file path that has conflicts
    pub fn record_conflict(mut self, file: String) -> Self {
        self.checkpoint = self.checkpoint.clone().with_conflicted_file(file);
        self
    }

    /// Record that a conflict has been resolved.
    ///
    /// # Arguments
    ///
    /// * `file` - The file path that was resolved
    pub fn record_resolution(mut self, file: String) -> Self {
        self.checkpoint = self.checkpoint.clone().with_resolved_file(file);
        self
    }

    /// Record an error that occurred.
    ///
    /// # Arguments
    ///
    /// * `error` - The error message to record
    pub fn record_error(mut self, error: String) -> Self {
        self.checkpoint = self.checkpoint.clone().with_error(error);
        self
    }

    /// Check if recovery is possible.
    ///
    /// Returns `true` if the phase-specific error count is below the maximum
    /// recovery attempts for the current phase.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn can_recover(&self) -> bool {
        let max_for_phase = self.checkpoint.phase.max_recovery_attempts();
        self.checkpoint.phase_error_count < max_for_phase
    }

    /// Check if the rebase should be aborted.
    ///
    /// Returns `true` if the phase-specific error count has exceeded the maximum
    /// recovery attempts for the current phase.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn should_abort(&self) -> bool {
        let max_for_phase = self.checkpoint.phase.max_recovery_attempts();
        self.checkpoint.phase_error_count >= max_for_phase
    }

    /// Check if all conflicts have been resolved.
    ///
    /// Returns `true` if all conflicted files have been marked as resolved.
    #[must_use]
    pub fn all_conflicts_resolved(&self) -> bool {
        self.checkpoint.all_conflicts_resolved()
    }

    /// Get the current checkpoint.
    #[must_use]
    pub const fn checkpoint(&self) -> &RebaseCheckpoint {
        &self.checkpoint
    }

    /// Get the current phase.
    #[must_use]
    pub const fn phase(&self) -> &RebasePhase {
        &self.checkpoint.phase
    }

    /// Get the upstream branch.
    #[must_use]
    pub fn upstream_branch(&self) -> &str {
        &self.checkpoint.upstream_branch
    }

    /// Get the number of unresolved conflicts.
    #[must_use]
    pub fn unresolved_conflict_count(&self) -> usize {
        self.checkpoint.unresolved_conflict_count()
    }

    /// Clear the checkpoint (typically on successful completion).
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn clear_checkpoint(self) -> io::Result<()> {
        clear_rebase_checkpoint()
    }

    /// Force abort and save the aborted state.
    ///
    /// This method consumes the state machine and saves the aborted state.
    /// It's primarily used in tests or for explicit abort scenarios where
    /// you own the state machine.
    ///
    /// # Errors
    ///
    /// Returns an error if saving the checkpoint fails.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn abort(self) -> io::Result<()> {
        let checkpoint = self
            .checkpoint
            .clone()
            .with_phase(RebasePhase::RebaseAborted);
        save_rebase_checkpoint(&checkpoint)
    }
}

/// Actions that can be taken during recovery.
#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecoveryAction {
    /// Continue with the rebase operation.
    ///
    /// Used when the operation can proceed without changes,
    /// such as after resolving conflicts or recovering from a checkpoint.
    Continue,
    /// Retry the current operation.
    ///
    /// Used when transient failures can be overcome by retrying,
    /// such as concurrent operations or stale locks.
    Retry,
    /// Abort the rebase.
    ///
    /// Used when the error cannot be recovered automatically
    /// and requires manual intervention or a full restart.
    Abort,
    /// Skip the current step and proceed.
    ///
    /// Used when the current step can be safely bypassed,
    /// such as for empty commits or `NoOp` scenarios.
    Skip,
}

#[cfg(any(test, feature = "test-utils"))]
impl RecoveryAction {
    /// Decide the appropriate recovery action based on the error and current state.
    ///
    /// # Arguments
    ///
    /// * `error_kind` - The error that occurred
    /// * `error_count` - The number of errors that have occurred so far
    /// * `max_attempts` - The maximum number of recovery attempts allowed
    ///
    /// # Returns
    ///
    /// Returns the appropriate `RecoveryAction` for the given error and state.
    #[must_use]
    pub const fn decide(
        error_kind: &crate::git_helpers::rebase::RebaseErrorKind,
        error_count: u32,
        max_attempts: u32,
    ) -> Self {
        // Check if we've exceeded maximum attempts
        if error_count >= max_attempts {
            return Self::Abort;
        }

        match error_kind {
            // Category 1: Rebase Cannot Start - Generally not recoverable
            crate::git_helpers::rebase::RebaseErrorKind::InvalidRevision { .. } => Self::Abort,
            crate::git_helpers::rebase::RebaseErrorKind::DirtyWorkingTree => Self::Abort,
            crate::git_helpers::rebase::RebaseErrorKind::ConcurrentOperation { .. } => Self::Retry,
            crate::git_helpers::rebase::RebaseErrorKind::RepositoryCorrupt { .. } => Self::Abort,
            crate::git_helpers::rebase::RebaseErrorKind::EnvironmentFailure { .. } => Self::Abort,
            crate::git_helpers::rebase::RebaseErrorKind::HookRejection { .. } => Self::Abort,

            // Category 2: Rebase Stops (Interrupted)
            crate::git_helpers::rebase::RebaseErrorKind::ContentConflict { .. } => Self::Continue,
            crate::git_helpers::rebase::RebaseErrorKind::PatchApplicationFailed { .. } => {
                Self::Retry
            }
            crate::git_helpers::rebase::RebaseErrorKind::InteractiveStop { .. } => Self::Abort,
            crate::git_helpers::rebase::RebaseErrorKind::EmptyCommit => Self::Skip,
            crate::git_helpers::rebase::RebaseErrorKind::AutostashFailed { .. } => Self::Retry,
            crate::git_helpers::rebase::RebaseErrorKind::CommitCreationFailed { .. } => Self::Retry,
            crate::git_helpers::rebase::RebaseErrorKind::ReferenceUpdateFailed { .. } => {
                Self::Retry
            }

            // Category 3: Post-Rebase Failures
            #[cfg(any(test, feature = "test-utils"))]
            crate::git_helpers::rebase::RebaseErrorKind::ValidationFailed { .. } => Self::Abort,

            // Category 4: Interrupted/Corrupted State
            #[cfg(any(test, feature = "test-utils"))]
            crate::git_helpers::rebase::RebaseErrorKind::ProcessTerminated { .. } => Self::Continue,
            #[cfg(any(test, feature = "test-utils"))]
            crate::git_helpers::rebase::RebaseErrorKind::InconsistentState { .. } => Self::Retry,

            // Category 5: Unknown
            crate::git_helpers::rebase::RebaseErrorKind::Unknown { .. } => Self::Abort,
        }
    }
}
