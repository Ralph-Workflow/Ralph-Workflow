//! Same-agent retry behavior tests for review prompt preparation.
//!
//! Verifies that same-agent retry mode reuses the previously prepared prompt
//! and prepends retry notes correctly without stacking duplicate notes.

use super::super::super::common::TestFixture;
use crate::reducer::event::{PipelineEvent, PromptInputEvent};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{ContinuationState, PipelineState, PromptMode, SameAgentRetryReason};
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
