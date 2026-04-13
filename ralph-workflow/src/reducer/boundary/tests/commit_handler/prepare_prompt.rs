use super::super::common::TestFixture;
use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_registry::TemplateRegistry;
use crate::prompts::PromptHistoryEntry;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{AgentEvent, PipelineEvent, PipelinePhase, PromptInputEvent};
use crate::reducer::state::{
    AgentChainState, CommitState, ContinuationState, MaterializedCommitInputs,
    MaterializedPromptInput, PipelineState, PromptInputKind, PromptInputRepresentation,
    PromptInputsState, PromptMaterializationReason, PromptMode, SameAgentRetryReason,
};
use crate::workspace::{MemoryWorkspace, Workspace};
use std::fs;
use std::panic::{catch_unwind, AssertUnwindSafe};
use tempfile::tempdir;

#[test]
fn test_prepare_commit_prompt_does_not_emit_generation_started() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.iteration = 1;
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );
    let result = handler
        .prepare_commit_prompt_with_diff_and_mode(
            &ctx,
            "diff --git a/a b/a\n+change\n",
            crate::reducer::state::PromptMode::Normal,
        )
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(crate::reducer::event::CommitEvent::PromptPrepared { attempt: 1 })
    ));
    assert!(
        result.additional_events.iter().all(|event| !matches!(
            event,
            PipelineEvent::Commit(crate::reducer::event::CommitEvent::GenerationStarted)
        )),
        "prepare commit prompt should not emit commit_generation_started"
    );
}

#[test]
fn test_prepare_commit_prompt_emits_template_rendered_on_validation_failure() {
    let tempdir = tempdir().expect("create temp dir");
    let template_path = tempdir.path().join("commit_message_xml.txt");
    fs::write(&template_path, "Diff:\n{{DIFF}}\nMissing: {{MISSING}}\n")
        .expect("write commit template");

    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.template_context =
        TemplateContext::new(TemplateRegistry::new(Some(tempdir.path().to_path_buf())));
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.iteration = 1;
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    let result = handler
        .prepare_commit_prompt_with_diff_and_mode(
            &ctx,
            "diff --git a/a b/a\n+change\n",
            PromptMode::Normal,
        )
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    assert!(result.ui_events.iter().any(|ev| matches!(
        ev,
        crate::reducer::ui_event::UIEvent::PromptReplayHit { key, was_replayed: false }
            if key == "commit_message_attempt_iter1_1"
    )));

    match result.event {
        PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
            phase,
            template_name,
            log,
        }) => {
            assert_eq!(phase, PipelinePhase::CommitMessage);
            assert_eq!(template_name, "commit_message_xml");
            assert!(log.unsubstituted.contains(&"MISSING".to_string()));
        }
        other => panic!("expected TemplateRendered event, got {other:?}"),
    }

    assert!(
        result.additional_events.iter().any(|event| matches!(
            event,
            PipelineEvent::Agent(AgentEvent::TemplateVariablesInvalid { missing_variables, .. })
                if missing_variables.contains(&"MISSING".to_string())
        )),
        "expected TemplateVariablesInvalid with missing variables"
    );
}

#[test]
fn test_prepare_commit_prompt_does_not_panic_when_materialized_attempt_mismatch() {
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/commit_diff.model_safe.txt", "diff");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 2,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["qwen".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );
    // Materialized for attempt 1, but current attempt is 2 (mismatch).
    handler.state.prompt_inputs.commit = Some(MaterializedCommitInputs {
        attempt: 1,
        diff: MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: "hash".to_string(),
            consumer_signature_sha256: handler.state.agent_chain.consumer_signature_sha256(),
            original_bytes: 1,
            final_bytes: 1,
            model_budget_bytes: Some(100_000),
            inline_budget_bytes: Some(100_000),
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
    });

    let result = catch_unwind(AssertUnwindSafe(|| {
        handler.prepare_commit_prompt(&ctx, PromptMode::Normal)
    }));
    assert!(
        result.is_ok(),
        "prepare_commit_prompt should not panic when commit inputs are missing for the current attempt"
    );
}

#[test]
fn test_prepare_commit_prompt_same_agent_retry_uses_previous_prepared_prompt() {
    let marker = "<<<PREVIOUS_COMMIT_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/commit_prompt.txt", marker);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(1, 0)
    });
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    handler
        .prepare_commit_prompt_with_diff_and_mode(
            &ctx,
            "diff --git a/a b/a\n+change\n",
            PromptMode::SameAgentRetry,
        )
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    let prompt = fixture
        .workspace
        .read(std::path::Path::new(".agent/tmp/commit_prompt.txt"))
        .expect("commit_prompt.txt should be written");

    assert!(
        prompt.contains(marker),
        "Same-agent retry should reuse the previously prepared prompt; got: {prompt}"
    );
    assert!(
        prompt.contains("## Retry Note (attempt 1)"),
        "Same-agent retry should prepend retry note; got: {prompt}"
    );
}

#[test]
fn test_prepare_commit_prompt_same_agent_retry_does_not_stack_retry_notes() {
    let marker = "<<<PREVIOUS_COMMIT_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/commit_prompt.txt", marker);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(1, 0)
    });
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    handler
        .prepare_commit_prompt_with_diff_and_mode(
            &ctx,
            "diff --git a/a b/a\n+change\n",
            PromptMode::SameAgentRetry,
        )
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    handler.state.continuation.same_agent_retry_count = 2;
    handler
        .prepare_commit_prompt_with_diff_and_mode(
            &ctx,
            "diff --git a/a b/a\n+change\n",
            PromptMode::SameAgentRetry,
        )
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    let prompt = fixture
        .workspace
        .read(std::path::Path::new(".agent/tmp/commit_prompt.txt"))
        .expect("commit_prompt.txt should be written");

    assert!(
        prompt.contains(marker),
        "Same-agent retry should keep the base prompt content; got: {prompt}"
    );
    assert_eq!(
        prompt.matches("## Retry Note").count(),
        1,
        "Expected exactly one retry note block, got: {prompt}"
    );
    assert!(
        prompt.contains("## Retry Note (attempt 2)"),
        "Expected retry note attempt 2 after second retry, got: {prompt}"
    );
    assert!(
        !prompt.contains("## Retry Note (attempt 1)"),
        "Expected previous retry note to be replaced, got: {prompt}"
    );
}

#[test]
fn test_prepare_commit_prompt_same_agent_retry_replays_from_prompt_history_when_available() {
    use crate::reducer::prompt_inputs::sha256_hex_str;
    use crate::reducer::ui_event::UIEvent;

    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(1, 0)
    });
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    let scope_key = crate::prompts::PromptScopeKey::for_commit(
        handler.state.iteration,
        1,
        crate::prompts::RetryMode::SameAgent { count: 1 },
        handler.state.recovery_epoch,
    );
    let key = scope_key.to_string();
    let diff_content_id = sha256_hex_str("DIFF");
    let consumer_sig = handler.state.agent_chain.consumer_signature_sha256();
    let prompt_content_id = sha256_hex_str(&format!(
        "commit_prompt|diff:{diff_content_id}|consumer:{consumer_sig}"
    ));
    handler.state.prompt_history.insert(
        key.clone(),
        PromptHistoryEntry::new("STORED-PROMPT".to_string(), Some(prompt_content_id)),
    );

    let result = handler
        .prepare_commit_prompt_with_diff_and_mode(&ctx, "DIFF", PromptMode::SameAgentRetry)
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    let prompt = fixture
        .workspace
        .read(std::path::Path::new(".agent/tmp/commit_prompt.txt"))
        .expect("commit_prompt.txt should be written");
    assert_eq!(prompt, "STORED-PROMPT");

    assert!(
        result.ui_events.iter().any(|e| matches!(
            e,
            UIEvent::PromptReplayHit {
                key: k,
                was_replayed: true
            } if k == &key
        )),
        "Expected PromptReplayHit(was_replayed=true) for {key}; got: {:?}",
        result.ui_events
    );
    assert!(
        !result.additional_events.iter().any(|e| matches!(
            e,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured { key: k, .. })
                if k == &key
        )),
        "Prompt replay should not emit PromptCaptured for {key}; got: {:?}",
        result.additional_events
    );
}

/// Test that commit prompt keys are unique per iteration, preventing cross-cycle prompt replay.
///
/// Root cause of the stale-commit-diff bug: commit prompt keys use only the attempt number
/// (e.g. `commit_message_attempt_1`). Since attempt numbers reset to 1 on each new commit cycle
/// and `prompt_history` is run-scoped, cycle 2 with attempt=1 looks up the same key as cycle 1
/// and replays the stale cycle-1 prompt (which embeds cycle-1's diff content).
///
/// This test proves the bug: it pre-populates `prompt_history` with the cycle-1 key and asserts
/// that a cycle-2 handler generates a fresh prompt (not replayed from history). It FAILS on code
/// where the key is `commit_message_attempt_{attempt}` and PASSES when iteration is included.
#[test]
fn test_commit_prompt_key_is_unique_per_cycle_prevents_stale_replay() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    // Simulate cycle 2: iteration=2, attempt resets to 1.
    // Pre-populate state.prompt_history with the cycle-1 key as if cycle 1 already ran.
    // On buggy code (key = "commit_message_attempt_1"), cycle 2 will find and replay this.
    // Since handlers now read from self.state.prompt_history, insert into the handler state.
    let mut handler = MainEffectHandler::new(PipelineState::initial(2, 0));
    handler.state.prompt_history.insert(
        "commit_message_attempt_1".to_string(),
        PromptHistoryEntry::from_string(
            "STALE-CYCLE-1-PROMPT: old diff content from iteration 1".to_string(),
        ),
    );
    handler.state.iteration = 2;
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    handler
        .prepare_commit_prompt_with_diff_and_mode(
            &ctx,
            "FRESH-CYCLE-2-DIFF: unique cycle 2 changes",
            PromptMode::Normal,
        )
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    let prompt = fixture
        .workspace
        .get_file(".agent/tmp/commit_prompt.txt")
        .expect("commit_prompt.txt should be written");

    assert!(
        !prompt.contains("STALE-CYCLE-1-PROMPT"),
        "Cycle-2 commit prompt must NOT contain stale cycle-1 content — \
         prompt key collision caused cross-cycle replay; got: {prompt}"
    );
    assert!(
        prompt.contains("FRESH-CYCLE-2-DIFF"),
        "Cycle-2 commit prompt must contain fresh cycle-2 diff content; got: {prompt}"
    );
}

/// Test that stored commit prompts are gated on the current commit diff content-id.
///
/// RFC-007 introduced optional content-id validation for prompt replay: if a stored
/// prompt's content-id does not match the current materialized inputs, the stored
/// entry must be treated as a cache miss and a fresh prompt must be generated.
///
/// For commit prompts, the relevant materialized input is the commit diff; this
/// test ensures that when the diff content-id changes, we do not replay a stale
/// stored commit prompt under the same prompt key.
#[test]
fn test_commit_prompt_replay_is_gated_on_commit_diff_content_id() {
    use crate::reducer::prompt_inputs::sha256_hex_str;

    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(2, 0));
    handler.state.iteration = 2;
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.commit_diff_content_id_sha256 = Some("new_hash".to_string());
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    let consumer_sig = handler.state.agent_chain.consumer_signature_sha256();
    let expected_prompt_content_id = sha256_hex_str(&format!(
        "commit_prompt|diff:new_hash|consumer:{consumer_sig}"
    ));

    let scope_key = crate::prompts::PromptScopeKey::for_commit(
        2,
        1,
        crate::prompts::RetryMode::Normal,
        handler.state.recovery_epoch,
    );
    let prompt_key = scope_key.to_string();

    // Pre-populate prompt history with a stale prompt entry for the same key.
    handler.state.prompt_history.insert(
        prompt_key.clone(),
        PromptHistoryEntry::new("STALE-PROMPT".to_string(), Some("old_hash".to_string())),
    );

    let result = handler
        .prepare_commit_prompt_with_diff_and_mode(&ctx, "FRESH-DIFF", PromptMode::Normal)
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    let prompt = fixture
        .workspace
        .get_file(".agent/tmp/commit_prompt.txt")
        .expect("commit_prompt.txt should be written");

    assert!(
        !prompt.contains("STALE-PROMPT"),
        "Commit prompt must not replay stale stored prompt when diff content-id changes; got: {prompt}"
    );
    assert!(
        prompt.contains("FRESH-DIFF"),
        "Commit prompt must be freshly generated using the current diff; got: {prompt}"
    );

    // Replay observability must report a cache miss (fresh generation).
    assert!(
        result.ui_events.iter().any(|e| matches!(
            e,
            crate::reducer::ui_event::UIEvent::PromptReplayHit {
                key,
                was_replayed: false
            } if key == &prompt_key
        )),
        "Expected PromptReplayHit with was_replayed=false for key {prompt_key}; got: {:?}",
        result.ui_events
    );

    // Fresh generation must emit PromptCaptured so reducer-owned history can be updated.
    assert!(
        result.additional_events.iter().any(|e| matches!(
            e,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key,
                content_id: Some(content_id),
                ..
            }) if key == &prompt_key && content_id == &expected_prompt_content_id
        )),
        "Expected PromptCaptured with computed prompt content id for key {prompt_key}; got: {:?}",
        result.additional_events
    );
}

/// Commit prompt replay must also be gated on the agent-chain consumer signature.
///
/// If the consumer signature changes (e.g., agent selection changes), replaying a stored
/// prompt based only on diff id can return a prompt rendered for a different consumer.
#[test]
fn test_commit_prompt_replay_is_gated_on_consumer_signature() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(2, 0));
    handler.state.iteration = 2;
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.commit_diff_content_id_sha256 = Some("diff_id".to_string());

    // Current consumer signature (new chain)
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["codex".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    // Stored prompt was captured under a different consumer signature (old chain), but legacy
    // checkpoints may only store a diff content-id in PromptHistoryEntry.content_id.
    let scope_key = crate::prompts::PromptScopeKey::for_commit(
        2,
        1,
        crate::prompts::RetryMode::Normal,
        handler.state.recovery_epoch,
    );
    let prompt_key = scope_key.to_string();
    handler.state.prompt_history.insert(
        prompt_key.clone(),
        PromptHistoryEntry::new(
            "PROMPT-FOR-OLD-CONSUMER".to_string(),
            Some("diff_id".to_string()),
        ),
    );

    let result = handler
        .prepare_commit_prompt_with_diff_and_mode(&ctx, "FRESH-DIFF", PromptMode::Normal)
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    let prompt = fixture
        .workspace
        .get_file(".agent/tmp/commit_prompt.txt")
        .expect("commit_prompt.txt should be written");

    assert!(
        !prompt.contains("PROMPT-FOR-OLD-CONSUMER"),
        "Commit prompt must not replay a prompt captured for a different consumer signature; got: {prompt}"
    );

    assert!(result.ui_events.iter().any(|e| matches!(
        e,
        crate::reducer::ui_event::UIEvent::PromptReplayHit { key, was_replayed: false }
            if key == &prompt_key
    )));
}

/// Test that `prepare_commit_prompt` reads from materialized model-safe diff file.
///
/// Once commit inputs are materialized, the `prepare_commit_prompt` effect should
/// read from .`agent/tmp/commit_diff.model_safe.txt`, ensuring the prompt uses
/// the already-truncated content instead of re-truncating.
#[test]
fn test_prepare_commit_prompt_uses_materialized_diff() {
    // Original large diff (will be truncated)
    let large_diff = format!("diff --git a/a b/a\n+{}\n", "x".repeat(150_000));
    // Simulated truncated diff from materialization
    let model_safe_diff = "diff --git a/a b/a\n+truncated_content [truncated...]\n";

    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_diff.txt", &large_diff)
        .with_file(".agent/tmp/commit_diff.model_safe.txt", model_safe_diff)
        .with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["qwen".to_string()], // qwen has 100KB budget
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );
    // Set up pre-materialized inputs
    let consumer_sig = handler.state.agent_chain.consumer_signature_sha256();
    handler.state.prompt_inputs = PromptInputsState {
        commit: Some(MaterializedCommitInputs {
            attempt: 1,
            diff: MaterializedPromptInput {
                kind: PromptInputKind::Diff,
                content_id_sha256: "hash".to_string(),
                consumer_signature_sha256: consumer_sig,
                original_bytes: large_diff.len() as u64,
                final_bytes: model_safe_diff.len() as u64,
                model_budget_bytes: Some(100_000),
                inline_budget_bytes: Some(100_000),
                representation: PromptInputRepresentation::Inline,
                reason: PromptMaterializationReason::ModelBudgetExceeded,
            },
        }),
        ..Default::default()
    };

    let result = handler
        .prepare_commit_prompt(&ctx, PromptMode::Normal)
        .expect("prepare_commit_prompt should succeed");

    // Should succeed with a prompt containing the truncated diff, not the original
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(crate::reducer::event::CommitEvent::PromptPrepared { .. })
        ),
        "expected PromptPrepared event"
    );

    // The generated prompt file should contain the truncated diff content
    let prompt_content = fixture
        .workspace
        .get_file(".agent/tmp/commit_prompt.txt")
        .unwrap();
    assert!(
        prompt_content.contains("truncated_content"),
        "prompt should contain materialized (truncated) diff content"
    );
    assert!(
        !prompt_content.contains(&"x".repeat(1000)),
        "prompt should NOT contain original large diff content"
    );
}

#[test]
fn test_commit_prompt_residual_files_are_accounted_for_not_forced_into_commit() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );
    handler.state.commit_residual_files = vec!["src/leftover.rs".to_string()];

    handler
        .prepare_commit_prompt_with_diff_and_mode(&ctx, "DIFF", PromptMode::Normal)
        .expect("prepare_commit_prompt_with_diff_and_mode should succeed");

    let prompt = fixture
        .workspace
        .get_file(".agent/tmp/commit_prompt.txt")
        .expect("commit_prompt.txt should be written");

    assert!(
        prompt.contains("must be accounted for"),
        "Residual file guidance must require accounting, not forced inclusion; got: {prompt}"
    );
    assert!(
        prompt.contains("ralph-excluded-files"),
        "Residual file guidance must mention the exclusion metadata section; got: {prompt}"
    );
    assert!(
        prompt.contains("- src/leftover.rs"),
        "Residual file list must be included; got: {prompt}"
    );
}

#[test]
fn test_prepare_commit_prompt_invalidates_materialized_inputs_when_model_safe_diff_missing() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(
            ".agent/tmp/commit_diff.txt",
            "diff --git a/a b/a\n+change\n",
        )
        .with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.commit_diff_prepared = true;
    handler.state.commit_diff_empty = false;
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["qwen".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );
    let consumer_sig = handler.state.agent_chain.consumer_signature_sha256();
    handler.state.prompt_inputs = PromptInputsState {
        commit: Some(MaterializedCommitInputs {
            attempt: 1,
            diff: MaterializedPromptInput {
                kind: PromptInputKind::Diff,
                content_id_sha256: "hash".to_string(),
                consumer_signature_sha256: consumer_sig,
                original_bytes: 1,
                final_bytes: 1,
                model_budget_bytes: Some(100_000),
                inline_budget_bytes: Some(100_000),
                representation: PromptInputRepresentation::Inline,
                reason: PromptMaterializationReason::WithinBudgets,
            },
        }),
        ..Default::default()
    };

    let result = handler
        .prepare_commit_prompt(&ctx, PromptMode::Normal)
        .expect("prepare_commit_prompt should return an EffectResult");

    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(crate::reducer::event::CommitEvent::DiffInvalidated { .. })
        ),
        "Expected DiffInvalidated event to force diff recomputation when commit_diff.model_safe.txt is missing, got {:?}",
        result.event
    );
}

#[test]
fn test_prepare_commit_prompt_invalidates_materialized_inputs_when_diff_file_reference_missing() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(
            ".agent/tmp/commit_diff.txt",
            "diff --git a/a b/a\n+change\n",
        )
        .with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.commit_diff_prepared = true;
    handler.state.commit_diff_empty = false;
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["qwen".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );
    let consumer_sig = handler.state.agent_chain.consumer_signature_sha256();
    handler.state.prompt_inputs = PromptInputsState {
        commit: Some(MaterializedCommitInputs {
            attempt: 1,
            diff: MaterializedPromptInput {
                kind: PromptInputKind::Diff,
                content_id_sha256: "hash".to_string(),
                consumer_signature_sha256: consumer_sig,
                original_bytes: 1,
                final_bytes: 1,
                model_budget_bytes: Some(100_000),
                inline_budget_bytes: Some(1),
                representation: PromptInputRepresentation::FileReference {
                    path: std::path::PathBuf::from(".agent/tmp/commit_diff.model_safe.txt"),
                },
                reason: PromptMaterializationReason::InlineBudgetExceeded,
            },
        }),
        ..Default::default()
    };

    // The file reference points at `.agent/tmp/commit_diff.model_safe.txt` but it doesn't exist.
    // The handler should invalidate diff-prepared state by emitting DiffInvalidated, forcing
    // CheckCommitDiff (and subsequent rematerialization) on the next orchestration loop.
    let result = handler
        .prepare_commit_prompt(&ctx, PromptMode::Normal)
        .expect("prepare_commit_prompt should return an EffectResult");

    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(crate::reducer::event::CommitEvent::DiffInvalidated { .. })
        ),
        "Expected DiffInvalidated event to force diff recomputation when a diff file reference is missing, got {:?}",
        result.event
    );
}

#[test]
#[cfg(debug_assertions)]
#[should_panic(expected = "Orchestrator must filter Continuation mode")]
fn test_prepare_commit_prompt_asserts_continuation_precondition_in_debug() {
    // GREEN test proving Phase-4 fix: boundary now documents the precondition via
    // debug_assert instead of implementing policy validation. Orchestrator ensures
    // Continuation is never passed (verified by orchestration tests).

    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );

    // In debug builds, this triggers the precondition assertion.
    // In release builds, behavior is undefined (orchestrator guarantees this never happens).
    let _ = handler.prepare_commit_prompt(&ctx, PromptMode::Continuation);
}
