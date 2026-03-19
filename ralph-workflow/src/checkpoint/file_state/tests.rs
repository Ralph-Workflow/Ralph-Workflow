mod tests {
    use super::*;

    // =========================================================================
    // Workspace-based tests (for testability without real filesystem)
    // =========================================================================

    #[cfg(feature = "test-utils")]
    mod workspace_tests {
        use super::*;
        use crate::workspace::MemoryWorkspace;

        #[test]
        fn test_file_system_state_new() {
            let state = FileSystemState::new();
            assert!(state.files.is_empty());
            assert!(state.git_head_oid.is_none());
            assert!(state.git_branch.is_none());
        }

        #[test]
        fn test_capture_file_with_workspace() {
            let workspace = MemoryWorkspace::new_test().with_file("test.txt", "content");

            let state = FileSystemState::new();
            state.capture_file_with_workspace(&workspace, "test.txt");

            assert!(state.files.contains_key("test.txt"));
            let snapshot = &state.files["test.txt"];
            assert!(snapshot.exists);
            assert_eq!(snapshot.size, 7);
        }

        #[test]
        fn test_capture_file_with_workspace_nonexistent() {
            let workspace = MemoryWorkspace::new_test();

            let state = FileSystemState::new();
            state.capture_file_with_workspace(&workspace, "nonexistent.txt");

            assert!(state.files.contains_key("nonexistent.txt"));
            let snapshot = &state.files["nonexistent.txt"];
            assert!(!snapshot.exists);
            assert_eq!(snapshot.size, 0);
        }

        #[test]
        fn test_validate_with_workspace_success() {
            let workspace = MemoryWorkspace::new_test().with_file("test.txt", "content");

            let state = FileSystemState::new();
            state.capture_file_with_workspace(&workspace, "test.txt");

            let errors = state.validate_with_workspace(&workspace, None);
            assert!(errors.is_empty());
        }

        #[test]
        fn test_validate_with_workspace_file_missing() {
            // Create workspace with file, capture state
            let workspace_with_file = MemoryWorkspace::new_test().with_file("test.txt", "content");
            let state = FileSystemState::new();
            state.capture_file_with_workspace(&workspace_with_file, "test.txt");

            // Create new workspace without the file (simulating file deletion)
            let workspace_without_file = MemoryWorkspace::new_test();

            // Validation should fail because file is missing
            let errors = state.validate_with_workspace(&workspace_without_file, None);
            assert!(!errors.is_empty());
            assert!(matches!(errors[0], ValidationError::FileMissing { .. }));
        }

        #[test]
        fn test_validate_with_workspace_file_changed() {
            // Create workspace with original file
            let workspace_original = MemoryWorkspace::new_test().with_file("test.txt", "content");
            let state = FileSystemState::new();
            state.capture_file_with_workspace(&workspace_original, "test.txt");

            // Create new workspace with modified content
            let workspace_modified = MemoryWorkspace::new_test().with_file("test.txt", "modified");

            let errors = state.validate_with_workspace(&workspace_modified, None);
            assert!(!errors.is_empty());
            assert!(matches!(
                errors[0],
                ValidationError::FileContentChanged { .. }
            ));
        }

        #[test]
        fn test_validate_with_workspace_file_unexpectedly_exists() {
            // Create state with non-existent file
            let workspace_empty = MemoryWorkspace::new_test();
            let state = FileSystemState::new();
            state.capture_file_with_workspace(&workspace_empty, "test.txt");

            // Create new workspace with the file (simulating unexpected file creation)
            let workspace_with_file = MemoryWorkspace::new_test().with_file("test.txt", "content");

            let errors = state.validate_with_workspace(&workspace_with_file, None);
            assert!(!errors.is_empty());
            assert!(matches!(
                errors[0],
                ValidationError::FileUnexpectedlyExists { .. }
            ));
        }
    }

    // =========================================================================
    // Interrupt-skipping tests
    // =========================================================================

    #[cfg(feature = "test-utils")]
    mod interrupt_tests {
        use super::*;
        use crate::executor::MockProcessExecutor;
        use crate::interrupt::{
            request_user_interrupt, reset_user_interrupted_occurred, take_user_interrupt_request,
        };
        use crate::workspace::MemoryWorkspace;

        /// Ensure `capture_git_state` is skipped when a user interrupt is pending.
        ///
        /// If interrupted, we must NOT block on `executor.execute("git", ...)` calls because
        /// those calls can hang indefinitely after a SIGTERM-killed agent leaves orphaned
        /// processes holding pipe write ends, or after git processes cannot acquire locks.
        #[test]
        fn capture_with_workspace_skips_git_state_when_interrupted() {
            // The interrupt flags are process-global; coordinate all test access so
            // parallel tests can't steal each other's pending interrupt requests.
            let _lock = crate::interrupt::interrupt_test_lock();

            // Guarantee clean state: clear any interrupt flag left from other tests
            take_user_interrupt_request();
            reset_user_interrupted_occurred();

            let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "# task");
            let executor = MockProcessExecutor::new();

            // Signal a user interrupt BEFORE capturing
            request_user_interrupt();

            let _state = FileSystemState::capture_with_workspace(&workspace, &executor);

            // Clean up the interrupt flags so other tests aren't affected
            take_user_interrupt_request();
            reset_user_interrupted_occurred();

            // No git commands should have been executed
            let git_calls: Vec<_> = executor
                .execute_calls()
                .into_iter()
                .filter(|(cmd, _, _, _)| cmd == "git")
                .collect();
            assert!(
                git_calls.is_empty(),
                "capture_with_workspace must not call git when a user interrupt is pending; \
                got {} git call(s): {:?}",
                git_calls.len(),
                git_calls
            );
        }
    }

    // =========================================================================
    // Pure unit tests (no filesystem access)
    // =========================================================================

    #[test]
    fn test_validation_error_display() {
        let err = ValidationError::FileMissing {
            path: "test.txt".to_string(),
        };
        assert_eq!(err.to_string(), "File missing: test.txt");

        let err = ValidationError::FileContentChanged {
            path: "test.txt".to_string(),
        };
        assert_eq!(err.to_string(), "File content changed: test.txt");
    }

    #[test]
    fn test_validation_error_recovery_suggestion() {
        let err = ValidationError::FileMissing {
            path: "test.txt".to_string(),
        };
        let (problem, commands) = err.recovery_commands();
        assert!(problem.contains("test.txt"));
        assert!(!commands.is_empty());

        let err = ValidationError::GitHeadChanged {
            expected: "abc123".to_string(),
            actual: "def456".to_string(),
        };
        let (problem, commands) = err.recovery_commands();
        assert!(problem.contains("abc123"));
        assert!(commands.iter().any(|c| c.contains("git reset")));
    }

    #[test]
    fn test_validation_error_recovery_commands_file_missing() {
        let err = ValidationError::FileMissing {
            path: "PROMPT.md".to_string(),
        };
        let (problem, commands) = err.recovery_commands();

        assert!(problem.contains("missing"));
        assert!(problem.contains("PROMPT.md"));
        assert!(!commands.is_empty());
        assert!(commands.iter().any(|c| c.contains("find")));
    }

    #[test]
    fn test_validation_error_recovery_commands_git_head_changed() {
        let err = ValidationError::GitHeadChanged {
            expected: "abc123".to_string(),
            actual: "def456".to_string(),
        };
        let (problem, commands) = err.recovery_commands();

        assert!(problem.contains("changed"));
        assert!(problem.contains("abc123"));
        assert!(problem.contains("def456"));
        assert!(!commands.is_empty());
        assert!(commands.iter().any(|c| c.contains("git reset")));
        assert!(commands.iter().any(|c| c.contains("git log")));
    }

    #[test]
    fn test_validation_error_recovery_commands_working_tree_changed() {
        let err = ValidationError::GitWorkingTreeChanged {
            changes: "M file1.txt\nM file2.txt".to_string(),
        };
        let (problem, commands) = err.recovery_commands();

        assert!(problem.contains("uncommitted changes"));
        assert!(!commands.is_empty());
        assert!(commands.iter().any(|c| c.contains("git status")));
        assert!(commands.iter().any(|c| c.contains("git stash")));
        assert!(commands.iter().any(|c| c.contains("git commit")));
    }

    #[test]
    fn test_validation_error_recovery_commands_git_state_invalid() {
        let err = ValidationError::GitStateInvalid {
            reason: "detached HEAD state".to_string(),
        };
        let (problem, commands) = err.recovery_commands();

        assert!(problem.contains("detached HEAD state"));
        assert!(!commands.is_empty());
        assert!(commands.iter().any(|c| c.contains("git checkout")));
    }

    #[test]
    fn test_validation_error_recovery_commands_file_content_changed() {
        let err = ValidationError::FileContentChanged {
            path: "PROMPT.md".to_string(),
        };
        let (problem, commands) = err.recovery_commands();

        assert!(problem.contains("changed"));
        assert!(problem.contains("PROMPT.md"));
        assert!(!commands.is_empty());
        assert!(commands.iter().any(|c| c.contains("git diff")));
    }
}
