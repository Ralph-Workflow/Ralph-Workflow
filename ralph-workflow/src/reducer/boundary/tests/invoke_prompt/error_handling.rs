//! Error handling tests for `invoke_prompt`
//!
//! Tests error scenarios when prompts are missing or unreadable:
//! - Missing prompt files (`NotFound` errors)
//! - Non-NotFound I/O errors (`PermissionDenied`, etc.)
//! - Agent invocation failures don't mark agent as invoked

use super::super::common::TestFixture;
use super::ReadFailingWorkspace;
use crate::executor::MockProcessExecutor;
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{AgentChainState, PipelineState};
use crate::workspace::MemoryWorkspace;
use std::path::PathBuf;
use std::sync::Arc;

#[test]
fn test_invoke_planning_agent_returns_error_when_prompt_missing() {
    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    let err = handler
        .invoke_planning_agent(&mut ctx, 0)
        .expect_err("invoke_planning_agent should return error when prompt missing");

    assert!(
        err.to_string().contains("planning prompt"),
        "Expected error about missing planning prompt, got: {err}"
    );
}

#[test]
fn test_invoke_planning_agent_maps_non_not_found_prompt_read_errors_to_workspace_read_failed() {
    let inner = MemoryWorkspace::new_test();
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/tmp/planning_prompt.txt"),
        std::io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx_with_workspace(&workspace);
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    let err = handler
        .invoke_planning_agent(&mut ctx, 0)
        .expect_err("invoke_planning_agent should error on non-NotFound prompt read");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/tmp/planning_prompt.txt"
        ),
        "expected WorkspaceReadFailed, got: {error_event:?}"
    );
}

#[test]
fn test_invoke_planning_agent_does_not_mark_invoked_on_failure() {
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/planning_prompt.txt", "planning prompt");
    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.executor = Arc::new(MockProcessExecutor::new().with_agent_result(
        "claude",
        Ok(crate::executor::AgentCommandResult::failure(1, "boom")),
    ));
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Developer,
    );

    let result = handler
        .invoke_planning_agent(&mut ctx, 0)
        .expect("invoke_planning_agent should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Agent(AgentEvent::InvocationStarted { .. })
    ));
    assert!(
        result.additional_events.iter().any(|e| {
            matches!(
                e,
                PipelineEvent::Agent(
                    AgentEvent::InvocationFailed { .. }
                        | AgentEvent::RateLimited { .. }
                        | AgentEvent::AuthFailed { .. }
                        | AgentEvent::TimedOut { .. }
                )
            )
        }),
        "invoke_agent should emit a failure fact event after InvocationStarted"
    );
    assert!(
        !result
            .additional_events
            .iter()
            .any(|e| matches!(e, PipelineEvent::Lifecycle(_))),
        "planning agent invoked should not be emitted on failure"
    );
}
