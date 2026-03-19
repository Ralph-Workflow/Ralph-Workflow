use super::common::TestFixture;
use crate::reducer::event::PipelineEvent;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{PipelineState, PlanningValidatedOutcome};
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;

#[test]
fn test_write_planning_markdown_uses_validated_markdown_without_xml() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.planning_validated_outcome = Some(PlanningValidatedOutcome {
        iteration: 0,
        valid: true,
        markdown: Some("# Plan\n\n- Step 1\n".to_string()),
    });

    let result = handler
        .write_planning_markdown(&ctx, 0)
        .expect("write_planning_markdown should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Planning(crate::reducer::event::PlanningEvent::PlanMarkdownWritten {
            iteration: 0
        })
    ));

    let plan = fixture
        .workspace
        .read(Path::new(".agent/PLAN.md"))
        .expect("PLAN.md should be written");
    assert!(plan.contains("# Plan"));
    assert!(plan.contains("Step 1"));
}

#[test]
fn test_write_planning_markdown_returns_error_when_missing_validated_outcome() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    let err = handler.write_planning_markdown(&ctx, 0).expect_err(
        "write_planning_markdown should return error when validated outcome is missing",
    );

    assert!(
        err.to_string().contains("validated planning markdown"),
        "Expected error about missing validated planning markdown, got: {err}"
    );
}
