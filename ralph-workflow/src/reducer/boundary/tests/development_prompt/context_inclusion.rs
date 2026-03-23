use super::*;
use crate::reducer::event::PromptInputEvent;
use crate::reducer::state::PromptInputRepresentation;
use std::path::PathBuf;

#[test]
fn test_materialize_development_inputs_returns_error_when_prompt_missing() {
    let workspace = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Plan\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    let err = handler.materialize_development_inputs(&ctx, 0).expect_err(
        "materialize_development_inputs should return an error when PROMPT.md is missing",
    );

    assert!(
        err.to_string().contains("PROMPT.md"),
        "Expected error message about PROMPT.md, got: {err}"
    );
}

#[test]
fn test_materialize_development_inputs_returns_error_when_plan_missing() {
    let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "Prompt\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    let err = handler.materialize_development_inputs(&ctx, 0).expect_err(
        "materialize_development_inputs should return an error when PLAN.md is missing",
    );

    assert!(
        err.to_string().contains("PLAN.md"),
        "Expected error message about PLAN.md, got: {err}"
    );
}

#[test]
fn test_materialize_development_inputs_stores_workspace_relative_file_references() {
    // Make PROMPT exceed inline budget so it becomes a file reference.
    let oversize_prompt = "x".repeat(150 * 1024);
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", &oversize_prompt)
        .with_file(".agent/PLAN.md", "Plan content");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    let result = handler
        .materialize_development_inputs(&ctx, 0)
        .expect("materialize_development_inputs should succeed");

    match &result.event {
        PipelineEvent::PromptInput(PromptInputEvent::DevelopmentInputsMaterialized {
            prompt,
            ..
        }) => {
            let PromptInputRepresentation::FileReference { path } = &prompt.representation else {
                panic!("expected PROMPT to be a file reference when oversize");
            };
            assert!(
                !path.is_absolute(),
                "file reference path should be workspace-relative (checkpoints must not store absolute paths)"
            );
            assert_eq!(
                path,
                &PathBuf::from(".agent/PROMPT.md.backup"),
                "expected PROMPT file reference to point at the PROMPT backup artifact"
            );
        }
        other => panic!("unexpected event: {other:?}"),
    }
}
