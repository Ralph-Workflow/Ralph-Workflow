//! Error handling tests for review prompt preparation.
//!
//! Covers scenarios where review input reading fails with various error conditions,
//! verifying that non-NotFound errors are properly surfaced as `ErrorEvents`.

use super::super::super::common::TestFixture;
use super::super::AtomicWriteEnforcingWorkspace;
use super::helpers::ReadFailingWorkspace;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::state::{
    MaterializedPromptInput, MaterializedReviewInputs, PipelineState, PromptInputKind,
    PromptInputRepresentation, PromptMaterializationReason, PromptMode,
};
use crate::workspace::MemoryWorkspace;
use std::io;
use std::path::PathBuf;

#[test]
fn test_prepare_review_prompt_returns_error_when_inputs_not_materialized() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let err = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect_err("prepare_review_prompt should return an error when inputs not materialized");

    assert!(
        err.to_string().contains("not materialized"),
        "Expected error message about inputs not being materialized, got: {err}"
    );
}

#[test]
fn test_prepare_review_prompt_workspace_write_failure_is_non_fatal() {
    // Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
    // When prompt file write fails, the handler logs a warning and continues successfully.
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");
    let workspace =
        AtomicWriteEnforcingWorkspace::new(inner, PathBuf::from(".agent/tmp/review_prompt.txt"));

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    // Per AC #5: Write failure should NOT return an error; it should succeed
    // with a warning logged instead.
    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_review_prompt should succeed even when write fails (non-fatal)");

    // Verify that the prompt was prepared in memory even though the write failed
    assert!(
        matches!(result.event, PipelineEvent::Review(_)),
        "should emit Review event even when write fails, got: {:?}",
        result.event
    );
}

#[test]
fn test_prepare_review_prompt_does_not_mask_non_not_found_diff_backup_read_errors() {
    // This test does not call materialize_review_inputs; instead it injects a materialized
    // inline diff input and verifies that prepare_review_prompt surfaces read failures.
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.base", "abc123")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/DIFF.backup"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    handler.state.prompt_inputs.review = Some(MaterializedReviewInputs {
        pass: 0,
        plan: MaterializedPromptInput {
            kind: PromptInputKind::Plan,
            content_id_sha256: "plan".to_string(),
            consumer_signature_sha256: "sig".to_string(),
            original_bytes: 1,
            final_bytes: 1,
            model_budget_bytes: None,
            inline_budget_bytes: Some(1024),
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
        diff: MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: "diff".to_string(),
            consumer_signature_sha256: "sig".to_string(),
            original_bytes: 1,
            final_bytes: 1,
            model_budget_bytes: None,
            inline_budget_bytes: Some(1024),
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
    });

    let err = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect_err("prepare_review_prompt should surface non-NotFound DIFF read failures");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/DIFF.backup"
        ),
        "expected WorkspaceReadFailed for DIFF backup read, got: {error_event:?}"
    );
}

#[test]
fn test_prepare_review_prompt_does_not_mask_non_not_found_diff_baseline_read_errors() {
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.base", "abc123")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/DIFF.base"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    handler.state.prompt_inputs.review = Some(MaterializedReviewInputs {
        pass: 0,
        plan: MaterializedPromptInput {
            kind: PromptInputKind::Plan,
            content_id_sha256: "plan".to_string(),
            consumer_signature_sha256: "sig".to_string(),
            original_bytes: 1,
            final_bytes: 1,
            model_budget_bytes: None,
            inline_budget_bytes: Some(1024),
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
        diff: MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: "diff".to_string(),
            consumer_signature_sha256: "sig".to_string(),
            original_bytes: 1,
            final_bytes: 1,
            model_budget_bytes: None,
            inline_budget_bytes: Some(1024),
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
    });

    let err = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect_err("prepare_review_prompt should surface non-NotFound baseline read failures");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/DIFF.base"
        ),
        "expected WorkspaceReadFailed for DIFF baseline read, got: {error_event:?}"
    );
}
