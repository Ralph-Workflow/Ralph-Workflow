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
use ralph_workflow::reducer::event::{CommitEvent, PipelineEvent, PromptInputEvent};
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::ui_event::UIEvent;
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

/// Test that the second commit cycle's diff input size does not exceed the first cycle's.
///
/// This is the size-based behavioral invariant: when the real diff shrinks from
/// cycle 1 to cycle 2, the materialized commit context for cycle 2 must also be
/// smaller (or equal) — never larger due to stale reuse of cycle-1 diff content.
///
/// Scenario:
/// - Cycle 1: 50KB diff content
/// - Cycle 2: 30KB diff content
///
/// Required: `cycle_2` `original_bytes` <= `cycle_1` `original_bytes`, and
///           `cycle_2` `original_bytes` reflects the actual second diff (≤ 30KB).
#[test]
fn test_second_commit_cycle_diff_size_does_not_exceed_first_cycle() {
    with_default_timeout(|| {
        let mut app_handler = create_multi_iter_app_handler();
        let cycle_1_diff = "A".repeat(50_000);
        let cycle_2_diff = "B".repeat(30_000);
        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_staged_diff_sequence([cycle_1_diff.as_str(), cycle_2_diff.as_str()]);
        let config = create_test_config_struct().with_developer_iters(2);
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Collect original_bytes from all CommitInputsMaterialized events (one per cycle).
        let diff_sizes: Vec<u64> = effect_handler
            .captured_events()
            .into_iter()
            .filter_map(|e| {
                if let PipelineEvent::PromptInput(PromptInputEvent::CommitInputsMaterialized {
                    diff,
                    ..
                }) = e
                {
                    Some(diff.original_bytes)
                } else {
                    None
                }
            })
            .collect();

        assert_eq!(
            diff_sizes.len(),
            2,
            "Expected exactly 2 CommitInputsMaterialized events (one per commit cycle), got: {diff_sizes:?}"
        );

        let cycle_1_bytes = diff_sizes[0];
        let cycle_2_bytes = diff_sizes[1];

        assert!(
            cycle_1_bytes <= 50_000,
            "Cycle 1 diff must reflect actual first diff content (≤ 50KB), got {cycle_1_bytes} bytes"
        );
        assert!(
            cycle_2_bytes <= 30_000,
            "Cycle 2 diff must reflect actual second diff content (≤ 30KB), got {cycle_2_bytes} bytes — \
             stale reuse of cycle-1 diff detected"
        );
        assert!(
            cycle_2_bytes <= cycle_1_bytes,
            "Cycle 2 diff input size ({cycle_2_bytes}) must not exceed cycle 1 ({cycle_1_bytes}) — \
             second commit context must not grow due to stale reuse"
        );
    });
}

/// Test that `UIEvent::PromptReplayHit` with `was_replayed=false` fires for freshly
/// generated commit prompts in a 2-iteration pipeline.
///
/// RFC-007 Short-term #3: Replay observability — every prompt generation must emit
/// a `UIEvent::PromptReplayHit` so operators can distinguish fresh generation from
/// checkpoint replay. In a fresh run (no prior history), all prompts should be
/// generated fresh and `was_replayed` must be `false`.
#[test]
fn test_prompt_replay_hit_fires_with_was_replayed_false_on_fresh_generation() {
    with_default_timeout(|| {
        let mut app_handler = create_multi_iter_app_handler();
        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_staged_diff_sequence(["iter-1-diff", "iter-2-diff"]);
        let config = create_test_config_struct().with_developer_iters(2);
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // At least one PromptReplayHit with was_replayed=false must have been emitted
        // (for the commit prompt(s) — no prior history exists in a fresh run).
        let has_fresh_hit = effect_handler.was_ui_event_emitted(|e| {
            matches!(
                e,
                UIEvent::PromptReplayHit {
                    was_replayed: false,
                    ..
                }
            )
        });

        assert!(
            has_fresh_hit,
            "UIEvent::PromptReplayHit {{ was_replayed: false }} must fire for freshly generated \
             commit prompts in a 2-iteration run with no prior checkpoint history"
        );
    });
}
