//! Integration tests for v3 checkpoint prompt history replay.
//!
//! Verifies that when resuming from v3 checkpoints, the prompt history is correctly
//! replayed to ensure deterministic agent behavior across suspend/resume cycles.

use std::path::PathBuf;

use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
use ralph_workflow::reducer::effect::{Effect, EffectHandler};
use ralph_workflow::reducer::handler::MainEffectHandler;
use ralph_workflow::reducer::state::{
    AgentChainState, CommitState, MaterializedCommitInputs, MaterializedPromptInput,
    PromptInputKind, PromptInputRepresentation, PromptInputsState, PromptMaterializationReason,
    PromptMode,
};
use ralph_workflow::reducer::ui_event::UIEvent;
use ralph_workflow::reducer::PipelineState;
use ralph_workflow::workspace::MemoryWorkspace;

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handler,
};
use crate::test_timeout::with_default_timeout;

use super::super::{make_checkpoint_json, MOCK_REPO_PATH, STANDARD_PROMPT};

use super::make_checkpoint_without_new_fields;

// ============================================================================
// V3 Hardened Resume Tests - Prompt Replay (Production Lookup Path)
// ============================================================================

fn assert_commit_prompt_replay_hit_true_uses_prompt_history_lookup() {
    let workspace = MemoryWorkspace::new(PathBuf::from(MOCK_REPO_PATH))
        .with_dir(".agent/tmp")
        .with_file(
            ".agent/tmp/commit_diff.model_safe.txt",
            "diff --git a/a b/a\n+change\n",
        );
    let workspace_arc: std::sync::Arc<dyn ralph_workflow::workspace::Workspace> =
        std::sync::Arc::new(workspace);

    let mut fixture = crate::common::IntegrationFixture::with_workspace(workspace_arc);
    let mut ctx = fixture.ctx(None);

    let mut state = PipelineState::initial(1, 0);
    state.iteration = 1;
    state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 1,
    };
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["commit-agent".to_string()],
        vec![vec![]],
        ralph_workflow::agents::AgentRole::Commit,
    );

    // Content-id ties replay to the materialized diff inputs.
    state.commit_diff_content_id_sha256 = Some("diff-id".to_string());
    state.prompt_history.insert(
        "commit_message_attempt_iter1_1".to_string(),
        ralph_workflow::prompts::PromptHistoryEntry::new(
            "STORED COMMIT PROMPT".to_string(),
            Some("diff-id".to_string()),
        ),
    );

    state.prompt_inputs = PromptInputsState {
        commit: Some(MaterializedCommitInputs {
            attempt: 1,
            diff: MaterializedPromptInput {
                kind: PromptInputKind::Diff,
                content_id_sha256: "diff-id".to_string(),
                consumer_signature_sha256: state.agent_chain.consumer_signature_sha256(),
                original_bytes: 1,
                final_bytes: 1,
                model_budget_bytes: None,
                inline_budget_bytes: None,
                representation: PromptInputRepresentation::Inline,
                reason: PromptMaterializationReason::WithinBudgets,
            },
        }),
        ..PromptInputsState::default()
    };

    let mut handler = MainEffectHandler::new(state);
    let result = handler
        .execute(
            Effect::PrepareCommitPrompt {
                prompt_mode: PromptMode::Normal,
            },
            &mut ctx,
        )
        .expect("PrepareCommitPrompt should succeed");

    assert!(result.ui_events.iter().any(|e| matches!(
        e,
        UIEvent::PromptReplayHit { key, was_replayed: true }
            if key == "commit_message_attempt_iter1_1"
    )));
    assert!(
        !result.additional_events.iter().any(|e| matches!(
            e,
            ralph_workflow::reducer::event::PipelineEvent::PromptInput(
                ralph_workflow::reducer::event::PromptInputEvent::PromptCaptured { .. }
            )
        )),
        "Prompt replay must not emit PromptCaptured"
    );
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
    with_default_timeout(|| {
        // This test exercises the production lookup path via `MainEffectHandler`.
        assert_commit_prompt_replay_hit_true_uses_prompt_history_lookup();
    });
}
