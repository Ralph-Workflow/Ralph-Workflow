use super::super::super::common::TestFixture;
use crate::prompts::{PromptHistoryEntry, PromptScopeKey, RetryMode};
use crate::reducer::event::{PipelineEvent, PromptInputEvent};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{ContinuationState, PipelineState, PromptMode, SameAgentRetryReason};
use crate::reducer::ui_event::UIEvent;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;

#[test]
fn test_prepare_fix_prompt_same_agent_retry_uses_previous_prepared_prompt() {
    let marker = "<<<PREVIOUS_FIX_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "<issues/>\n")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/fix_prompt.txt", marker);

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::Other),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });
    let _ = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_fix_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt file should be written");

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
fn test_prepare_fix_prompt_same_agent_retry_does_not_stack_retry_notes() {
    let marker = "<<<PREVIOUS_FIX_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "<issues/>\n")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/fix_prompt.txt", marker);

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::Other),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let _ = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_fix_prompt should succeed");

    handler.state.continuation.same_agent_retry_count = 2;
    let _ = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_fix_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt file should be written");

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
fn test_prepare_fix_prompt_same_agent_retry_replays_from_prompt_history() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "<issues/>\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::Other),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let scope_key = PromptScopeKey::for_fix(0, RetryMode::SameAgent { count: 1 }, 0);
    let stored_prompt = "REPLAYED FIX SAME-AGENT RETRY PROMPT";
    handler.state.prompt_history.insert(
        scope_key.to_string(),
        PromptHistoryEntry::from_string(stored_prompt.to_string()),
    );

    let result = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_fix_prompt should succeed");

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
        .read(Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt file should be written");
    assert_eq!(prompt, stored_prompt);
}
