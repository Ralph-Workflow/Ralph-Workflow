use super::super::super::common::TestFixture;
use super::super::AtomicWriteEnforcingWorkspace;
use super::ReadFailingWorkspace;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::state::{ContinuationState, PipelineState, PromptMode, SameAgentRetryReason};
use crate::workspace::MemoryWorkspace;
use std::io;
use std::path::PathBuf;

#[test]
fn test_prepare_fix_prompt_workspace_write_failure_is_non_fatal() {
    // Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
    // When prompt file write fails, the handler logs a warning and continues successfully.
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "<issues/>\n")
        .with_dir(".agent/tmp")
        .with_file(
            ".agent/tmp/fix_prompt.txt",
            "<<<PREVIOUS_FIX_PROMPT_MARKER>>>",
        );
    let workspace =
        AtomicWriteEnforcingWorkspace::new(inner, PathBuf::from(".agent/tmp/fix_prompt.txt"));

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::Other),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    // Per AC #5: Write failure should NOT return an error; it should succeed
    // with a warning logged instead.
    let result = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_fix_prompt should succeed even when write fails (non-fatal)");

    // Verify that the prompt was prepared in memory even though the write failed
    assert!(
        matches!(result.event, PipelineEvent::Review(_)),
        "should emit Review event even when write fails, got: {:?}",
        result.event
    );
}

#[test]
fn test_prepare_fix_prompt_does_not_mask_non_not_found_prompt_backup_read_errors() {
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "<issues/>\n")
        .with_dir(".agent/tmp");
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/PROMPT.md.backup"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let err = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::Normal)
        .expect_err("prepare_fix_prompt should surface non-NotFound PROMPT backup read failures");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/PROMPT.md.backup"
        ),
        "expected WorkspaceReadFailed for PROMPT backup read, got: {error_event:?}"
    );
}

#[test]
fn test_prepare_fix_prompt_does_not_mask_non_not_found_plan_read_errors() {
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/ISSUES.md", "<issues/>\n")
        .with_dir(".agent/tmp");
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/PLAN.md"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let err = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::Normal)
        .expect_err("prepare_fix_prompt should surface non-NotFound PLAN read failures");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/PLAN.md"
        ),
        "expected WorkspaceReadFailed for PLAN read, got: {error_event:?}"
    );
}

#[test]
fn test_prepare_fix_prompt_does_not_mask_non_not_found_issues_read_errors() {
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_dir(".agent/tmp");
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/ISSUES.md"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let err = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::Normal)
        .expect_err("prepare_fix_prompt should surface non-NotFound ISSUES read failures");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/ISSUES.md"
        ),
        "expected WorkspaceReadFailed for ISSUES read, got: {error_event:?}"
    );
}

