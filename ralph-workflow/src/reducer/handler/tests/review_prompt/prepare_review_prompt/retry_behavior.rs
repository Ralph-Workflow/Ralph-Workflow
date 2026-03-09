//! Same-agent retry behavior tests for review prompt preparation.
//!
//! Verifies that same-agent retry mode reuses the previously prepared prompt
//! and prepends retry notes correctly without stacking duplicate notes.

use super::super::super::common::TestFixture;
use crate::prompts::{PromptHistoryEntry, PromptScopeKey, RetryMode};
use crate::reducer::event::{PipelineEvent, PromptInputEvent};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{ContinuationState, PipelineState, PromptMode, SameAgentRetryReason};
use crate::reducer::ui_event::UIEvent;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;

#[test]
fn test_prepare_review_prompt_same_agent_retry_uses_previous_prepared_prompt() {
    let marker = "<<<PREVIOUS_REVIEW_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/review_prompt.txt", marker);

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::InternalError),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }
    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_review_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");

    assert!(
        prompt.contains(marker),
        "Same-agent retry should reuse the previously prepared prompt; got: {prompt}"
    );
    assert!(
        prompt.contains("## Retry Note (attempt 1)"),
        "Same-agent retry should prepend retry note; got: {prompt}"
    );
    assert!(
        !result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered { .. })
        )),
        "Same-agent retry should not emit TemplateRendered when reusing the stored prompt"
    );
}

#[test]
fn test_prepare_review_prompt_same_agent_retry_does_not_stack_retry_notes() {
    let marker = "<<<PREVIOUS_REVIEW_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/review_prompt.txt", marker);

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::InternalError),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    let _ = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_review_prompt should succeed");

    handler.state.continuation.same_agent_retry_count = 2;
    let _ = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_review_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");

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
fn test_prepare_review_prompt_same_agent_retry_replays_from_prompt_history() {
    use crate::reducer::prompt_inputs::sha256_hex_str;

    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::InternalError),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    // Arrange: materialize review inputs so SameAgentRetry mode can build refs if needed,
    // but the prompt itself should be replayed from history.
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    let inputs = handler
        .state
        .prompt_inputs
        .review
        .as_ref()
        .expect("precondition: review inputs must be materialized");
    // Baseline oid is read from .agent/tmp/diff_baseline_oid.txt; absent in this fixture.
    let baseline_oid_for_prompts = "";
    let current_prompt_content_id = sha256_hex_str(&format!(
        "review_same_agent_retry|plan:{}|diff:{}|baseline:{}|consumer:{}",
        inputs.plan.content_id_sha256,
        inputs.diff.content_id_sha256,
        baseline_oid_for_prompts,
        handler.state.agent_chain.consumer_signature_sha256(),
    ));

    let scope_key = PromptScopeKey::for_review(0, RetryMode::SameAgent { count: 1 }, 0);
    let stored_prompt = "REPLAYED REVIEW SAME-AGENT RETRY PROMPT";
    handler.state.prompt_history.insert(
        scope_key.to_string(),
        PromptHistoryEntry::new(stored_prompt.to_string(), Some(current_prompt_content_id)),
    );

    // Act
    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_review_prompt should succeed");

    // Assert: prompt is replayed and PromptCaptured is not emitted.
    assert!(result.ui_events.iter().any(|ev| {
        matches!(
            ev,
            UIEvent::PromptReplayHit {
                key,
                was_replayed: true
            } if key == &scope_key.to_string()
        )
    }));
    assert!(
        !result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured { .. })
        )),
        "replayed prompts must not emit PromptCaptured"
    );

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");
    assert_eq!(prompt, stored_prompt);
}
