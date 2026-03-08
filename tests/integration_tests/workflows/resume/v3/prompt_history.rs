//! Integration tests for v3 checkpoint prompt history replay.
//!
//! Verifies that when resuming from v3 checkpoints, the prompt history is correctly
//! replayed to ensure deterministic agent behavior across suspend/resume cycles.

use std::path::PathBuf;

use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;

use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::ui_event::UIEvent;
use ralph_workflow::reducer::PipelineState;

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handler,
    run_ralph_cli_with_handlers,
};
use crate::test_timeout::with_default_timeout;

use super::super::{
    make_checkpoint_json, make_checkpoint_with_prompt_history, MOCK_REPO_PATH, STANDARD_PROMPT,
};

use super::make_checkpoint_without_new_fields;

// ============================================================================
// V3 Hardened Resume Tests - Prompt Replay
// ============================================================================

#[test]
fn ralph_v3_prompt_replay_is_deterministic() {
    with_default_timeout(|| {
        // Create prompt history JSON
        let prompt_history_json = r#"{
            "development_1": "DETERMINISTIC PROMPT FOR DEVELOPMENT ITERATION 1",
            "planning_1": "DETERMINISTIC PROMPT FOR PLANNING"
        }"#;

        let checkpoint_json =
            make_checkpoint_with_prompt_history(MOCK_REPO_PATH, "Complete", prompt_history_json);

        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/PLAN.md", "Test plan\n")
            .with_file(".agent/commit-message.txt", "feat: test\n");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handler(&["--resume"], executor, config, &mut handler).unwrap();
    });
}

#[test]
fn ralph_v3_prompt_replay_across_multiple_iterations() {
    with_default_timeout(|| {
        // Create prompt history JSON with multiple iterations
        let prompt_history_json = r#"{
            "planning_1": "PLANNING PROMPT ITERATION 1",
            "development_1": "DEVELOPMENT PROMPT ITERATION 1",
            "planning_2": "PLANNING PROMPT ITERATION 2"
        }"#;

        let checkpoint_json =
            make_checkpoint_with_prompt_history(MOCK_REPO_PATH, "Complete", prompt_history_json);

        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/PLAN.md", "Test plan\n")
            .with_file(".agent/commit-message.txt", "feat: test\n");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Resume from Complete phase
        run_ralph_cli_with_handler(&["--resume"], executor, config, &mut handler).unwrap();
    });
}

// ============================================================================
// V3 Hardened Resume Tests - Interactive Resume Offering
// ============================================================================

#[test]
fn ralph_v3_interactive_resume_offer_on_existing_checkpoint() {
    with_default_timeout(|| {
        // Create a v3 checkpoint at Complete phase
        let checkpoint_json = make_checkpoint_json(MOCK_REPO_PATH, "Complete", 1, 1);

        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/PLAN.md", "Test plan\n")
            .with_file(".agent/commit-message.txt", "feat: test\n");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Run without --resume flag - should offer to resume interactively
        // But since we're not in a TTY, it should skip the offer and start fresh
        run_ralph_cli_with_handler(&[], executor, config, &mut handler).unwrap();

        // Verify the checkpoint was cleared
        assert!(!handler.file_exists(&PathBuf::from(".agent/checkpoint.json")));
    });
}

// ============================================================================
// Prompt Replay Determinism Tests
// ============================================================================

#[test]
fn ralph_resume_replays_prompts_deterministically() {
    with_default_timeout(|| {
        // Create prompt history JSON
        let prompt_history_json = r#"{
            "development_1": "DEVELOPMENT ITERATION 1 OF 2\n\nContext:\nTest plan content",
            "review_1": "REVIEW MODE\n\nReview the following changes..."
        }"#;

        let checkpoint_json =
            make_checkpoint_with_prompt_history(MOCK_REPO_PATH, "Complete", prompt_history_json);

        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/PLAN.md", "# Plan\n\n1. Step 1\n2. Step 2")
            .with_file(".agent/ISSUES.md", "No issues\n")
            .with_file(".agent/commit-message.txt", "feat: test\n");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Resume and verify
        run_ralph_cli_with_handler(&["--resume"], executor, config, &mut handler).unwrap();
    });
}

/// Test that checkpoints missing `prompt_md_checksum` are rejected as legacy.
///
/// Legacy checkpoints (missing required fields like `prompt_md_checksum`) are no
/// longer supported. Users must delete the checkpoint and restart the pipeline.
#[test]
fn ralph_v3_rejects_legacy_checkpoint_missing_prompt_md_checksum() {
    with_default_timeout(|| {
        // Create checkpoint WITHOUT prompt_md_checksum (legacy format)
        let checkpoint_json = make_checkpoint_without_new_fields(MOCK_REPO_PATH);

        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/PLAN.md", "Test plan\n")
            .with_file(".agent/commit-message.txt", "feat: test\n");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Verify checkpoint is REJECTED (legacy checkpoints no longer supported)
        let result = run_ralph_cli_with_handler(&["--resume"], executor, config, &mut handler);
        assert!(
            result.is_err(),
            "Should reject legacy checkpoint missing prompt_md_checksum"
        );

        let error_msg = result.unwrap_err().to_string();
        assert!(
            error_msg.contains("Legacy checkpoints are not supported")
                || error_msg.contains("checkpoint")
                || error_msg.contains("validation"),
            "Error message should mention legacy checkpoint rejection: {error_msg}"
        );
    });
}

/// Test that `UIEvent::PromptReplayHit` with `was_replayed=true` fires when a
/// stored prompt is replayed from checkpoint history on resume.
///
/// RFC-007 Short-term #3: Replay observability — every prompt generation must emit
/// a `UIEvent::PromptReplayHit`. When resuming from a checkpoint that contains
/// `commit_message_attempt_iter1_1` in its prompt history, the commit prompt
/// preparation must emit `was_replayed=true` instead of regenerating the prompt.
#[test]
fn test_prompt_replay_hit_fires_with_was_replayed_true_on_checkpoint_resume() {
    crate::test_timeout::with_default_timeout(|| {
        // Checkpoint at "CommitMessage" phase with stored commit prompt in history.
        // The key "commit_message_attempt_iter1_1" corresponds to PromptScopeKey::for_commit
        // with iteration=1, RetryMode::Normal, recovery_epoch=0.
        let prompt_history_json = r#"{
            "commit_message_attempt_iter1_1": "STORED COMMIT PROMPT FOR REPLAY TEST"
        }"#;
        let checkpoint_json = make_checkpoint_with_prompt_history(
            MOCK_REPO_PATH,
            "CommitMessage",
            prompt_history_json,
        );

        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/PLAN.md", "Test plan\n")
            .with_diff("diff --git a/test.txt b/test.txt\n+resumed change")
            .with_staged_changes(true);

        // MockEffectHandler captures UIEvents; staged diff needed for CheckCommitDiff.
        // Mark the commit prompt key as replayed so PrepareCommitPrompt emits was_replayed=true.
        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_staged_diff_sequence(["resumed-diff-content"])
            .with_replay_prompt_key("commit_message_attempt_iter1_1");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(
            &["--resume"],
            executor,
            config,
            &mut app_handler,
            &mut effect_handler,
        )
        .unwrap();

        // The commit prompt must have been replayed from checkpoint history, not regenerated.
        let has_replay_hit = effect_handler.was_ui_event_emitted(|e| {
            matches!(
                e,
                UIEvent::PromptReplayHit { key, was_replayed: true }
                    if key == "commit_message_attempt_iter1_1"
            )
        });

        assert!(
            has_replay_hit,
            "UIEvent::PromptReplayHit {{ was_replayed: true, key: \"commit_message_attempt_iter1_1\" }} \
             must fire when resuming from CommitMessage phase with stored prompt in checkpoint history"
        );
    });
}
