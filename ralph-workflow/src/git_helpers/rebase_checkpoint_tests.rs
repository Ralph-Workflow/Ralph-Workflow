// Tests for rebase checkpoint functionality.
//
// Contains pure unit tests for checkpoint types and workspace-aware variants.
// Tests requiring CWD-relative filesystem I/O are in tests/system_tests/rebase_checkpoint/.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rebase_checkpoint_default() {
        let checkpoint = RebaseCheckpoint::default();
        assert_eq!(checkpoint.phase, RebasePhase::NotStarted);
        assert!(checkpoint.upstream_branch.is_empty());
        assert!(checkpoint.conflicted_files.is_empty());
        assert!(checkpoint.resolved_files.is_empty());
        assert_eq!(checkpoint.error_count, 0);
        assert!(checkpoint.last_error.is_none());
    }

    #[test]
    fn test_rebase_checkpoint_new() {
        let checkpoint = RebaseCheckpoint::new("main".to_string());
        assert_eq!(checkpoint.phase, RebasePhase::NotStarted);
        assert_eq!(checkpoint.upstream_branch, "main");
    }

    #[test]
    fn test_rebase_checkpoint_with_phase() {
        let checkpoint =
            RebaseCheckpoint::new("main".to_string()).with_phase(RebasePhase::RebaseInProgress);
        assert_eq!(checkpoint.phase, RebasePhase::RebaseInProgress);
    }

    #[test]
    fn test_rebase_checkpoint_with_conflicted_file() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_conflicted_file("file1.txt".to_string())
            .with_conflicted_file("file2.txt".to_string());

        assert_eq!(
            checkpoint.conflicted_files.len(),
            2,
            "Should track both files"
        );
        assert!(
            checkpoint
                .conflicted_files
                .contains(&"file1.txt".to_string()),
            "Should contain file1.txt"
        );
        assert!(
            checkpoint
                .conflicted_files
                .contains(&"file2.txt".to_string()),
            "Should contain file2.txt"
        );

        // Adding duplicate should not increase count
        let checkpoint = checkpoint.with_conflicted_file("file1.txt".to_string());
        assert_eq!(
            checkpoint.conflicted_files.len(),
            2,
            "Should not increase count for duplicate"
        );
        assert!(
            checkpoint
                .conflicted_files
                .contains(&"file1.txt".to_string()),
            "Should still contain file1.txt"
        );
    }

    #[test]
    fn test_rebase_checkpoint_with_resolved_file() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_conflicted_file("file1.txt".to_string())
            .with_resolved_file("file1.txt".to_string());
        assert!(checkpoint.resolved_files.contains(&"file1.txt".to_string()));
    }

    #[test]
    fn test_rebase_checkpoint_with_error() {
        let checkpoint =
            RebaseCheckpoint::new("main".to_string()).with_error("Test error".to_string());
        assert_eq!(checkpoint.error_count, 1);
        assert_eq!(checkpoint.last_error, Some("Test error".to_string()));
    }

    #[test]
    fn test_rebase_checkpoint_all_conflicts_resolved() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_conflicted_file("file1.txt".to_string())
            .with_conflicted_file("file2.txt".to_string())
            .with_resolved_file("file1.txt".to_string())
            .with_resolved_file("file2.txt".to_string());
        assert!(checkpoint.all_conflicts_resolved());
    }

    #[test]
    fn test_rebase_checkpoint_unresolved_conflict_count() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_conflicted_file("file1.txt".to_string())
            .with_conflicted_file("file2.txt".to_string())
            .with_resolved_file("file1.txt".to_string());
        assert_eq!(checkpoint.unresolved_conflict_count(), 1);
    }

    #[test]
    fn test_rebase_phase_equality() {
        assert_eq!(RebasePhase::NotStarted, RebasePhase::NotStarted);
        assert_ne!(RebasePhase::NotStarted, RebasePhase::RebaseInProgress);
    }

    #[test]
    fn test_rebase_checkpoint_path() {
        let path = rebase_checkpoint_path();
        assert!(path.contains(".agent"));
        assert!(path.contains("rebase_checkpoint.json"));
    }

    #[test]
    fn test_rebase_checkpoint_serialization() {
        let checkpoint = RebaseCheckpoint::new("feature-branch".to_string())
            .with_phase(RebasePhase::ConflictResolutionInProgress)
            .with_conflicted_file("src/lib.rs".to_string())
            .with_resolved_file("src/main.rs".to_string())
            .with_error("Test error".to_string());

        let json = serde_json::to_string(&checkpoint).unwrap();
        assert!(json.contains("feature-branch"));
        assert!(json.contains("src/lib.rs"));

        let deserialized: RebaseCheckpoint = serde_json::from_str(&json).unwrap();
        assert_eq!(deserialized.phase, checkpoint.phase);
        assert_eq!(deserialized.upstream_branch, checkpoint.upstream_branch);
    }

    #[test]
    fn test_validate_checkpoint_valid() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_phase(RebasePhase::RebaseInProgress)
            .with_conflicted_file("file1.rs".to_string())
            .with_resolved_file("file1.rs".to_string());

        assert!(validate_checkpoint(&checkpoint).is_ok());
    }

    #[test]
    fn test_validate_checkpoint_empty_upstream() {
        // NotStarted phase allows empty upstream
        let checkpoint = RebaseCheckpoint::new(String::new()).with_phase(RebasePhase::NotStarted);
        assert!(validate_checkpoint(&checkpoint).is_ok());

        // Other phases require non-empty upstream
        let checkpoint =
            RebaseCheckpoint::new(String::new()).with_phase(RebasePhase::RebaseInProgress);
        assert!(validate_checkpoint(&checkpoint).is_err());
    }

    #[test]
    fn test_validate_checkpoint_invalid_timestamp() {
        let mut checkpoint = RebaseCheckpoint::new("main".to_string());
        checkpoint.timestamp = "invalid-timestamp".to_string();

        assert!(validate_checkpoint(&checkpoint).is_err());
    }

    #[test]
    fn test_validate_checkpoint_resolved_without_conflicted() {
        let checkpoint =
            RebaseCheckpoint::new("main".to_string()).with_resolved_file("file1.rs".to_string());

        // Resolved file not in conflicted list should fail validation
        assert!(validate_checkpoint(&checkpoint).is_err());
    }
}

#[cfg(all(test, feature = "test-utils"))]
mod workspace_tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_save_and_load_checkpoint_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_phase(RebasePhase::ConflictDetected)
            .with_conflicted_file("file1.rs".to_string());

        // Save checkpoint
        save_rebase_checkpoint_with_workspace(&checkpoint, &workspace).unwrap();

        // Verify it exists
        assert!(rebase_checkpoint_exists_with_workspace(&workspace));

        // Load it back
        let loaded = load_rebase_checkpoint_with_workspace(&workspace)
            .unwrap()
            .expect("checkpoint should exist after save");

        assert_eq!(loaded.phase, RebasePhase::ConflictDetected);
        assert_eq!(loaded.upstream_branch, "main");
        assert_eq!(
            loaded.conflicted_files.len(),
            1,
            "Should have one conflicted file"
        );
        assert!(
            loaded.conflicted_files.contains(&"file1.rs".to_string()),
            "Should contain file1.rs"
        );
    }

    #[test]
    fn test_clear_checkpoint_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        let checkpoint = RebaseCheckpoint::new("main".to_string());
        save_rebase_checkpoint_with_workspace(&checkpoint, &workspace).unwrap();
        assert!(rebase_checkpoint_exists_with_workspace(&workspace));

        clear_rebase_checkpoint_with_workspace(&workspace).unwrap();
        assert!(!rebase_checkpoint_exists_with_workspace(&workspace));
    }

    #[test]
    fn test_load_nonexistent_checkpoint_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        let result = load_rebase_checkpoint_with_workspace(&workspace).unwrap();
        assert!(result.is_none());
    }

    #[test]
    fn test_checkpoint_backup_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        // Create and save first checkpoint
        let checkpoint1 =
            RebaseCheckpoint::new("main".to_string()).with_phase(RebasePhase::RebaseInProgress);
        save_rebase_checkpoint_with_workspace(&checkpoint1, &workspace).unwrap();

        // Save another checkpoint (should create backup of first)
        let checkpoint2 =
            RebaseCheckpoint::new("main".to_string()).with_phase(RebasePhase::RebaseComplete);
        save_rebase_checkpoint_with_workspace(&checkpoint2, &workspace).unwrap();

        // Load should return the latest checkpoint
        let loaded = load_rebase_checkpoint_with_workspace(&workspace)
            .unwrap()
            .expect("checkpoint should exist");
        assert_eq!(loaded.phase, RebasePhase::RebaseComplete);
    }

    #[test]
    fn test_corrupted_checkpoint_restores_from_backup_with_workspace() {
        let workspace = MemoryWorkspace::new_test();
        let checkpoint_path = Path::new(AGENT_DIR).join(REBASE_CHECKPOINT_FILE);
        let backup_path = Path::new(AGENT_DIR).join(format!("{REBASE_CHECKPOINT_FILE}.bak"));

        // Create and save a valid checkpoint
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_phase(RebasePhase::ConflictDetected)
            .with_conflicted_file("file.rs".to_string());
        save_rebase_checkpoint_with_workspace(&checkpoint, &workspace).unwrap();

        // Verify backup exists
        assert!(workspace.exists(&backup_path));

        // Corrupt the main checkpoint
        workspace
            .write(&checkpoint_path, "corrupted data {{{")
            .unwrap();

        // Loading should restore from backup
        let loaded = load_rebase_checkpoint_with_workspace(&workspace)
            .unwrap()
            .expect("should restore from backup");

        assert_eq!(loaded.phase, RebasePhase::ConflictDetected);
    }
}
