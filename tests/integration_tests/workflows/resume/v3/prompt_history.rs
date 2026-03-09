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
    use ralph_workflow::reducer::prompt_inputs::sha256_hex_str;

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

    // Content-id ties replay to the materialized diff inputs and consumer signature.
    state.commit_diff_content_id_sha256 = Some("diff-id".to_string());
    let consumer_sig = state.agent_chain.consumer_signature_sha256();
    let prompt_content_id = sha256_hex_str(&format!(
        "commit_prompt|diff:diff-id|consumer:{consumer_sig}"
    ));
    state.prompt_history.insert(
        "commit_message_attempt_iter1_1".to_string(),
        ralph_workflow::prompts::PromptHistoryEntry::new(
            "STORED COMMIT PROMPT".to_string(),
            Some(prompt_content_id),
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

// ==========================================================================
// End-to-end resume path tests (checkpoint JSON + --resume)
// ==========================================================================

struct PromptReplayAwareCommitHandler {
    inner: ralph_workflow::reducer::mock_effect_handler::MockEffectHandler,
    captured_ui_events: Vec<UIEvent>,
    commit_prompt_generator_ran: bool,
}

impl ralph_workflow::reducer::effect::EffectHandler<'_> for PromptReplayAwareCommitHandler {
    fn execute(
        &mut self,
        effect: ralph_workflow::reducer::effect::Effect,
        ctx: &mut ralph_workflow::phases::PhaseContext<'_>,
    ) -> anyhow::Result<ralph_workflow::reducer::effect::EffectResult> {
        use ralph_workflow::prompts::{get_stored_or_generate_prompt, PromptScopeKey, RetryMode};
        use ralph_workflow::reducer::event::{PipelineEvent, PromptInputEvent};
        use ralph_workflow::reducer::prompt_inputs::sha256_hex_str;
        use ralph_workflow::reducer::state::CommitState;

        // Delegate to the real MockEffectHandler for everything except commit prompt prep.
        if !matches!(
            effect,
            ralph_workflow::reducer::effect::Effect::PrepareCommitPrompt { .. }
        ) {
            let result = self.inner.execute(effect, ctx)?;
            self.captured_ui_events.extend(result.ui_events.clone());
            return Ok(result);
        }

        let attempt = match self.inner.state.commit {
            CommitState::Generating { attempt, .. } => attempt,
            _ => 1,
        };

        let scope_key = PromptScopeKey::for_commit(
            self.inner.state.iteration,
            attempt,
            RetryMode::Normal,
            self.inner.state.recovery_epoch,
        );
        let key = scope_key.to_string();

        let diff_id = self
            .inner
            .state
            .commit_diff_content_id_sha256
            .as_deref()
            .unwrap_or("missing-diff-id");
        let consumer_sig = self.inner.state.agent_chain.consumer_signature_sha256();
        let current_content_id = sha256_hex_str(&format!(
            "commit_prompt|diff:{diff_id}|consumer:{consumer_sig}"
        ));

        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.inner.state.prompt_history,
            Some(&current_content_id),
            || {
                self.commit_prompt_generator_ran = true;
                "GENERATED COMMIT PROMPT".to_string()
            },
        );

        let ui_events = vec![UIEvent::PromptReplayHit {
            key: key.clone(),
            was_replayed,
        }];

        // When not replayed, emit PromptCaptured to update reducer-owned history.
        let mut additional_events = Vec::new();
        if !was_replayed {
            additional_events.push(PipelineEvent::PromptInput(
                PromptInputEvent::PromptCaptured {
                    key,
                    content: prompt,
                    content_id: Some(current_content_id),
                },
            ));
        }

        let result = ralph_workflow::reducer::effect::EffectResult {
            event: PipelineEvent::commit_prompt_prepared(attempt),
            ui_events: ui_events.clone(),
            additional_events,
        };

        self.captured_ui_events.extend(ui_events);
        Ok(result)
    }
}

impl ralph_workflow::app::event_loop::StatefulHandler for PromptReplayAwareCommitHandler {
    fn update_state(&mut self, state: PipelineState) {
        self.inner.update_state(state);
    }
}

/// End-to-end resume test: load v3 checkpoint JSON via `--resume`, including a mixed-format
/// `prompt_history` map (v0 bare string + v1 object). Verify that commit prompt replay emits
/// `UIEvent::PromptReplayHit { was_replayed: true }` based on the deserialized history.
#[test]
fn test_prompt_replay_hit_true_on_resume_from_checkpoint_json_mixed_prompt_history() {
    with_default_timeout(|| {
        use crate::common::run_ralph_cli_with_custom_effect_handler;
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::prompt_inputs::sha256_hex_str;

        // Arrange a checkpoint with a stored commit prompt (v1) and a legacy v0 entry.
        let diff_content = "resume-diff\n";
        let diff_id = sha256_hex_str(diff_content);
        // Commit phase in `MockEffectHandler` always initializes the chain to a single
        // deterministic agent: `mock_agent`.
        let consumer_sig = AgentChainState::initial()
            .with_agents(
                vec!["mock_agent".to_string()],
                vec![vec![]],
                AgentRole::Commit,
            )
            .consumer_signature_sha256();
        let commit_prompt_content_id = sha256_hex_str(&format!(
            "commit_prompt|diff:{diff_id}|consumer:{consumer_sig}"
        ));

        let prompt_checksum = super::super::STANDARD_PROMPT_CHECKSUM;

        let checkpoint_json = format!(
            r#"{{
            "version": 3,
            "phase": "CommitMessage",
            "iteration": 0,
            "total_iterations": 1,
            "reviewer_pass": 0,
            "total_reviewer_passes": 0,
            "timestamp": "2024-01-01 12:00:00",
            "developer_agent": "codex",
            "reviewer_agent": "codex",
            "cli_args": {{
                "developer_iters": 1,
                "reviewer_reviews": 0,
                "commit_msg": "",
                "review_depth": null
            }},
            "developer_agent_config": {{
                "name": "codex",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "reviewer_agent_config": {{
                "name": "codex",
                "cmd": "echo",
                "output_flag": "",
                "yolo_flag": null,
                "can_commit": false,
                "model_override": null,
                "provider_override": null,
                "context_level": 1
            }},
            "rebase_state": "NotStarted",
            "config_path": null,
            "config_checksum": null,
            "working_dir": "{MOCK_REPO_PATH}",
            "prompt_md_checksum": "{prompt_checksum}",
            "git_user_name": null,
            "git_user_email": null,
            "run_id": "00000000-0000-0000-0000-000000000001",
            "parent_run_id": null,
            "resume_count": 0,
            "actual_developer_runs": 0,
            "actual_reviewer_runs": 0,
            "execution_history": null,
            "file_system_state": null,
            "prompt_history": {{
                "planning_0": "legacy planning prompt (v0)",
                "commit_message_attempt_iter0_1": {{
                    "content": "STORED COMMIT PROMPT",
                    "content_id": "{commit_prompt_content_id}"
                }}
            }}
        }}"#
        );

        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file(".agent/checkpoint.json", &checkpoint_json);

        let config = create_test_config_struct().with_developer_iters(1);
        let executor = mock_executor_with_success();

        let inner = ralph_workflow::reducer::mock_effect_handler::MockEffectHandler::new(
            PipelineState::initial(0, 0),
        )
        .with_commit_diff_content(diff_content);
        let mut effect_handler = PromptReplayAwareCommitHandler {
            inner,
            captured_ui_events: Vec::new(),
            commit_prompt_generator_ran: false,
        };

        run_ralph_cli_with_custom_effect_handler(
            &["--resume"],
            executor,
            config,
            &mut app_handler,
            &mut effect_handler,
        )
        .expect("resume run should succeed");

        assert!(
            effect_handler.captured_ui_events.iter().any(|e| matches!(
                e,
                UIEvent::PromptReplayHit { key, was_replayed: true }
                    if key == "commit_message_attempt_iter0_1"
            )),
            "Expected PromptReplayHit was_replayed=true for commit prompt key on resume; got: {:?}",
            effect_handler.captured_ui_events,
        );
        assert!(
            !effect_handler.commit_prompt_generator_ran,
            "Commit prompt generator must not run when prompt is replayed from checkpoint history"
        );
    });
}
