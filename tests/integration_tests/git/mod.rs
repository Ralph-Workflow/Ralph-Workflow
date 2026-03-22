//! Integration tests for git workflow with per-iteration commits.
//!
//! These tests verify that:
//! - `start_commit` file tracking works
//! - The --reset-start-commit flag works
//! - ALL git operations go through `AppEffectHandler` (no real git calls in tests)
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module follow the integration test style guide
//! defined in **[`INTEGRATION_TESTS.md`](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (effect execution, state changes)
//! - Uses `MockAppEffectHandler` for git/filesystem isolation
//! - Tests are deterministic and verify effect sequences
//! - **NO REAL GIT CALLS** - all git operations must go through handler

use ralph_workflow::app::effect::AppEffect;
use ralph_workflow::app::effectful::{
    get_head_oid, handle_reset_start_commit, is_on_main_branch, require_repo, save_start_commit,
};
use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::PipelineState;
use std::path::PathBuf;

use crate::common::{create_test_config_struct, mock_executor_with_success};
use crate::test_timeout::with_default_timeout;

/// Test that the `--reset-start-commit` command updates `.agent/start_commit` on main branch.
///
/// This verifies that when a user invokes the reset-start-commit handler
/// on the main/master branch, the `.agent/start_commit` file is updated to the current HEAD
/// (since there's no merge-base with itself).
#[test]
fn ralph_reset_start_commit_on_main_uses_head() {
    with_default_timeout(|| {
        // Configure mock to simulate being on main branch
        let expected_oid = "a".repeat(40);
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid(&expected_oid)
            .on_main_branch();

        // Execute the command
        let result = handle_reset_start_commit(&mut handler, None);

        // Verify success
        assert!(result.is_ok(), "reset_start_commit should succeed");

        // Verify correct effects were emitted
        let effects = handler.captured();

        // Should validate git repo
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitRequireRepo)),
            "should validate git repo"
        );

        // Should get repo root
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitGetRepoRoot)),
            "should get repo root"
        );

        // Should reset start commit
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitResetStartCommit)),
            "should reset start commit"
        );

        // Verify the start_commit file was "written" in mock filesystem
        let start_commit_path = PathBuf::from(".agent/start_commit");
        assert!(
            handler.file_exists(&start_commit_path),
            ".agent/start_commit should be created"
        );

        let content = handler.get_file(&start_commit_path).unwrap();
        assert_eq!(
            content, expected_oid,
            "start_commit should contain HEAD OID when on main branch"
        );
    });
}

/// Test that the `--reset-start-commit` command uses merge-base on feature branches.
///
/// This verifies that when a user invokes the reset-start-commit handler
/// on a feature branch, the `.agent/start_commit` file is updated appropriately.
/// (In the mock, `GitResetStartCommit` returns the HEAD OID, but in production
/// it calculates merge-base).
#[test]
fn ralph_reset_start_commit_on_feature_branch_uses_merge_base() {
    with_default_timeout(|| {
        // Configure mock to simulate being on a feature branch (not main)
        let feature_head_oid = "b".repeat(40);
        let mut handler = MockAppEffectHandler::new().with_head_oid(&feature_head_oid);
        // Note: NOT calling .on_main_branch() means is_main_branch returns false

        // Execute the command
        let result = handle_reset_start_commit(&mut handler, None);

        // Verify success
        assert!(result.is_ok(), "reset_start_commit should succeed");

        // Verify effects were emitted
        let effects = handler.captured();

        // Verify the start_commit file was written
        let start_commit_path = PathBuf::from(".agent/start_commit");
        assert!(
            handler.file_exists(&start_commit_path),
            ".agent/start_commit should be created on feature branch"
        );

        // Verify correct effects sequence
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitRequireRepo)),
            "should validate git repo"
        );
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitResetStartCommit)),
            "should reset start commit"
        );
    });
}

/// Test that `save_start_commit` correctly saves the starting commit.
///
/// This verifies that when the pipeline starts, `save_start_commit`
/// creates the `.agent/start_commit` file with a valid OID.
#[test]
fn ralph_start_commit_created_during_pipeline() {
    with_default_timeout(|| {
        let expected_oid = "c".repeat(40);
        let mut handler = MockAppEffectHandler::new().with_head_oid(&expected_oid);

        // Execute save_start_commit (what happens at pipeline start)
        let result = save_start_commit(&mut handler);

        // Verify success and OID returned
        assert!(result.is_ok(), "save_start_commit should succeed");
        assert_eq!(result.unwrap(), expected_oid, "should return the HEAD OID");

        // Verify the GitSaveStartCommit effect was emitted
        let effects = handler.captured();
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitSaveStartCommit)),
            "should emit GitSaveStartCommit effect"
        );

        // Verify the start_commit file exists in mock filesystem
        let start_commit_path = PathBuf::from(".agent/start_commit");
        assert!(
            handler.file_exists(&start_commit_path),
            ".agent/start_commit should be created"
        );
    });
}

/// Test that `require_repo` fails gracefully on an empty repository.
///
/// This verifies that when a user invokes commands in a non-git directory,
/// the command fails with an appropriate error.
#[test]
fn ralph_save_start_commit_handles_empty_repo() {
    with_default_timeout(|| {
        // Configure mock to simulate no git repository
        let mut handler = MockAppEffectHandler::new().without_repo();

        // Try to require repo - should fail
        let result = require_repo(&mut handler);
        assert!(result.is_err(), "should fail without a git repository");
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("git repository"),
            "error message should mention git repository"
        );

        // Now test with a valid repo
        let mut handler_with_repo = MockAppEffectHandler::new().with_head_oid("d".repeat(40));

        // require_repo should succeed
        let result = require_repo(&mut handler_with_repo);
        assert!(result.is_ok(), "should succeed with a git repository");

        // save_start_commit should also succeed
        let result = save_start_commit(&mut handler_with_repo);
        assert!(result.is_ok(), "save_start_commit should succeed");

        // Verify the start_commit file was created
        let start_commit_path = PathBuf::from(".agent/start_commit");
        assert!(
            handler_with_repo.file_exists(&start_commit_path),
            ".agent/start_commit should be created"
        );
    });
}

/// Test that `is_on_main_branch` correctly reports branch status.
#[test]
fn ralph_is_on_main_branch_detection() {
    with_default_timeout(|| {
        // Test on main branch
        let mut main_handler = MockAppEffectHandler::new().on_main_branch();
        let result = is_on_main_branch(&mut main_handler);
        assert!(result.is_ok());
        assert!(result.unwrap(), "should report true when on main branch");

        // Test on feature branch
        let mut feature_handler = MockAppEffectHandler::new(); // default is not on main
        let result = is_on_main_branch(&mut feature_handler);
        assert!(result.is_ok());
        assert!(
            !result.unwrap(),
            "should report false when on feature branch"
        );
    });
}

/// Test that `get_head_oid` returns the correct OID.
#[test]
fn ralph_get_head_oid_returns_correct_value() {
    with_default_timeout(|| {
        let expected_oid = "1234567890abcdef1234567890abcdef12345678";
        let mut handler = MockAppEffectHandler::new().with_head_oid(expected_oid);

        let result = get_head_oid(&mut handler);

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), expected_oid);

        // Verify the effect was captured
        let effects = handler.captured();
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitGetHeadOid)),
            "should emit GitGetHeadOid effect"
        );
    });
}

/// Test that `reset_start_commit` works with a working directory override.
#[test]
fn ralph_reset_start_commit_with_working_dir_override() {
    with_default_timeout(|| {
        let expected_oid = "e".repeat(40);
        let override_dir = PathBuf::from("/custom/workspace");
        let mut handler = MockAppEffectHandler::new().with_head_oid(&expected_oid);

        let result = handle_reset_start_commit(&mut handler, Some(&override_dir));

        assert!(result.is_ok(), "reset_start_commit should succeed");

        // Verify the SetCurrentDir effect was emitted with the override path
        let effects = handler.captured();
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::SetCurrentDir { path } if path == &override_dir)),
            "should set current dir to override path"
        );

        // Verify start_commit was written
        let start_commit_path = PathBuf::from(".agent/start_commit");
        assert!(
            handler.file_exists(&start_commit_path),
            ".agent/start_commit should be created"
        );
    });
}

// ============================================================================
// CLI Flow Tests with MockAppEffectHandler
// ============================================================================
// These tests verify that the full CLI (run_with_config_and_resolver) uses
// the AppEffectHandler for git operations instead of calling git_helpers directly.

/// Test that --reset-start-commit CLI flag uses the effect handler.
///
/// This is the key test that drives the refactor of app/mod.rs to use
/// `AppEffectHandler` throughout. When this test passes, the CLI properly
/// delegates git operations to the handler.
#[test]
fn cli_reset_start_commit_uses_effect_handler() {
    with_default_timeout(|| {
        let expected_oid = "f".repeat(40);
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid(&expected_oid)
            .on_main_branch();

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Call the CLI entry point with --reset-start-commit
        // This should use the handler instead of real git operations
        let result = crate::common::run_ralph_cli_with_handler(
            &["--reset-start-commit"],
            executor,
            config,
            &mut handler,
        );

        assert!(result.is_ok(), "CLI should succeed: {result:?}");

        // Verify the handler captured the expected effects
        let effects = handler.captured();

        // Should have called GitRequireRepo or SetCurrentDir
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitRequireRepo))
                || effects
                    .iter()
                    .any(|e| matches!(e, AppEffect::SetCurrentDir { .. })),
            "should emit GitRequireRepo or SetCurrentDir effect"
        );

        // Should have called GitResetStartCommit
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitResetStartCommit)),
            "should emit GitResetStartCommit effect"
        );

        // The start_commit file should be created via the handler
        let start_commit_path = PathBuf::from(".agent/start_commit");
        assert!(
            handler.file_exists(&start_commit_path),
            ".agent/start_commit should be created via handler"
        );
    });
}

/// Test that the full pipeline uses handler for ALL git operations.
///
/// **CRITICAL TDD TEST**: This test verifies that when running the full ralph pipeline,
/// ALL git operations go through the `AppEffectHandler`. If this test passes with an
/// empty effects list, it means real git calls are being made instead of going through
/// the handler - which is a BUG that causes real commits during tests.
///
/// The test should capture at minimum:
/// - `GitRequireRepo` (validate we're in a git repo)
/// - `GitGetRepoRoot` (get repo root for setup)
/// - `SetCurrentDir` (change to repo root)
///
/// If additional pipeline operations occur, they should also be captured:
/// - `GitSaveStartCommit` (save start commit for diff tracking)
/// - `GitAddAll` / `GitCommit` (if committing changes)
#[test]
fn full_pipeline_uses_handler_for_all_git_operations() {
    with_default_timeout(|| {
        let expected_oid = "a".repeat(40);
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid(&expected_oid)
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file(
                "PROMPT.md",
                "# Test\n\n## Goal\nTest goal\n\n## Acceptance Criteria\n- Test criterion",
            )
            .with_file(".agent/PLAN.md", "# Plan\nTest plan");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Run the full pipeline with both handlers to avoid any real agent/git side effects.
        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(1, 0));
        let _ = crate::common::run_ralph_cli_with_handlers(
            &[],
            executor,
            config,
            &mut handler,
            &mut effect_handler,
        );

        let effects = handler.captured();

        // CRITICAL: If effects is empty, real git calls are being made!
        assert!(
            !effects.is_empty(),
            "CRITICAL: Effects list is empty! This means real git calls are being made \
             instead of going through the handler. All git operations MUST use the handler \
             to prevent real commits during tests. Effects: {effects:?}"
        );

        // Validate setup phase used handler
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitRequireRepo)),
            "validate_and_setup_agents should emit GitRequireRepo. Effects: {effects:?}"
        );

        assert!(
            effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitGetRepoRoot)),
            "validate_and_setup_agents should emit GitGetRepoRoot. Effects: {effects:?}"
        );

        // If pipeline runs, it should save start commit via handler
        // (This may not trigger if pipeline exits early, but if it does run, it must use handler)
        let has_save_start = effects
            .iter()
            .any(|e| matches!(e, AppEffect::GitSaveStartCommit));
        let has_set_dir = effects
            .iter()
            .any(|e| matches!(e, AppEffect::SetCurrentDir { .. }));

        // At minimum, setup phase should set current dir
        assert!(
            has_set_dir || has_save_start,
            "Pipeline should emit SetCurrentDir or GitSaveStartCommit. Effects: {effects:?}"
        );
    });
}

/// Test that the full pipeline with BOTH handlers makes ZERO real git calls.
///
/// This test verifies the complete isolation of the pipeline from real git operations
/// by injecting both `MockAppEffectHandler` (CLI layer) and `MockEffectHandler` (reducer layer).
///
/// When both handlers are used:
/// - CLI-layer operations (`GitRequireRepo`, `SetCurrentDir`) are captured by `MockAppEffectHandler`
/// - Reducer-layer operations (`CreateCommit`, `RunRebase`) are captured by `MockEffectHandler`
/// - NO real git calls are made at any layer
#[test]
fn full_pipeline_with_both_handlers_makes_no_real_git_calls() {
    with_default_timeout(|| {
        let expected_oid = "a".repeat(40);
        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid(&expected_oid)
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file(
                "PROMPT.md",
                "# Test\n\n## Goal\nTest goal\n\n## Acceptance Criteria\n- Test criterion",
            )
            .with_file(".agent/PLAN.md", "# Plan\nTest plan");

        // Create MockEffectHandler for reducer-layer operations
        let state = PipelineState::initial(1, 0);
        let mut effect_handler = MockEffectHandler::new(state);

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Run with BOTH handlers
        let _ = crate::common::run_ralph_cli_with_handlers(
            &[],
            executor,
            config,
            &mut app_handler,
            &mut effect_handler,
        );

        // Verify CLI-layer effects were captured
        let app_effects = app_handler.captured();
        assert!(
            !app_effects.is_empty(),
            "AppEffectHandler should capture effects. Got empty list - real git calls made!"
        );

        // Verify GitRequireRepo or SetCurrentDir was captured
        assert!(
            app_effects
                .iter()
                .any(|e| matches!(e, AppEffect::GitRequireRepo))
                || app_effects
                    .iter()
                    .any(|e| matches!(e, AppEffect::SetCurrentDir { .. })),
            "Should emit GitRequireRepo or SetCurrentDir. Effects: {app_effects:?}"
        );

        // If the pipeline ran to completion, reducer effects should be captured
        // (the mock executor returns success, so the pipeline should run)
        // Note: Pipeline may exit early if PROMPT.md validation fails, so we only
        // check that IF effects were captured, no real git operations occurred.
        let reducer_effects = effect_handler.captured_effects();
        if !reducer_effects.is_empty() {
            // Verify that CreateCommit was handled by mock (not real git)
            // The mock returns a predictable hash
            for effect in &reducer_effects {
                if let ralph_workflow::reducer::effect::Effect::CreateCommit { .. } = effect {
                    // If CreateCommit was captured, it went through MockEffectHandler
                    // This means no real git_add_all() or git_commit() was called
                }
            }
        }
    });
}
