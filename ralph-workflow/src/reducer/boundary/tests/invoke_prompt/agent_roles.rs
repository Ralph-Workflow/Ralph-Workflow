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

#[test]
fn test_invoke_development_agent_uses_parser_type_from_agent_config() {
    use crate::agents::{AgentConfig, AgentDrain, AgentRegistry, JsonParserType};

    // Set up workspace with a development prompt.
    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_prompt.txt",
        "test development prompt",
    );
    let mut fixture = TestFixture::with_workspace(workspace);

    // Register a "test-codex" agent configured with the Codex parser.
    // Default (buggy) behaviour uses JsonParserType::Claude regardless of this config.
    let codex_config = AgentConfig {
        cmd: String::from("codex"),
        json_parser: JsonParserType::Codex,
        ..AgentConfig::default()
    };
    fixture.registry = AgentRegistry::new()
        .unwrap()
        .register("test-codex", codex_config);

    // Point the agent chain at "test-codex".
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["test-codex".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_drain(AgentDrain::Development);

    // Clone the executor Arc so we can inspect it after the PhaseContext borrow ends.
    let executor = Arc::clone(&fixture.executor);

    {
        let mut ctx = fixture.ctx();
        ctx.developer_agent = "test-codex";
        // The mock executor returns success by default; ignore the result here.
        let _ = handler.invoke_development_agent(&mut ctx, 0);
    }

    let agent_calls = executor.agent_calls();
    assert_eq!(
        agent_calls.len(),
        1,
        "expected exactly one agent call, got {}",
        agent_calls.len()
    );
    assert_eq!(
        agent_calls[0].parser_type,
        JsonParserType::Codex,
        "expected parser_type to come from agent_config.json_parser (Codex), \
         got {:?} — hardcoded JsonParserType::default() (Claude) was used instead",
        agent_calls[0].parser_type
    );
}

#[test]
fn test_invoke_review_agent_uses_parser_type_from_agent_config() {
    use crate::agents::{AgentConfig, AgentDrain, AgentRegistry, JsonParserType};

    // Set up workspace with a review prompt.
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/review_prompt.txt", "test review prompt");
    let mut fixture = TestFixture::with_workspace(workspace);

    // Register a "test-opencode" agent configured with the OpenCode parser.
    // Default (buggy) behaviour uses JsonParserType::Claude regardless of this config.
    let opencode_config = AgentConfig {
        cmd: String::from("opencode"),
        json_parser: JsonParserType::OpenCode,
        ..AgentConfig::default()
    };
    fixture.registry = AgentRegistry::new()
        .unwrap()
        .register("test-opencode", opencode_config);

    // Point the agent chain at "test-opencode" with Review drain.
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["test-opencode".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        )
        .with_drain(AgentDrain::Review);

    // Clone the executor Arc so we can inspect it after the PhaseContext borrow ends.
    let executor = Arc::clone(&fixture.executor);

    {
        let mut ctx = fixture.ctx();
        ctx.reviewer_agent = "test-opencode";
        // The mock executor returns success by default; ignore the result here.
        let _ = handler.invoke_review_agent(&mut ctx, 0);
    }

    let agent_calls = executor.agent_calls();
    assert_eq!(
        agent_calls.len(),
        1,
        "expected exactly one agent call, got {}",
        agent_calls.len()
    );
    assert_eq!(
        agent_calls[0].parser_type,
        JsonParserType::OpenCode,
        "expected parser_type to come from agent_config.json_parser (OpenCode), \
         got {:?} — hardcoded JsonParserType::default() (Claude) was used instead",
        agent_calls[0].parser_type
    );
}

#[test]
fn test_invoke_fix_agent_uses_parser_type_from_agent_config() {
    use crate::agents::{AgentConfig, AgentDrain, AgentRegistry, JsonParserType};

    // Set up workspace with a fix prompt.
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/fix_prompt.txt", "test fix prompt");
    let mut fixture = TestFixture::with_workspace(workspace);

    // Register a "test-codex-gemini" agent using the codex command (recognized agent type)
    // but configured with the Gemini json_parser. This tests that json_parser from agent_config
    // flows through independently of the command-based agent type detection.
    // Default (buggy) behaviour uses JsonParserType::Claude regardless of this config.
    let codex_gemini_config = AgentConfig {
        cmd: String::from("codex"),
        json_parser: JsonParserType::Gemini,
        ..AgentConfig::default()
    };
    fixture.registry = AgentRegistry::new()
        .unwrap()
        .register("test-codex-gemini", codex_gemini_config);

    // Point the agent chain at "test-codex-gemini" with Fix drain.
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["test-codex-gemini".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        )
        .with_drain(AgentDrain::Fix);

    // Clone the executor Arc so we can inspect it after the PhaseContext borrow ends.
    let executor = Arc::clone(&fixture.executor);

    {
        let mut ctx = fixture.ctx();
        ctx.reviewer_agent = "test-codex-gemini";
        // The mock executor returns success by default; ignore the result here.
        let _ = handler.invoke_fix_agent(&mut ctx, 0);
    }

    let agent_calls = executor.agent_calls();
    assert_eq!(
        agent_calls.len(),
        1,
        "expected exactly one agent call, got {}",
        agent_calls.len()
    );
    assert_eq!(
        agent_calls[0].parser_type,
        JsonParserType::Gemini,
        "expected parser_type to come from agent_config.json_parser (Gemini), \
         got {:?} — hardcoded JsonParserType::default() (Claude) was used instead",
        agent_calls[0].parser_type
    );
}

#[test]
fn test_invoke_development_agent_forwards_env_vars_from_agent_config() {
    use crate::agents::{AgentConfig, AgentDrain, AgentRegistry, JsonParserType};
    use std::collections::HashMap;

    // Set up workspace with a development prompt.
    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_prompt.txt",
        "test development prompt",
    );
    let mut fixture = TestFixture::with_workspace(workspace);

    // Register an agent with non-empty env_vars.
    // The bug (fixed in commit 643c0f60) ignored agent_config.env_vars entirely,
    // passing an empty map to the executor instead.
    let mut agent_env = HashMap::new();
    agent_env.insert("MY_AGENT_KEY".to_string(), "agent_value_42".to_string());
    let agent_config = AgentConfig {
        cmd: String::from("codex"),
        json_parser: JsonParserType::Codex,
        env_vars: agent_env,
        ..AgentConfig::default()
    };
    fixture.registry = AgentRegistry::new()
        .unwrap()
        .register("test-env-agent", agent_config);

    // Point the agent chain at "test-env-agent".
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["test-env-agent".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_drain(AgentDrain::Development);

    let executor = Arc::clone(&fixture.executor);

    {
        let mut ctx = fixture.ctx();
        ctx.developer_agent = "test-env-agent";
        let _ = handler.invoke_development_agent(&mut ctx, 0);
    }

    let agent_calls = executor.agent_calls();
    assert_eq!(
        agent_calls.len(),
        1,
        "expected exactly one agent call, got {}",
        agent_calls.len()
    );
    assert!(
        agent_calls[0]
            .env
            .get("MY_AGENT_KEY")
            .map(|v| v == "agent_value_42")
            .unwrap_or(false),
        "expected agent_config.env_vars to be forwarded to the executor spawn config; \
         MY_AGENT_KEY not found or has wrong value in env: {:?}",
        agent_calls[0].env.get("MY_AGENT_KEY")
    );
}
