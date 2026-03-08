//! Integration tests for multi-iteration commit diff freshness.
//!
//! These tests verify that in a multi-iteration pipeline, each commit cycle
//! receives a freshly captured diff — not a stale diff from a prior iteration.
//!
//! The core bug this guards against: `commit_diff_prepared` and related flags
//! surviving into the next commit cycle, causing the second commit to reuse the
//! first iteration's diff content (which includes already-committed changes).
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

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handlers,
};
use crate::test_timeout::with_default_timeout;
use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{CommitEvent, PipelineEvent};
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::PipelineState;
use std::path::PathBuf;

/// Standard PROMPT.md content for multi-iteration commit tests.
const STANDARD_PROMPT: &str = r"## Goal

Do something.

## Acceptance

- Tests pass
";

/// Create app handler configured for a 2-iteration commit freshness test.
fn create_multi_iter_app_handler() -> MockAppEffectHandler {
    MockAppEffectHandler::new()
        .with_head_oid("a".repeat(40))
        .with_cwd(PathBuf::from("/mock/repo"))
        .with_file("PROMPT.md", STANDARD_PROMPT)
        .with_diff("diff --git a/test.txt b/test.txt\n+new content")
        .with_staged_changes(true)
}

/// Test that `CheckCommitDiff` is executed once per commit-phase entry in a 2-iteration pipeline.
///
/// This guards the regression where `commit_diff_prepared=true` survives across
/// phase transitions, causing the orchestrator to skip `CheckCommitDiff` on the
/// second commit phase entry and reuse stale diff context.
#[test]
fn test_two_iterations_call_check_commit_diff_twice() {
    with_default_timeout(|| {
        let mut app_handler = create_multi_iter_app_handler();
        // Two distinct diff contents — one per commit cycle.
        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_staged_diff_sequence(["iter-1-diff-content", "iter-2-diff-content"]);
        let config = create_test_config_struct().with_developer_iters(2);
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        let check_diff_count = effect_handler
            .captured_effects()
            .into_iter()
            .filter(|e| matches!(e, Effect::CheckCommitDiff))
            .count();

        assert_eq!(
            check_diff_count, 2,
            "CheckCommitDiff must fire once per commit-phase entry (2 dev iterations = 2 commits)"
        );
    });
}

/// Test that each commit cycle receives a distinct diff `content_id_sha256`.
///
/// This is the core behavioral guard for the stale-diff bug: in a 2-iteration
/// pipeline, the second commit MUST receive a freshly captured diff with a
/// different `content_id` than the first commit's diff.
#[test]
fn test_second_commit_cycle_gets_distinct_diff_content_id() {
    with_default_timeout(|| {
        let mut app_handler = create_multi_iter_app_handler();
        // Two deliberately distinct diff strings so their sha256 content_ids differ.
        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_staged_diff_sequence([
                "diff --git a/file.rs b/file.rs\n+iter 1 change",
                "diff --git a/file.rs b/file.rs\n+iter 2 change",
            ]);
        let config = create_test_config_struct().with_developer_iters(2);
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Collect content_ids from all DiffPrepared events.
        let diff_content_ids: Vec<String> = effect_handler
            .captured_events()
            .into_iter()
            .filter_map(|e| {
                if let PipelineEvent::Commit(CommitEvent::DiffPrepared {
                    content_id_sha256, ..
                }) = e
                {
                    Some(content_id_sha256)
                } else {
                    None
                }
            })
            .collect();

        assert_eq!(
            diff_content_ids.len(),
            2,
            "Expected exactly 2 DiffPrepared events (one per commit cycle)"
        );
        assert_ne!(
            diff_content_ids[0], diff_content_ids[1],
            "Each commit cycle must use a distinct content_id_sha256 — \
             second commit must NOT reuse the first cycle's diff"
        );
    });
}

/// Test that `CreateCommit` is called once per commit cycle in a 2-iteration pipeline.
///
/// This verifies end-to-end that both commit cycles complete successfully,
/// each with its own fresh diff context.
#[test]
fn test_two_iterations_each_produce_a_commit() {
    with_default_timeout(|| {
        let mut app_handler = create_multi_iter_app_handler();
        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_staged_diff_sequence(["iter-1-diff", "iter-2-diff"]);
        let config = create_test_config_struct().with_developer_iters(2);
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        let create_commit_count = effect_handler
            .captured_effects()
            .into_iter()
            .filter(|e| matches!(e, Effect::CreateCommit { .. }))
            .count();

        assert_eq!(
            create_commit_count, 2,
            "CreateCommit must be called once per commit cycle (2 dev iterations = 2 commits)"
        );
    });
}
