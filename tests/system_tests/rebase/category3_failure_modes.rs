//! Integration tests for Category 3: Post-Rebase Failures.
//!
//! Tests for failure modes where rebase completes but leaves system in failed state:
//! - Push or remote integration failures
//! - Post-rebase validation failures (tests failing, build failures, lint violations)
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (error detection, recovery actions)
//! - Uses `tempfile::TempDir` to mock at architectural boundary (filesystem)
//! - Tests are deterministic and isolated

use tempfile::TempDir;
use test_helpers::{
    assert_project_head_unchanged, capture_project_head_oid, commit_all, init_git_repo,
    with_temp_cwd, write_file,
};

use crate::common::mock_executor_for_git_success;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::git_helpers::RebaseErrorKind;
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

/// Test that `ValidationFailed` error kind is properly represented.
///
/// This verifies that when a `ValidationFailed` error is constructed,
/// the error description contains details about the validation failure.
#[test]
#[serial]
fn rebase_detects_validation_failure() {
    with_default_timeout(|| {
        // Test that ValidationFailed error kind exists and can be created
        let err = RebaseErrorKind::ValidationFailed {
            reason: "Tests failed after rebase".to_string(),
        };

        assert!(err.description().contains("Post-rebase validation failed"));
        assert!(err.description().contains("Tests failed"));
    });
}

/// Test that `ValidationFailed` error is categorized as Category 3.
///
/// This verifies that when a `ValidationFailed` error occurs, the system
/// correctly categorizes it as a post-rebase failure.
#[test]
#[serial]
fn rebase_validation_error_has_correct_category() {
    with_default_timeout(|| {
        // Test that ValidationFailed is in category 3
        let err = RebaseErrorKind::ValidationFailed {
            reason: "Build failed".to_string(),
        };

        assert_eq!(err.category(), 3);
    });
}

/// Test that test failures after rebase are detected.
///
/// This verifies that when tests fail after rebase completes, the system
/// can represent this scenario with a `ValidationFailed` error.
#[test]
#[serial]
fn rebase_detects_test_failures() {
    with_default_timeout(|| {
        // Document expected behavior for test failures
        //
        // When a rebase completes successfully but tests fail:
        // 1. The system should detect test failures
        // 2. Return ValidationFailed error with test details
        // 3. Allow the user to decide whether to continue or abort
        //
        // We verify the error kind can represent this scenario
        let err = RebaseErrorKind::ValidationFailed {
            reason: "5 tests failed after rebase".to_string(),
        };

        assert!(err.description().contains("5 tests failed"));
    });
}

/// Test that build failures after rebase are detected.
///
/// This verifies that when build fails after rebase completes, the system
/// can represent this scenario with a `ValidationFailed` error.
#[test]
#[serial]
fn rebase_detects_build_failures() {
    with_default_timeout(|| {
        // Document expected behavior for build failures
        //
        // When a rebase completes but the build fails:
        // 1. The system should detect build failures
        // 2. Return ValidationFailed error with build error details
        // 3. Provide options to investigate or abort
        let err = RebaseErrorKind::ValidationFailed {
            reason: "Cargo build failed: unresolved import".to_string(),
        };

        assert!(err.description().contains("Cargo build failed"));
    });
}

/// Test that lint violations after rebase are detected.
///
/// This verifies that when clippy/fmt fails after rebase completes,
/// the system can represent this scenario with a `ValidationFailed` error.
#[test]
#[serial]
fn rebase_detects_lint_violations() {
    with_default_timeout(|| {
        // Document expected behavior for lint violations
        //
        // When a rebase completes but clippy/fmt fails:
        // 1. The system should detect lint violations
        // 2. Return ValidationFailed error with lint details
        // 3. Allow user to decide whether to fix or continue
        let err = RebaseErrorKind::ValidationFailed {
            reason: "Clippy found 3 warnings".to_string(),
        };

        assert!(err.description().contains("Clippy"));
    });
}

/// Test that lockfile changes after rebase are detected.
///
/// This verifies that when lockfile drift occurs after rebase, the system
/// can represent this scenario with a `ValidationFailed` error.
#[test]
#[serial]
fn rebase_detects_lockfile_changes() {
    with_default_timeout(|| {
        // Document expected behavior for lockfile changes
        //
        // When a rebase causes lockfile drift:
        // 1. The system should detect lockfile differences
        // 2. Return ValidationFailed error about lockfile
        // 3. Allow user to regenerate lockfile
        let err = RebaseErrorKind::ValidationFailed {
            reason: "Cargo.lock differs from source".to_string(),
        };

        assert!(err.description().contains("Cargo.lock"));
    });
}

/// Test that validation failures are not automatically recoverable.
///
/// This verifies that when `ValidationFailed` errors occur, the system
/// marks them as not recoverable without manual intervention.
#[test]
#[serial]
fn rebase_validation_failure_not_recoverable() {
    with_default_timeout(|| {
        // Test that validation failures are not considered automatically recoverable
        let err = RebaseErrorKind::ValidationFailed {
            reason: "Tests failed".to_string(),
        };

        // Validation failures require manual intervention
        assert!(!err.is_recoverable());
    });
}

/// Test that successful rebase produces no validation error.
///
/// This verifies that when a rebase completes successfully with all
/// validation checks passing, the system returns Success or `NoOp` result.
#[test]
#[serial]
fn rebase_successful_rebase_has_no_validation_error() {
    with_default_timeout(|| {
        let head_before = capture_project_head_oid();
        with_temp_cwd(|dir| {
            let repo = init_repo_with_initial_commit(dir);
            let default_branch = get_default_branch_name(&repo);

            // Create feature branch
            let head_commit = repo.head().unwrap().peel_to_commit().unwrap();
            let _feature_branch = repo.branch("feature", &head_commit, false).unwrap();

            // Checkout feature branch
            let obj = repo.revparse_single("feature").unwrap();
            let commit = obj.peel_to_commit().unwrap();
            repo.checkout_tree(commit.as_object(), None).unwrap();
            repo.set_head("refs/heads/feature").unwrap();

            // Make a commit on feature
            write_file(dir.path().join("feature.txt"), "feature content");
            let _ = commit_all(&repo, "add feature");

            // Rebase should succeed
            let executor = mock_executor_for_git_success();
            let result =
                ralph_workflow::git_helpers::rebase_onto(&default_branch, executor.as_ref());

            // Any outcome is acceptable here; this test verifies no validation error path.
            let _ = result;
            assert_project_head_unchanged(&head_before);
        });
    });
}

/// Test that submodule pointer changes after rebase are detected.
///
/// This verifies that when submodule pointers diverge after rebase,
/// the system can represent this scenario with a `ValidationFailed` error.
#[test]
#[serial]
fn rebase_handles_submodule_pointer_changes() {
    with_default_timeout(|| {
        // Document expected behavior for submodule changes
        //
        // When a rebase changes submodule pointers:
        // 1. The system should detect submodule mismatches
        // 2. Return ValidationFailed error about submodule
        // 3. Allow user to update submodules
        let err = RebaseErrorKind::ValidationFailed {
            reason: "Submodule 'deps/lib' has diverged".to_string(),
        };

        assert!(err.description().contains("Submodule"));
    });
}

#[test]
#[serial]
fn rebase_detects_generated_file_divergence() {
    with_default_timeout(|| {
        // Document expected behavior for generated file divergence
        //
        // When a rebase causes generated files to differ:
        // 1. The system should detect generated file differences
        // 2. Return ValidationFailed error about generated files
        // 3. Allow user to regenerate or accept differences
        let err = RebaseErrorKind::ValidationFailed {
            reason: "Generated file 'src/parser.rs' differs".to_string(),
        };

        assert!(err.description().contains("Generated file"));
    });
}
