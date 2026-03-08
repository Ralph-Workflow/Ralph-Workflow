//! Diff handling tests for review prompt preparation.
//!
//! Covers scenarios where DIFF.backup is missing, oversized, or requires baseline fallback,
//! verifying that appropriate fallback instructions are generated.

use super::super::super::common::TestFixture;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{PipelineState, PromptMode};
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;

#[test]
fn test_prepare_review_prompt_diff_fallback_instructions_include_staged_and_untracked() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.base", "abc123")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed (diff is optional for review)");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }
    let _ = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_review_prompt should succeed with diff fallback instructions");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");

    assert!(
        prompt.contains("git diff abc123..HEAD"),
        "fallback should include committed diff since baseline"
    );
    assert!(
        prompt.contains("git diff abc123"),
        "fallback should include working tree diff vs baseline"
    );
    assert!(
        prompt.contains("git diff --cached abc123"),
        "fallback should include staged diff vs baseline"
    );
    assert!(
        prompt.contains("git ls-files --others --exclude-standard"),
        "fallback should include untracked files command"
    );
}

#[test]
fn test_prepare_review_prompt_uses_diff_baseline_for_oversize_diff() {
    let large_diff = "d".repeat(crate::prompts::MAX_INLINE_CONTENT_SIZE + 1);
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", &large_diff)
        .with_file(".agent/DIFF.base", "abc123")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }
    let _ = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_review_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");

    assert!(
        prompt.contains("git diff abc123"),
        "review prompt should include baseline git diff command"
    );
    assert!(
        prompt.contains("git diff --cached abc123"),
        "review prompt should include baseline cached diff command"
    );
}

#[test]
fn test_prepare_review_prompt_missing_diff_backup_with_baseline_uses_fallback_instructions() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.base", "abc123def456")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));

    // Materialize review inputs (should succeed despite missing DIFF.backup)
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed with fallback DIFF instructions");

    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    // Prepare review prompt (should use fallback instructions with baseline)
    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_review_prompt should succeed with fallback DIFF");

    assert!(
        matches!(
            result.event,
            crate::reducer::event::PipelineEvent::Review(
                crate::reducer::event::ReviewEvent::PromptPrepared { .. }
            )
        ),
        "Expected PromptPrepared event, got {:?}",
        result.event
    );

    // Verify fallback instructions contain the baseline git diff command
    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");

    assert!(
        prompt.contains("git diff abc123def456..HEAD"),
        "Review prompt should include baseline-based git diff fallback instruction; got: {prompt}"
    );
}

#[test]
fn test_prepare_review_prompt_missing_diff_backup_without_baseline_uses_generic_fallback() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));

    // Materialize review inputs (should succeed despite missing DIFF.backup and baseline)
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed with generic fallback");

    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    // Prepare review prompt (should use generic fallback instructions)
    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_review_prompt should succeed with generic fallback");

    assert!(
        matches!(
            result.event,
            crate::reducer::event::PipelineEvent::Review(
                crate::reducer::event::ReviewEvent::PromptPrepared { .. }
            )
        ),
        "Expected PromptPrepared event, got {:?}",
        result.event
    );

    // Verify fallback instructions contain generic git diff commands
    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");

    assert!(
        prompt.contains("git diff HEAD~1..HEAD")
            || prompt.contains("git diff --staged")
            || prompt.contains("git diff"),
        "Review prompt should include generic git diff fallback instructions; got: {prompt}"
    );
}
