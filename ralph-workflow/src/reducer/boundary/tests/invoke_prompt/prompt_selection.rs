//! Prompt selection logic tests
//!
//! Tests for prompt selection behavior:
//! - Unique logfile paths with attempt numbers
//! - Retry prompt priorities (same-agent retry vs XSD retry vs rate limit continuation)
//! - Session ID management and reuse
//! - Collision-free logfile naming across different attempt contexts

use super::super::common::TestFixture;
use crate::agents::AgentRole;
use crate::executor::MockProcessExecutor;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{AgentEvent, PipelineEvent};
use crate::reducer::state::{AgentChainState, PipelineState};
use crate::workspace::MemoryWorkspace;
use std::sync::Arc;

#[test]
fn test_invoke_planning_agent_uses_unique_logfile_path_with_attempt() {
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/planning_prompt.txt", "planning prompt");
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
        AgentRole::Developer,
    );

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
    // New per-run log format: .agent/logs-<run_id>/agents/planning_1.log
    // Agent identity is in the log file header, not the filename
    assert!(
        calls[0].logfile.contains("/agents/planning_1.log"),
        "logfile should use per-run format with phase_index naming: {}",
        calls[0].logfile
    );
}

#[test]
fn test_invoke_agent_prefers_same_agent_retry_prompt_over_rate_limit_continuation_prompt() {
    let mut fixture = TestFixture::new();
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
        vec![vec![]],
        AgentRole::Developer,
    );
    let saved_prompt = "CONTINUATION PROMPT (stale)".to_string();
    handler.state.agent_chain.rate_limit_continuation_prompt =
        Some(crate::reducer::state::RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: saved_prompt,
        });
    handler.state.continuation.same_agent_retry_count = 1;
    handler.state.continuation.same_agent_retry_reason =
        Some(crate::reducer::state::SameAgentRetryReason::Timeout);

    let retry_preamble = crate::reducer::boundary::retry_guidance::same_agent_retry_preamble(
        &handler.state.continuation,
    );
    let retry_prompt = format!(
        "{retry_preamble}\n\
        ORIGINAL PROMPT BODY\n\
        RETRY PROMPT MARKER"
    );

    let _ = handler
        .invoke_agent(
            &mut ctx,
            crate::agents::AgentDrain::Development,
            AgentRole::Developer,
            "claude",
            None,
            retry_prompt,
        )
        .expect("invoke_agent should succeed");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    assert!(
        calls[0].prompt.contains("RETRY PROMPT MARKER"),
        "retry prompt marker should be present in effective prompt"
    );
    assert!(
        calls[0].prompt.starts_with("## Retry Note"),
        "effective prompt should preserve same-agent retry preamble"
    );
    assert!(
        !calls[0].prompt.contains("CONTINUATION PROMPT (stale)"),
        "effective prompt should not be overwritten by stale continuation prompt"
    );
}

#[test]
fn test_invoke_agent_prefers_xsd_retry_prompt_over_rate_limit_continuation_prompt() {
    let mut fixture = TestFixture::new();
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
        vec![vec![]],
        AgentRole::Developer,
    );
    handler.state.agent_chain.rate_limit_continuation_prompt =
        Some(crate::reducer::state::RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "CONTINUATION PROMPT (stale)".to_string(),
        });
    handler.state.continuation.xsd_retry_session_reuse_pending = true;

    let xsd_retry_prompt = "XSD RETRY PROMPT MARKER".to_string();
    let _ = handler
        .invoke_agent(
            &mut ctx,
            crate::agents::AgentDrain::Development,
            AgentRole::Developer,
            "claude",
            None,
            xsd_retry_prompt.clone(),
        )
        .expect("invoke_agent should succeed");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(
        calls[0].prompt, xsd_retry_prompt,
        "effective prompt should use the XSD retry prompt, not stale continuation prompt"
    );
}

#[test]
fn test_invoke_analysis_agent_does_not_use_rate_limit_continuation_prompt() {
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Plan\n\n- Do the thing\n");
    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.executor = Arc::new(
        MockProcessExecutor::new()
            .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
    );
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.phase = crate::reducer::event::PipelinePhase::Development;
    handler.state.iteration = 0;
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );
    let saved_prompt = "CONTINUATION PROMPT (stale)".to_string();
    handler.state.agent_chain.rate_limit_continuation_prompt =
        Some(crate::reducer::state::RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: saved_prompt.clone(),
        });

    let _ = handler
        .invoke_analysis_agent(&mut ctx, 0)
        .expect("invoke_analysis_agent should succeed");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    assert!(
        calls[0]
            .prompt
            .contains("independent, objective code verification agent"),
        "analysis invocation should use analysis prompt, not a stale continuation prompt"
    );
    assert_ne!(
        calls[0].prompt, saved_prompt,
        "analysis invocation must not be overridden by a role-mismatched continuation prompt"
    );
}

#[test]
fn test_xsd_retry_reuses_session_id_even_after_prompt_prepared_clears_pending() {
    use crate::reducer::state_reduction::reduce;

    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/planning_prompt.txt", "planning prompt");
    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.executor = Arc::new(
        MockProcessExecutor::new()
            .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
    );
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let session_id = "session-123".to_string();

    // Simulate an XSD retry: XsdValidationFailed sets xsd_retry_pending=true, then the
    // pipeline prepares an XSD retry prompt and (currently) clears xsd_retry_pending.
    let mut state = PipelineState::initial(1, 0);
    state.agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_session_id(Some(session_id.clone()));
    state.continuation.xsd_retry_pending = true;
    state.continuation.xsd_retry_session_reuse_pending = true;

    state = reduce(state, PipelineEvent::planning_prompt_prepared(0));
    assert!(
        !state.continuation.xsd_retry_pending,
        "prompt preparation clears xsd_retry_pending before invocation"
    );

    let mut handler = MainEffectHandler::new(state);
    let _ = handler
        .invoke_planning_agent(&mut ctx, 0)
        .expect("invoke_planning_agent should succeed");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    assert!(
        calls[0].args.iter().any(|a| a == "--resume"),
        "agent command should include session continuation flag for XSD retry"
    );
    assert!(
        calls[0].args.iter().any(|a| a == &session_id),
        "agent command should include session id value for XSD retry"
    );
}

#[test]
fn test_invoke_planning_agent_logfile_attempt_is_collision_free_and_does_not_depend_on_counter_magnitude(
) {
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/planning_prompt.txt", "planning prompt");
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
        AgentRole::Developer,
    );

    // This should not affect logfile attempt selection.
    handler.state.agent_chain.retry_cycle = u32::MAX;
    handler.state.continuation.continuation_attempt = 1;
    handler.state.continuation.xsd_retry_count = 1;

    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        handler.invoke_planning_agent(&mut ctx, 0)
    }));
    assert!(
        result.is_ok(),
        "invoke_planning_agent should not panic when attempt counter would overflow"
    );

    let effect_result = result
        .unwrap()
        .expect("invoke_planning_agent should succeed");

    assert!(matches!(
        effect_result.event,
        PipelineEvent::Agent(AgentEvent::InvocationStarted { .. })
    ));
    assert!(effect_result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    }));

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    // New per-run log format: .agent/logs-<run_id>/agents/planning_1.log
    // Agent identity is in the log file header, not the filename
    assert!(
        calls[0].logfile.contains("/agents/planning_1.log"),
        "logfile should use per-run format with phase_index naming: {}",
        calls[0].logfile
    );
}

#[test]
fn test_invoke_planning_agent_logfile_attempt_does_not_collide_across_distinct_attempt_context() {
    let workspace =
        MemoryWorkspace::new_test().with_file(".agent/tmp/planning_prompt.txt", "planning prompt");
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
        AgentRole::Developer,
    );

    // First invocation context: retry_cycle=0, continuation_attempt=100, xsd_retry_count=0
    handler.state.agent_chain.retry_cycle = 0;
    handler.state.continuation.continuation_attempt = 100;
    handler.state.continuation.xsd_retry_count = 0;
    let _ = handler
        .invoke_planning_agent(&mut ctx, 0)
        .expect("first invoke_planning_agent should succeed");

    // Second invocation context: retry_cycle=1, continuation_attempt=0, xsd_retry_count=0
    // Under the old packed arithmetic scheme, both contexts can map to the same attempt value
    // and therefore collide on logfile paths.
    handler.state.agent_chain.retry_cycle = 1;
    handler.state.continuation.continuation_attempt = 0;
    handler.state.continuation.xsd_retry_count = 0;
    let _ = handler
        .invoke_planning_agent(&mut ctx, 0)
        .expect("second invoke_planning_agent should succeed");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 2);
    assert_ne!(
        calls[0].logfile, calls[1].logfile,
        "logfile path must not collide across distinct attempt contexts"
    );
}
