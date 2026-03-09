use super::super::super::common::TestFixture;
use crate::prompts::{PromptHistoryEntry, PromptScopeKey, RetryMode};
use crate::reducer::event::{PipelineEvent, PromptInputEvent};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{ContinuationState, PipelineState, PromptMode, SameAgentRetryReason};
use crate::reducer::ui_event::UIEvent;
use crate::workspace::MemoryWorkspace;
use std::path::Path;

#[test]
fn test_prepare_fix_prompt_same_agent_retry_uses_previous_prepared_prompt() {
    let prompt_backup = "# Prompt backup\n";
    let plan = "# Plan\n";
    let issues = "<issues/>\n";

    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", prompt_backup)
        .with_file(".agent/PLAN.md", plan)
        .with_file(".agent/ISSUES.md", issues)
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    // The same-agent retry path is allowed to reuse the previously prepared prompt *iff*
    // it matches what would be rendered from the current inputs.
    let marker = crate::prompts::prompt_fix_xml_with_context(
        ctx.template_context,
        prompt_backup,
        plan,
        issues,
        &[],
        ctx.workspace,
    );
    ctx.workspace
        .write(Path::new(".agent/tmp/fix_prompt.txt"), &marker)
        .expect("write previous prepared fix prompt");

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

    let prompt = ctx
        .workspace
        .read(Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt file should be written");

    assert!(
        prompt.contains(&marker),
        "Same-agent retry should reuse the previously prepared prompt; got: {prompt}"
    );
    assert!(
        prompt.contains("## Retry Note (attempt 1)"),
        "Same-agent retry should prepend retry note; got: {prompt}"
    );
}

#[test]
fn test_prepare_fix_prompt_same_agent_retry_does_not_stack_retry_notes() {
    let prompt_backup = "# Prompt backup\n";
    let plan = "# Plan\n";
    let issues = "<issues/>\n";
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", prompt_backup)
        .with_file(".agent/PLAN.md", plan)
        .with_file(".agent/ISSUES.md", issues)
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let marker = crate::prompts::prompt_fix_xml_with_context(
        ctx.template_context,
        prompt_backup,
        plan,
        issues,
        &[],
        ctx.workspace,
    );
    ctx.workspace
        .write(Path::new(".agent/tmp/fix_prompt.txt"), &marker)
        .expect("write previous prepared fix prompt");

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

    let prompt = ctx
        .workspace
        .read(Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt file should be written");

    assert!(
        prompt.contains(&marker),
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
    use crate::reducer::prompt_inputs::sha256_hex_str;

    let prompt_backup = "# Prompt backup\n";
    let plan = "# Plan\n";
    let issues = "<issues/>\n";
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", prompt_backup)
        .with_file(".agent/PLAN.md", plan)
        .with_file(".agent/ISSUES.md", issues)
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
    let prompt_id = sha256_hex_str(prompt_backup);
    let plan_id = sha256_hex_str(plan);
    let issues_id = sha256_hex_str(issues);
    let content_id = sha256_hex_str(&format!(
        "fix_same_agent_retry|count:1|{prompt_id}|{plan_id}|{issues_id}"
    ));
    handler.state.prompt_history.insert(
        scope_key.to_string(),
        PromptHistoryEntry::new(stored_prompt.to_string(), Some(content_id)),
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

    let prompt = ctx
        .workspace
        .read(Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt file should be written");
    assert_eq!(prompt, stored_prompt);
}
