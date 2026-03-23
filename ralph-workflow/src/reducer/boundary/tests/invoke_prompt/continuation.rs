//! Rate limit continuation prompt tests
//!
//! Tests for rate limit continuation prompt behavior:
//! - Using rate limit continuation prompt when available
//! - Using fresh prompt when no continuation prompt exists
//! - Analysis agent does not use continuation prompts

use super::super::common::TestFixture;
use crate::agents::{AgentDrain, AgentRole};
use crate::executor::MockProcessExecutor;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{AgentEvent, PipelineEvent};
use crate::reducer::state::{AgentChainState, PipelineState};
use crate::workspace::MemoryWorkspace;
use std::sync::Arc;

#[test]
fn test_invoke_agent_uses_rate_limit_continuation_prompt() {
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/planning_prompt.txt", "fresh prompt");
    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.executor = Arc::new(
        MockProcessExecutor::new()
            .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
    );
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec!["model-a".to_string()]],
        crate::agents::AgentRole::Developer,
    );
    // Simulate that a previous agent hit rate limit and saved the prompt
    handler.state.agent_chain.rate_limit_continuation_prompt =
        Some(crate::reducer::state::RateLimitContinuationPrompt {
            drain: AgentDrain::Planning,
            role: AgentRole::Developer,
            prompt: "saved prompt from rate limit".to_string(),
        });

    let result = handler
        .invoke_planning_agent(&mut ctx, 0)
        .expect("invoke_planning_agent should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Agent(AgentEvent::InvocationStarted { .. })
    ));
    assert!(result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    }));

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    // The saved prompt should have been used instead of "fresh prompt"
    assert_eq!(
        calls[0].prompt, "saved prompt from rate limit",
        "Agent should use rate_limit_continuation_prompt when available"
    );
}

/// Test that when `rate_limit_continuation_prompt` is None, the fresh prompt is used.
#[test]
fn test_invoke_agent_uses_fresh_prompt_when_no_continuation_prompt() {
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/planning_prompt.txt", "fresh prompt");
    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.executor = Arc::new(
        MockProcessExecutor::new()
            .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
    );
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec!["model-a".to_string()]],
        crate::agents::AgentRole::Developer,
    );
    // No rate_limit_continuation_prompt set
    assert!(handler
        .state
        .agent_chain
        .rate_limit_continuation_prompt
        .is_none());

    let result = handler
        .invoke_planning_agent(&mut ctx, 0)
        .expect("invoke_planning_agent should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Agent(AgentEvent::InvocationStarted { .. })
    ));
    assert!(result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    }));

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    // The fresh prompt should have been used
    assert_eq!(
        calls[0].prompt, "fresh prompt",
        "Agent should use fresh prompt when no rate_limit_continuation_prompt"
    );
}

#[test]
fn test_invoke_fix_agent_does_not_use_review_drain_continuation_prompt() {
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/fix_prompt.txt", "fresh fix prompt");
    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.executor = Arc::new(
        MockProcessExecutor::new()
            .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
    );
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "claude";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string()],
            vec![vec!["model-a".to_string()]],
            crate::agents::AgentRole::Reviewer,
        )
        .with_drain(AgentDrain::Fix);
    handler.state.agent_chain.rate_limit_continuation_prompt =
        Some(crate::reducer::state::RateLimitContinuationPrompt {
            drain: AgentDrain::Review,
            role: AgentRole::Reviewer,
            prompt: "stale review continuation prompt".to_string(),
        });

    let result = handler
        .invoke_fix_agent(&mut ctx, 0)
        .expect("invoke_fix_agent should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Agent(AgentEvent::InvocationStarted { .. })
    ));

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(
        calls[0].prompt, "fresh fix prompt",
        "fix drain must ignore continuation prompts captured for the review drain"
    );
}
