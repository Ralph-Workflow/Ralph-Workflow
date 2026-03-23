//! Rebase state machine for fault-tolerant rebase operations.
//!
//! This module provides a state machine that manages rebase operations
//! with checkpoint-based recovery. It tracks the current phase of a rebase
//! operation and can resume from interruptions.

#![deny(unsafe_code)]

use std::io;
use std::path::Path;

use crate::workspace::{Workspace, WorkspaceFs};

use super::rebase_checkpoint::{
    clear_rebase_checkpoint, load_rebase_checkpoint, rebase_checkpoint_exists,
    save_rebase_checkpoint, RebaseCheckpoint, RebasePhase,
};

// =============================================================================
// States (from rebase_state_machine/states.rs)
// =============================================================================

const DEFAULT_MAX_RECOVERY_ATTEMPTS: u32 = 3;

pub struct RebaseStateMachine {
    checkpoint: RebaseCheckpoint,
    max_recovery_attempts: u32,
}

impl RebaseStateMachine {
    #[must_use]
    pub fn new(upstream_branch: String) -> Self {
        Self {
            checkpoint: RebaseCheckpoint::new(upstream_branch),
            max_recovery_attempts: DEFAULT_MAX_RECOVERY_ATTEMPTS,
        }
    }

    #[expect(clippy::print_stderr, reason = "recovery warning messages")]
    pub fn load_or_create(upstream_branch: String) -> io::Result<Self> {
        if rebase_checkpoint_exists() {
            match load_rebase_checkpoint() {
                Ok(Some(checkpoint)) => Ok(Self {
                    checkpoint,
                    max_recovery_attempts: DEFAULT_MAX_RECOVERY_ATTEMPTS,
                }),
                Ok(None) => Self::try_load_backup_or_create(upstream_branch),
                Err(e) => {
                    eprintln!("Warning: Failed to load checkpoint: {e}. Attempting recovery...");

                    match Self::try_load_backup_or_create(upstream_branch) {
                        Ok(sm) => {
                            let _ = clear_rebase_checkpoint();
                            Ok(sm)
                        }
                        Err(backup_err) => Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            format!(
                                "Failed to load checkpoint ({e}) and backup ({backup_err}). \
                                 Manual intervention may be required."
                            ),
                        )),
                    }
                }
            }
        } else {
            Ok(Self::new(upstream_branch))
        }
    }

    fn try_load_backup_or_create(upstream_branch: String) -> io::Result<Self> {
        let workspace = WorkspaceFs::new(std::env::current_dir()?);
        Ok(Self::try_load_backup_or_create_with_workspace(
            &workspace,
            upstream_branch,
        ))
    }

    fn try_load_backup_or_create_with_workspace(
        workspace: &dyn Workspace,
        upstream_branch: String,
    ) -> Self {
        use super::rebase_checkpoint::rebase_checkpoint_backup_path;

        let backup_path_str = rebase_checkpoint_backup_path();
        let backup_path = Path::new(&backup_path_str);

        if workspace.exists(backup_path) {
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

        eprintln!("Creating fresh state machine (checkpoint data lost)");
        Self::new(upstream_branch)
    }

    #[must_use]
    pub const fn with_max_recovery_attempts(mut self, max: u32) -> Self {
        self.max_recovery_attempts = max;
        self
    }

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

    pub fn record_conflict(mut self, file: String) -> Self {
        self.checkpoint = self.checkpoint.clone().with_conflicted_file(file);
        self
    }

    pub fn record_resolution(mut self, file: String) -> Self {
        self.checkpoint = self.checkpoint.clone().with_resolved_file(file);
        self
    }

    pub fn record_error(mut self, error: String) -> Self {
        self.checkpoint = self.checkpoint.clone().with_error(error);
        self
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn can_recover(&self) -> bool {
        let max_for_phase = self.checkpoint.phase.max_recovery_attempts();
        self.checkpoint.phase_error_count < max_for_phase
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn should_abort(&self) -> bool {
        let max_for_phase = self.checkpoint.phase.max_recovery_attempts();
        self.checkpoint.phase_error_count >= max_for_phase
    }

    #[must_use]
    pub fn all_conflicts_resolved(&self) -> bool {
        self.checkpoint.all_conflicts_resolved()
    }

    #[must_use]
    pub const fn checkpoint(&self) -> &RebaseCheckpoint {
        &self.checkpoint
    }

    #[must_use]
    pub const fn phase(&self) -> &RebasePhase {
        &self.checkpoint.phase
    }

    #[must_use]
    pub fn upstream_branch(&self) -> &str {
        &self.checkpoint.upstream_branch
    }

    #[must_use]
    pub fn unresolved_conflict_count(&self) -> usize {
        self.checkpoint.unresolved_conflict_count()
    }

    pub fn clear_checkpoint(self) -> io::Result<()> {
        clear_rebase_checkpoint()
    }

    #[cfg(any(test, feature = "test-utils"))]
    pub fn abort(self) -> io::Result<()> {
        let checkpoint = self
            .checkpoint
            .clone()
            .with_phase(RebasePhase::RebaseAborted);
        save_rebase_checkpoint(&checkpoint)
    }
}

#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecoveryAction {
    Continue,
    Retry,
    Abort,
    Skip,
}

#[cfg(any(test, feature = "test-utils"))]
impl RecoveryAction {
    #[must_use]
    pub const fn decide(
        error_kind: &crate::git_helpers::rebase::RebaseErrorKind,
        error_count: u32,
        max_attempts: u32,
    ) -> Self {
        if error_count >= max_attempts {
            return Self::Abort;
        }

        match error_kind {
            crate::git_helpers::rebase::RebaseErrorKind::InvalidRevision { .. } => Self::Abort,
            crate::git_helpers::rebase::RebaseErrorKind::DirtyWorkingTree => Self::Abort,
            crate::git_helpers::rebase::RebaseErrorKind::ConcurrentOperation { .. } => Self::Retry,
            crate::git_helpers::rebase::RebaseErrorKind::RepositoryCorrupt { .. } => Self::Abort,
            crate::git_helpers::rebase::RebaseErrorKind::EnvironmentFailure { .. } => Self::Abort,
            crate::git_helpers::rebase::RebaseErrorKind::HookRejection { .. } => Self::Abort,
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
            #[cfg(any(test, feature = "test-utils"))]
            crate::git_helpers::rebase::RebaseErrorKind::ValidationFailed { .. } => Self::Abort,
            #[cfg(any(test, feature = "test-utils"))]
            crate::git_helpers::rebase::RebaseErrorKind::ProcessTerminated { .. } => Self::Continue,
            #[cfg(any(test, feature = "test-utils"))]
            crate::git_helpers::rebase::RebaseErrorKind::InconsistentState { .. } => Self::Retry,
            crate::git_helpers::rebase::RebaseErrorKind::Unknown { .. } => Self::Abort,
        }
    }
}

// =============================================================================
// Transitions (from rebase_state_machine/transitions.rs)
// =============================================================================

use crate::git_helpers::lock::{acquire_rebase_lock, release_rebase_lock};

pub struct RebaseLock {
    owns_lock: bool,
}

impl Drop for RebaseLock {
    fn drop(&mut self) {
        if self.owns_lock {
            let _ = release_rebase_lock();
        }
    }
}

impl RebaseLock {
    pub fn new() -> std::io::Result<Self> {
        acquire_rebase_lock()?;
        Ok(Self { owns_lock: true })
    }

    #[must_use]
    #[cfg(any(test, feature = "test-utils"))]
    pub fn leak(mut self) -> bool {
        let owned = self.owns_lock;
        self.owns_lock = false;
        owned
    }
}

// =============================================================================
// Tests (from rebase_state_machine/tests.rs)
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_state_machine_new() {
        let machine = RebaseStateMachine::new("main".to_string());
        assert_eq!(machine.phase(), &RebasePhase::NotStarted);
        assert_eq!(machine.upstream_branch(), "main");
        assert!(machine.can_recover());
        assert!(!machine.should_abort());
    }

    #[test]
    fn test_state_machine_record_conflict() {
        let machine = RebaseStateMachine::new("main".to_string())
            .record_conflict("file1.rs".to_string())
            .record_conflict("file2.rs".to_string());
        assert_eq!(machine.unresolved_conflict_count(), 2);
    }

    #[test]
    fn test_state_machine_record_resolution() {
        let machine = RebaseStateMachine::new("main".to_string())
            .record_conflict("file1.rs".to_string())
            .record_conflict("file2.rs".to_string());
        assert_eq!(machine.unresolved_conflict_count(), 2);

        let machine = machine.record_resolution("file1.rs".to_string());
        assert_eq!(machine.unresolved_conflict_count(), 1);
        assert!(!machine.all_conflicts_resolved());

        let machine = machine.record_resolution("file2.rs".to_string());
        assert_eq!(machine.unresolved_conflict_count(), 0);
        assert!(machine.all_conflicts_resolved());
    }

    #[test]
    fn test_state_machine_record_error() {
        let machine = RebaseStateMachine::new("main".to_string());
        assert!(machine.can_recover());
        assert!(!machine.should_abort());

        let machine = machine.record_error("First error".to_string());
        assert!(machine.can_recover());

        let machine = machine.record_error("Second error".to_string());
        assert!(machine.can_recover());

        let machine = machine.record_error("Third error".to_string());
        assert!(!machine.can_recover());
        assert!(machine.should_abort());
    }

    #[test]
    fn test_state_machine_custom_max_attempts() {
        let machine = RebaseStateMachine::new("main".to_string()).with_max_recovery_attempts(1);
        assert!(machine.can_recover());
    }

    #[test]
    fn test_recovery_action_variants_exist() {
        let _ = RecoveryAction::Continue;
        let _ = RecoveryAction::Retry;
        let _ = RecoveryAction::Abort;
        let _ = RecoveryAction::Skip;
    }

    #[test]
    fn test_recovery_action_decide_content_conflict() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::ContentConflict {
            files: vec!["file1.rs".to_string()],
        };

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Continue);

        let action = RecoveryAction::decide(&error, 2, 3);
        assert_eq!(action, RecoveryAction::Continue);

        let action = RecoveryAction::decide(&error, 3, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_concurrent_operation() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::ConcurrentOperation {
            operation: "rebase".to_string(),
        };

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Retry);

        let action = RecoveryAction::decide(&error, 2, 3);
        assert_eq!(action, RecoveryAction::Retry);

        let action = RecoveryAction::decide(&error, 3, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_invalid_revision() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::InvalidRevision {
            revision: "nonexistent".to_string(),
        };

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_dirty_working_tree() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::DirtyWorkingTree;

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_empty_commit() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::EmptyCommit;

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Skip);

        let action = RecoveryAction::decide(&error, 5, 10);
        assert_eq!(action, RecoveryAction::Skip);
    }

    #[test]
    fn test_recovery_action_decide_process_terminated() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::ProcessTerminated {
            reason: "agent crashed".to_string(),
        };

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Continue);
    }

    #[test]
    fn test_recovery_action_decide_inconsistent_state() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::InconsistentState {
            details: "HEAD detached unexpectedly".to_string(),
        };

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Retry);

        let action = RecoveryAction::decide(&error, 2, 3);
        assert_eq!(action, RecoveryAction::Retry);

        let action = RecoveryAction::decide(&error, 3, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_patch_application_failed() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::PatchApplicationFailed {
            reason: "context mismatch".to_string(),
        };

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Retry);
    }

    #[test]
    fn test_recovery_action_decide_validation_failed() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::ValidationFailed {
            reason: "tests failed".to_string(),
        };

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_unknown() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::Unknown {
            details: "something went wrong".to_string(),
        };

        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_max_attempts_exceeded() {
        use super::super::rebase::RebaseErrorKind;

        let retryable_errors = [
            RebaseErrorKind::ConcurrentOperation {
                operation: "merge".to_string(),
            },
            RebaseErrorKind::PatchApplicationFailed {
                reason: "fuzz failure".to_string(),
            },
            RebaseErrorKind::AutostashFailed {
                reason: "stash pop failed".to_string(),
            },
        ];

        for error in retryable_errors {
            let action = RecoveryAction::decide(&error, 5, 3);
            assert_eq!(action, RecoveryAction::Abort);
        }
    }

    #[test]
    fn test_recovery_action_decide_category_1_non_recoverable() {
        use super::super::rebase::RebaseErrorKind;

        let non_recoverable_errors = [
            RebaseErrorKind::InvalidRevision {
                revision: "bad-ref".to_string(),
            },
            RebaseErrorKind::RepositoryCorrupt {
                details: "missing objects".to_string(),
            },
            RebaseErrorKind::EnvironmentFailure {
                reason: "no editor configured".to_string(),
            },
            RebaseErrorKind::HookRejection {
                hook_name: "pre-rebase".to_string(),
            },
        ];

        for error in non_recoverable_errors {
            let action = RecoveryAction::decide(&error, 0, 3);
            assert_eq!(action, RecoveryAction::Abort);
        }
    }

    #[test]
    fn test_recovery_action_decide_category_2_mixed() {
        use super::super::rebase::RebaseErrorKind;

        let interactive = RebaseErrorKind::InteractiveStop {
            command: "edit".to_string(),
        };
        assert_eq!(
            RecoveryAction::decide(&interactive, 0, 3),
            RecoveryAction::Abort
        );

        let ref_fail = RebaseErrorKind::ReferenceUpdateFailed {
            reason: "concurrent update".to_string(),
        };
        assert_eq!(
            RecoveryAction::decide(&ref_fail, 0, 3),
            RecoveryAction::Retry
        );

        let commit_fail = RebaseErrorKind::CommitCreationFailed {
            reason: "hook failed".to_string(),
        };
        assert_eq!(
            RecoveryAction::decide(&commit_fail, 0, 3),
            RecoveryAction::Retry
        );
    }
}
