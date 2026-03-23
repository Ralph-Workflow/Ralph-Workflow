//! Agent role-specific invocation tests
//!
//! Tests invocation behavior for each agent role:
//! - Development agent prompt handling and errors
//! - Review agent prompt handling and errors
//! - Fix agent prompt handling and errors
//! - Commit agent prompt handling, errors, and uninitialized chain detection

use super::super::common::TestFixture;
use super::ReadFailingWorkspace;
use crate::agents::AgentRole;
use crate::executor::MockProcessExecutor;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{ErrorEvent, WorkspaceIoErrorKind};
use crate::reducer::state::{AgentChainState, CommitState, PipelineState};
use crate::workspace::MemoryWorkspace;
use std::path::PathBuf;
use std::sync::Arc;

#[test]
fn test_invoke_development_agent_returns_error_when_prompt_missing() {
    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    let err = handler
        .invoke_development_agent(&mut ctx, 0)
        .expect_err("invoke_development_agent should return error when prompt missing");

    assert!(
        err.to_string().contains("development prompt"),
        "Expected error about missing development prompt, got: {err}"
    );
}

#[test]
fn test_invoke_review_agent_returns_error_when_prompt_missing() {
    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["codex".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_drain(crate::agents::AgentDrain::Development);

    let err = handler
        .invoke_review_agent(&mut ctx, 0)
        .expect_err("invoke_review_agent should return error when prompt missing");

    assert!(
        err.to_string().contains("review prompt"),
        "Expected error about missing review prompt, got: {err}"
    );

    assert_eq!(
        handler.state.agent_chain.current_drain,
        crate::agents::AgentDrain::Development,
        "handler invocation must not repair routing by rewriting the active drain"
    );
}

#[test]
fn test_invoke_review_agent_maps_non_not_found_prompt_read_errors_to_workspace_read_failed() {
    let inner = MemoryWorkspace::new_test();
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/tmp/review_prompt.txt"),
        std::io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx_with_workspace(&workspace);
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    let err = handler
        .invoke_review_agent(&mut ctx, 0)
        .expect_err("invoke_review_agent should error on non-NotFound prompt read");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/tmp/review_prompt.txt"
        ),
        "expected WorkspaceReadFailed, got: {error_event:?}"
    );
}

#[test]
fn test_invoke_fix_agent_returns_error_when_prompt_missing() {
    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    let err = handler
        .invoke_fix_agent(&mut ctx, 0)
        .expect_err("invoke_fix_agent should return error when prompt missing");

    assert!(
        err.to_string().contains("fix prompt"),
        "Expected error about missing fix prompt, got: {err}"
    );
}

#[test]
fn test_invoke_fix_agent_maps_non_not_found_prompt_read_errors_to_workspace_read_failed() {
    let inner = MemoryWorkspace::new_test();
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/tmp/fix_prompt.txt"),
        std::io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx_with_workspace(&workspace);
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    let err = handler
        .invoke_fix_agent(&mut ctx, 0)
        .expect_err("invoke_fix_agent should error on non-NotFound prompt read");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/tmp/fix_prompt.txt"
        ),
        "expected WorkspaceReadFailed, got: {error_event:?}"
    );
}

#[test]
fn test_invoke_commit_agent_returns_error_when_prompt_missing() {
    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );

    let err = handler
        .invoke_commit_agent(&mut ctx)
        .expect_err("invoke_commit_agent should return error when prompt missing");

    assert!(
        err.to_string().contains("commit prompt"),
        "Expected error about missing commit prompt, got: {err}"
    );
}

#[test]
fn test_invoke_commit_agent_maps_non_not_found_prompt_read_errors_to_workspace_read_failed() {
    let inner = MemoryWorkspace::new_test();
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/tmp/commit_prompt.txt"),
        std::io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx_with_workspace(&workspace);
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );

    let err = handler
        .invoke_commit_agent(&mut ctx)
        .expect_err("invoke_commit_agent should error on non-NotFound prompt read");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/tmp/commit_prompt.txt"
        ),
        "expected WorkspaceReadFailed, got: {error_event:?}"
    );
}

#[test]
fn test_invoke_commit_agent_surfaces_uninitialized_agent_chain_as_error_event() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_prompt.txt", "commit prompt content");
    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.executor = Arc::new(MockProcessExecutor::new());
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.commit = CommitState::Generating {
        attempt: 1,
        max_attempts: 2,
    };
    // Intentionally leave the agent chain uninitialized/empty.
    handler.state.agent_chain = AgentChainState::initial();

    let err = handler
        .invoke_commit_agent(&mut ctx)
        .expect_err("invoke_commit_agent should return typed error when agent chain is empty");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::CommitAgentNotInitialized { attempt: 1 }
        ),
        "expected CommitAgentNotInitialized, got: {error_event:?}"
    );

    // Defensive: ensure the error type is not a string-based anyhow error.
    assert!(
        !matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                kind: WorkspaceIoErrorKind::Other,
                ..
            }
        ),
        "expected a specific invariant error, not a generic workspace error"
    );
}
