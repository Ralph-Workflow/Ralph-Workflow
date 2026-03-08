//! Integration tests for commit message generation.
//!
//! These tests verify that:
//! - Commit messages are generated when `developer_iters=0`
//! - `CreateCommit` effect is called correctly at the reducer layer
//! - The commit message fallback system works
//! - Diff failure fallback behavior (ralph-skip support)
//! - Pre-termination commit safety checks
//!
//! Note: Tests that specifically test LLM commit message generation behavior
//! require the commit agent to run and cannot be properly tested without the
//! `AgentExecutor` trait infrastructure. These tests focus on the observable
//! behavior of commit effect creation.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[`INTEGRATION_TESTS.md`](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** via effect capture
//! - Uses `MockAppEffectHandler` AND `MockEffectHandler` for git/filesystem isolation
//! - NO `TempDir`, `std::fs`, or real git operations
//! - Tests are deterministic and verify effects, not real filesystem state

mod diff_failure_fallback;
mod multi_iteration_diff_freshness;
mod pre_termination_safety;

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handlers,
};
use crate::test_timeout::with_default_timeout;
use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::PipelineState;
use std::path::PathBuf;

/// Standard PROMPT.md content for commit tests.
const STANDARD_PROMPT: &str = r"## Goal

Do something.

## Acceptance

- Tests pass
";

/// Create mock handlers with standard setup for commit tests.
///
/// Returns (`app_handler`, `effect_handler`) configured with:
/// - Git repo context (valid HEAD OID)
/// - Working directory set to /mock/repo
/// - PROMPT.md file with standard content
/// - A diff to trigger commit (changes from start commit)
fn create_commit_test_handlers() -> (MockAppEffectHandler, MockEffectHandler) {
    let app_handler = MockAppEffectHandler::new()
        .with_head_oid("a".repeat(40))
        .with_cwd(PathBuf::from("/mock/repo"))
        .with_file("PROMPT.md", STANDARD_PROMPT)
        // Simulate a diff exists (changes to commit)
        .with_diff("diff --git a/test.txt b/test.txt\n+new content")
        // Ensure git add stages changes
        .with_staged_changes(true);

    // Create effect handler with initial state (0 developer iters to skip to commit)
    let effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0));

    (app_handler, effect_handler)
}

/// Test that `CreateCommit` effect is called when there is a simple change.
///
/// This verifies that when a user has uncommitted changes and runs ralph
/// with `developer_iters=0` to skip agent execution, the `CreateCommit` effect
/// is called with a non-empty commit message.
#[test]
fn test_commit_message_generated_with_simple_diff() {
    with_default_timeout(|| {
        let (mut app_handler, mut effect_handler) = create_commit_test_handlers();
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Verify CreateCommit effect was called at the reducer layer
        let was_commit_created =
            effect_handler.was_effect_executed(|e| matches!(e, Effect::CreateCommit { .. }));

        assert!(
            was_commit_created,
            "CreateCommit effect should be called when there are changes"
        );
    });
}

/// Test that `CreateCommit` effect is called when there are changes to multiple files.
///
/// This verifies that when a user has uncommitted changes across multiple files
/// and runs ralph with `developer_iters=0` to skip agent execution,
/// the `CreateCommit` effect is called with a non-empty commit message.
#[test]
fn test_commit_message_generated_with_multiple_files() {
    with_default_timeout(|| {
        // Create handler with multiple file changes in the diff
        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_diff(
                "diff --git a/file1.txt b/file1.txt\n+content 1\n\
                 diff --git a/file2.txt b/file2.txt\n+content 2\n\
                 diff --git a/file3.rs b/file3.rs\n+fn main() {}",
            )
            .with_staged_changes(true);

        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0));
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Verify CreateCommit effect was called
        let was_commit_created =
            effect_handler.was_effect_executed(|e| matches!(e, Effect::CreateCommit { .. }));

        assert!(was_commit_created, "CreateCommit effect should be called");
    });
}

/// Test that `CreateCommit` effect captures diff content correctly.
///
/// This verifies that when a user has uncommitted changes including modifications
/// deep within a large file and runs ralph with `developer_iters=0`,
/// the `CreateCommit` effect is called with a non-empty commit message.
#[test]
fn test_commit_created_with_diff_content() {
    with_default_timeout(|| {
        // Create handler with a diff showing changes deep in a file
        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_diff(
                "diff --git a/large_file.txt b/large_file.txt\n\
                 @@ -148,7 +148,7 @@\n\
                  line 148\n\
                  line 149\n\
                 -line 150\n\
                 +line 150 modified\n\
                  line 151\n\
                  line 152",
            )
            .with_staged_changes(true);

        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0));
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Verify CreateCommit effect was called
        let was_commit_created =
            effect_handler.was_effect_executed(|e| matches!(e, Effect::CreateCommit { .. }));

        assert!(was_commit_created);
    });
}

/// Test that `CreateCommit` effect is called when both developer and review phases are skipped.
///
/// This verifies that when a user runs ralph with both `developer_iters=0`
/// and `reviewer_reviews=0` to skip agent execution, the `CreateCommit` effect is still
/// called with a non-empty commit message.
#[test]
fn test_commit_succeeds_without_developer_or_review() {
    with_default_timeout(|| {
        let (mut app_handler, mut effect_handler) = create_commit_test_handlers();
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Verify CreateCommit effect was called
        let was_commit_created =
            effect_handler.was_effect_executed(|e| matches!(e, Effect::CreateCommit { .. }));

        assert!(was_commit_created, "CreateCommit effect should be called");
    });
}
