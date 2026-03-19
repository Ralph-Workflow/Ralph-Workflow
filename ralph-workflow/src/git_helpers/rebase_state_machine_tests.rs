// Tests for rebase state machine.
//
// This file contains pure unit tests (no filesystem I/O, no test_helpers).
// Tests requiring CWD-relative filesystem ops are in tests/system_tests/rebase_state_machine/.

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

        // Content conflict should always return Continue (to AI resolution)
        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Continue);

        // Even at max attempts, ContentConflict should Continue
        let action = RecoveryAction::decide(&error, 2, 3);
        assert_eq!(action, RecoveryAction::Continue);

        // But if we exceed max attempts, it should Abort
        let action = RecoveryAction::decide(&error, 3, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_concurrent_operation() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::ConcurrentOperation {
            operation: "rebase".to_string(),
        };

        // Concurrent operation should be retried
        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Retry);

        // Should keep retrying until max attempts
        let action = RecoveryAction::decide(&error, 2, 3);
        assert_eq!(action, RecoveryAction::Retry);

        // At max attempts, should abort
        let action = RecoveryAction::decide(&error, 3, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_invalid_revision() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::InvalidRevision {
            revision: "nonexistent".to_string(),
        };

        // Invalid revision should always abort (not recoverable)
        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_dirty_working_tree() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::DirtyWorkingTree;

        // Dirty working tree should always abort (user needs to commit/stash)
        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_empty_commit() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::EmptyCommit;

        // Empty commit should be skipped
        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Skip);

        // Even at high error counts, should still skip
        let action = RecoveryAction::decide(&error, 5, 10);
        assert_eq!(action, RecoveryAction::Skip);
    }

    #[test]
    fn test_recovery_action_decide_process_terminated() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::ProcessTerminated {
            reason: "agent crashed".to_string(),
        };

        // Process termination should continue (recover from checkpoint)
        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Continue);
    }

    #[test]
    fn test_recovery_action_decide_inconsistent_state() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::InconsistentState {
            details: "HEAD detached unexpectedly".to_string(),
        };

        // Inconsistent state should retry (after cleanup)
        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Retry);

        // Should keep retrying until max attempts
        let action = RecoveryAction::decide(&error, 2, 3);
        assert_eq!(action, RecoveryAction::Retry);

        // At max attempts, should abort
        let action = RecoveryAction::decide(&error, 3, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_patch_application_failed() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::PatchApplicationFailed {
            reason: "context mismatch".to_string(),
        };

        // Patch application failure should retry
        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Retry);
    }

    #[test]
    fn test_recovery_action_decide_validation_failed() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::ValidationFailed {
            reason: "tests failed".to_string(),
        };

        // Validation failure should abort (needs manual fix)
        let action = RecoveryAction::decide(&error, 0, 3);
        assert_eq!(action, RecoveryAction::Abort);
    }

    #[test]
    fn test_recovery_action_decide_unknown() {
        use super::super::rebase::RebaseErrorKind;

        let error = RebaseErrorKind::Unknown {
            details: "something went wrong".to_string(),
        };

        // Unknown errors should abort (safe default)
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

        // All retryable errors should abort when max attempts exceeded
        for error in retryable_errors {
            let action = RecoveryAction::decide(&error, 5, 3);
            assert_eq!(
                action,
                RecoveryAction::Abort,
                "Expected Abort for error: {error:?}"
            );
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

        // All these should abort regardless of error count
        for error in non_recoverable_errors {
            let action = RecoveryAction::decide(&error, 0, 3);
            assert_eq!(
                action,
                RecoveryAction::Abort,
                "Expected Abort for error: {error:?}"
            );
        }
    }

    #[test]
    fn test_recovery_action_decide_category_2_mixed() {
        use super::super::rebase::RebaseErrorKind;

        // Interactive stop should abort (manual intervention needed)
        let interactive = RebaseErrorKind::InteractiveStop {
            command: "edit".to_string(),
        };
        assert_eq!(
            RecoveryAction::decide(&interactive, 0, 3),
            RecoveryAction::Abort
        );

        // Reference update failure should retry (transient)
        let ref_fail = RebaseErrorKind::ReferenceUpdateFailed {
            reason: "concurrent update".to_string(),
        };
        assert_eq!(
            RecoveryAction::decide(&ref_fail, 0, 3),
            RecoveryAction::Retry
        );

        // Commit creation failure should retry (transient)
        let commit_fail = RebaseErrorKind::CommitCreationFailed {
            reason: "hook failed".to_string(),
        };
        assert_eq!(
            RecoveryAction::decide(&commit_fail, 0, 3),
            RecoveryAction::Retry
        );
    }
}
