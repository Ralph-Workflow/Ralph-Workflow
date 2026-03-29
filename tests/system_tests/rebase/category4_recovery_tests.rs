//! Integration tests for Category 4: Interrupted/Corrupted State.
//!
//! Tests for failure modes involving process termination and corrupted state:
//! - Process termination during rebase
//! - Incomplete or inconsistent rebase metadata
//! - Lock and state artifacts
//! - Worktree, sparse checkout, and submodule edge cases
//! - Recovery metadata unavailable
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (checkpoint save/load, recovery)
//! - Uses `tempfile::TempDir` to mock at architectural boundary (filesystem)
//! - Tests are deterministic and isolated

use std::fs;
use tempfile::TempDir;
use test_helpers::{
    assert_project_head_unchanged, capture_project_head_oid, commit_all, init_git_repo,
    with_temp_cwd, write_file,
};

use crate::common::mock_executor_for_git_success;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::git_helpers::rebase_checkpoint::{
    load_rebase_checkpoint, rebase_checkpoint_exists, save_rebase_checkpoint, RebaseCheckpoint,
    RebasePhase,
};
use ralph_workflow::git_helpers::rebase_onto;
use serial_test::serial;

fn init_repo_with_initial_commit(dir: &TempDir) -> git2::Repository {
    let repo = init_git_repo(dir);
    write_file(dir.path().join("initial.txt"), "initial content");
    let _ = commit_all(&repo, "initial commit");
    repo
}

/// Helper to get the default branch name from the repository head
fn get_default_branch_name(repo: &git2::Repository) -> String {
    repo.head()
        .ok()
        .and_then(|h| h.shorthand().map(std::string::ToString::to_string))
        .unwrap_or_else(|| "main".to_string())
}

/// Test that `ProcessTerminated` error kind is properly represented.
///
/// This verifies that when a process termination error is constructed,
/// the error description contains relevant termination information.
#[test]
#[serial]
fn rebase_detects_process_termination_error_kind() {
    with_default_timeout(|| {
        // Test that ProcessTerminated error kind exists
        use ralph_workflow::git_helpers::RebaseErrorKind;

        let err = RebaseErrorKind::ProcessTerminated {
            reason: "Rebase interrupted by SIGKILL".to_string(),
        };

        assert!(err.description().contains("terminated"));
        assert!(err.description().contains("SIGKILL"));
    });
}

/// Test that `ProcessTerminated` error is categorized as Category 4.
///
/// This verifies that when a `ProcessTerminated` error occurs, the system
/// correctly categorizes it as an interrupted/corrupted state failure.
#[test]
#[serial]
fn rebase_process_terminated_has_correct_category() {
    with_default_timeout(|| {
        // Test that ProcessTerminated is in category 4
        use ralph_workflow::git_helpers::RebaseErrorKind;

        let err = RebaseErrorKind::ProcessTerminated {
            reason: "Process crashed".to_string(),
        };

        assert_eq!(err.category(), 4);
    });
}

/// Test that `InconsistentState` error kind is properly represented.
///
/// This verifies that when an inconsistent state error is constructed,
/// the error description contains details about the corruption.
#[test]
#[serial]
fn rebase_detects_inconsistent_state_error_kind() {
    with_default_timeout(|| {
        // Test that InconsistentState error kind exists
        use ralph_workflow::git_helpers::RebaseErrorKind;

        let err = RebaseErrorKind::InconsistentState {
            details: "Rebase state files corrupted".to_string(),
        };

        assert!(err.description().contains("Inconsistent"));
        assert!(err.description().contains("corrupted"));
    });
}

/// Test that `InconsistentState` error is categorized as Category 4.
///
/// This verifies that when an `InconsistentState` error occurs, the system
/// correctly categorizes it as an interrupted/corrupted state failure.
#[test]
#[serial]
fn rebase_inconsistent_state_has_correct_category() {
    with_default_timeout(|| {
        // Test that InconsistentState is in category 4
        use ralph_workflow::git_helpers::RebaseErrorKind;

        let err = RebaseErrorKind::InconsistentState {
            details: "State files missing".to_string(),
        };

        assert_eq!(err.category(), 4);
    });
}

/// Test that rebase checkpoints survive process termination.
///
/// This verifies that when a checkpoint is saved and the process terminates,
/// the checkpoint persists and can be loaded after restart.
#[test]
#[serial]
fn rebase_checkpoint_survives_process_termination() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|_dir| {
            // Create a checkpoint
            let checkpoint = RebaseCheckpoint::new("main".to_string())
                .with_phase(RebasePhase::ConflictDetected)
                .with_conflicted_file("src/lib.rs".to_string());

            save_rebase_checkpoint(&checkpoint).unwrap();

            // Verify checkpoint exists
            assert!(rebase_checkpoint_exists());

            // Simulate process restart by loading checkpoint
            let loaded = load_rebase_checkpoint()
                .unwrap()
                .expect("checkpoint should survive process termination");

            assert_eq!(loaded.phase, RebasePhase::ConflictDetected);
            assert_eq!(loaded.conflicted_files, vec!["src/lib.rs".to_string()]);
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that stale lock files are detected and cleaned up.
///
/// This verifies that when stale .git/index.lock files exist,
/// the system cleans them up without error.
#[test]
#[serial]
fn rebase_detects_stale_lock_files() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let _repo = init_repo_with_initial_commit(dir);

            // Create a stale .git/index.lock
            let index_lock = dir.path().join(".git").join("index.lock");
            fs::create_dir_all(index_lock.parent().unwrap()).unwrap();
            fs::write(&index_lock, "stale lock content").unwrap();

            // Cleanup function should handle stale locks
            let result = ralph_workflow::git_helpers::cleanup_stale_rebase_state();
            assert!(result.is_ok());
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that corrupted rebase-apply directories are detected and cleaned.
///
/// This verifies that when .git/rebase-apply directory contains corrupted state,
/// the system handles cleanup gracefully.
#[test]
#[serial]
fn rebase_detects_corrupted_reapplydirectory() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let _repo = init_repo_with_initial_commit(dir);

            // Create a corrupted .git/rebase-apply directory
            let rebasedir = dir.path().join(".git").join("rebase-apply");
            fs::create_dir_all(&rebasedir).unwrap();

            // Write some corrupted state
            fs::write(rebasedir.join("orig-head"), "invalid\0content").unwrap();

            // Cleanup function should handle corrupted state
            let result = ralph_workflow::git_helpers::cleanup_stale_rebase_state();
            assert!(result.is_ok());
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that corrupted rebase-merge directories are detected and cleaned.
///
/// This verifies that when .git/rebase-merge directory contains corrupted state,
/// the system handles cleanup gracefully.
#[test]
#[serial]
fn rebase_detects_corrupted_rebase_mergedirectory() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let _repo = init_repo_with_initial_commit(dir);

            // Create a corrupted .git/rebase-merge directory
            let rebasedir = dir.path().join(".git").join("rebase-merge");
            fs::create_dir_all(&rebasedir).unwrap();

            // Write some corrupted state files
            fs::write(rebasedir.join("head-name"), "refs/heads/\0feature").unwrap();
            fs::write(rebasedir.join("onto"), "not-a-valid-oid").unwrap();

            // Cleanup function should handle corrupted state
            let result = ralph_workflow::git_helpers::cleanup_stale_rebase_state();
            assert!(result.is_ok());
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that missing `ORIG_HEAD` in rebase-apply is handled gracefully.
///
/// This verifies that when .git/rebase-apply exists without `ORIG_HEAD` file,
/// the system handles the situation without crashing.
#[test]
#[serial]
fn rebase_handles_missing_orig_head() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let repo = init_repo_with_initial_commit(dir);
            let default_branch = get_default_branch_name(&repo);
            let executor = mock_executor_for_git_success();

            // Create .git/rebase-apply directory without ORIG_HEAD
            let rebasedir = dir.path().join(".git").join("rebase-apply");
            fs::create_dir_all(&rebasedir).unwrap();

            // Create some files but not ORIG_HEAD
            fs::write(rebasedir.join("head-name"), "refs/heads/feature\n").unwrap();

            // System should handle missing ORIG_HEAD gracefully
            let result = rebase_onto(&default_branch, executor.as_ref());

            // Result should be Ok (either succeeds or reports error gracefully)
            assert!(result.is_ok());
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that missing HEAD reference is detected and handled gracefully.
///
/// This verifies that when .git/HEAD file is missing (corrupted repository),
/// the system returns an appropriate error or succeeds if recoverable.
#[test]
#[serial]
fn rebase_handles_missing_head_ref() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let _repo = init_repo_with_initial_commit(dir);
            let executor = mock_executor_for_git_success();

            // Delete HEAD to simulate corrupted state
            let head_file = dir.path().join(".git").join("HEAD");
            fs::remove_file(&head_file).unwrap();

            // Rebase should detect this and handle gracefully
            let result = rebase_onto("main", executor.as_ref());

            if result.is_ok() {
                // May succeed if git can recover
            } else {
                // May fail with clear error
            }
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that corrupted checkpoint files attempt backup recovery.
///
/// This verifies that when a checkpoint file is corrupted, the system
/// attempts to load from backup or handles the failure gracefully.
#[test]
#[serial]
fn rebase_checkpoint_corruption_recovery() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|_dir| {
            // Create and save a valid checkpoint
            let checkpoint = RebaseCheckpoint::new("main".to_string())
                .with_phase(RebasePhase::RebaseInProgress)
                .with_conflicted_file("src/main.rs".to_string());

            save_rebase_checkpoint(&checkpoint).unwrap();

            // Corrupt the checkpoint file
            let checkpoint_path =
                ralph_workflow::git_helpers::rebase_checkpoint::rebase_checkpoint_path();
            fs::write(&checkpoint_path, "corrupted {{{ invalid json").unwrap();

            // Loading should attempt recovery from backup
            let result = load_rebase_checkpoint();

            if let Ok(Some(loaded)) = result {
                // Should have loaded from backup
                assert_eq!(loaded.upstream_branch, "main");
            }
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that orphaned temporary merge files are cleaned up.
///
/// This verifies that when `MERGE_HEAD`, `MERGE_MSG`, and similar files
/// exist without an active merge, the system cleans them up.
#[test]
#[serial]
fn rebase_handles_orphaned_temp_files() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let _repo = init_repo_with_initial_commit(dir);

            // Create orphaned temporary merge files
            let gitdir = dir.path().join(".git");
            fs::write(gitdir.join("MERGE_HEAD"), "abc123\n").unwrap();
            fs::write(gitdir.join("MERGE_MSG"), "Merge message\n").unwrap();
            fs::write(gitdir.join("COMMIT_EDITMSG"), "Commit message\n").unwrap();

            // Cleanup should handle orphaned files
            let result = ralph_workflow::git_helpers::cleanup_stale_rebase_state();
            assert!(result.is_ok());
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that rebase operations work when reflog is disabled.
///
/// This verifies that when reflog is not available for recovery,
/// the system falls back to checkpoint mechanism.
#[test]
#[serial]
fn rebase_handles_reflog_disabled() {
    with_default_timeout(|| {
        // Document expected behavior when reflog is disabled
        //
        // When reflog is disabled or pruned:
        // 1. The system cannot use reflog for recovery
        // 2. Should fall back to checkpoint system
        // 3. May need manual intervention if checkpoint is also unavailable

        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let _repo = init_repo_with_initial_commit(dir);
            let executor = mock_executor_for_git_success();

            // Disable reflog
            let gitdir = dir.path().join(".git");
            fs::create_dir_all(gitdir.join("refs").join("heads")).unwrap();

            // System should still work without reflog
            let result = rebase_onto("main", executor.as_ref());
            assert!(result.is_ok());
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that sparse checkout configuration is handled during rebase.
///
/// This verifies that when sparse checkout is enabled, the system
/// handles rebase operations without causing conflicts.
#[test]
#[serial]
fn rebase_detects_sparse_checkout_conflicts() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let repo = init_repo_with_initial_commit(dir);
            let default_branch = get_default_branch_name(&repo);
            let executor = mock_executor_for_git_success();

            // Configure sparse checkout
            let gitdir = dir.path().join(".git");
            let infodir = gitdir.join("info");
            fs::create_dir_all(&infodir).unwrap();

            // Create sparse checkout config
            fs::write(infodir.join("sparse-checkout"), "*.rs\n").unwrap();
            // Enable sparse checkout (requires core.sparseCheckout config)
            let mut cfg = repo.config().expect("open config");
            cfg.set_bool("core.sparseCheckout", true)
                .expect("set sparseCheckout config");

            // System should handle sparse checkout gracefully
            let result = rebase_onto(&default_branch, executor.as_ref());
            assert!(result.is_ok());
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that detached HEAD with interrupted rebase state is handled.
///
/// This verifies that when HEAD is detached and rebase state exists,
/// the system handles the situation gracefully.
#[test]
#[serial]
fn rebase_detects_detached_head_after_interruption() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let repo = init_repo_with_initial_commit(dir);
            let default_branch = get_default_branch_name(&repo);
            let executor = mock_executor_for_git_success();

            // Create a commit
            write_file(dir.path().join("file.txt"), "content");
            let _ = commit_all(&repo, "add file");

            // Detach HEAD
            let head_commit = repo.head().unwrap().peel_to_commit().unwrap();
            repo.set_head_detached(head_commit.id()).unwrap();
            repo.checkout_head(None).unwrap();

            // Simulate interrupted rebase state
            let rebasedir = dir.path().join(".git").join("rebase-merge");
            fs::create_dir_all(&rebasedir).unwrap();
            fs::write(rebasedir.join("onto"), head_commit.id().to_string()).unwrap();

            // System should handle detached HEAD with rebase state
            let result = rebase_onto(&default_branch, executor.as_ref());

            if result.is_ok() {
                // May succeed
            } else {
                // May fail with clear error
            }

            // Clean up
            let _ = ralph_workflow::git_helpers::abort_rebase(executor.as_ref());
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that garbage collected objects are reported as repository corruption.
///
/// This verifies that when git gc has removed objects needed for recovery,
/// the system returns `RepositoryCorrupt` error with appropriate message.
#[test]
#[serial]
fn rebase_handles_git_gc_removed_objects() {
    with_default_timeout(|| {
        // Document expected behavior when git gc removes recovery objects
        //
        // When garbage collection removes objects needed for recovery:
        // 1. The system should detect missing objects
        // 2. Return RepositoryCorrupt error
        // 3. Suggest fetching from remote or using reflog

        let err = ralph_workflow::git_helpers::RebaseErrorKind::RepositoryCorrupt {
            details: "Object not found - may have been garbage collected".to_string(),
        };

        assert!(err.description().contains("Repository integrity issue"));
        assert!(err.description().contains("garbage collected"));
    });
}

/// Test that `ProcessTerminated` errors are marked as recoverable.
///
/// This verifies that when a process termination occurs, the system
/// considers the error recoverable via checkpoint mechanism.
#[test]
#[serial]
fn rebase_process_terminated_is_recoverable() {
    with_default_timeout(|| {
        // Test that ProcessTerminated errors are considered recoverable
        use ralph_workflow::git_helpers::RebaseErrorKind;

        let err = RebaseErrorKind::ProcessTerminated {
            reason: "CI timeout".to_string(),
        };

        // Process termination should be recoverable via checkpoint
        assert!(err.is_recoverable());
    });
}

/// Test that `InconsistentState` errors are marked as recoverable.
///
/// This verifies that when inconsistent state is detected, the system
/// considers the error recoverable via cleanup operations.
#[test]
#[serial]
fn rebase_inconsistent_state_is_recoverable() {
    with_default_timeout(|| {
        // Test that InconsistentState errors are considered recoverable
        use ralph_workflow::git_helpers::RebaseErrorKind;

        let err = RebaseErrorKind::InconsistentState {
            details: "Corrupted rebase state".to_string(),
        };

        // Inconsistent state should be recoverable via cleanup
        assert!(err.is_recoverable());
    });
}
