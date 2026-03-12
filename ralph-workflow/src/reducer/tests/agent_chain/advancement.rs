//! Agent and model advancement tests.
//!
//! Tests for moving through the agent chain and model lists during failures
//! and fallback scenarios.

use crate::agents::AgentRole;
use crate::reducer::event::AgentErrorKind;
use crate::reducer::tests::*;

#[test]
fn test_agent_invocation_started_preserves_agent_chain_indices() {
    let base_state = create_test_state();
    let mut agent_chain = base_state.agent_chain.with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![
            vec!["model1".to_string()],
            vec!["model2".to_string(), "model3".to_string()],
        ],
        AgentRole::Developer,
    );
    agent_chain.retry_cycle = 2;
    agent_chain.rate_limit_continuation_prompt =
        Some(crate::reducer::state::RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        });

    // Start from a non-zero position so the test actually verifies reset behavior.
    let state = PipelineState {
        agent_chain: agent_chain.switch_to_next_agent().advance_to_next_model(),
        ..base_state
    };

    assert_eq!(state.agent_chain.current_agent_index, 1);
    assert_eq!(state.agent_chain.current_model_index, 1);
    assert_eq!(state.agent_chain.retry_cycle, 2);

    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_started(
            AgentRole::Developer,
            "agent1".to_string(),
            Some("model1".to_string()),
        ),
    );

    // InvocationStarted should not change indices or cycle tracking, and must not clear any
    // saved continuation prompt. After a 429, the prompt must remain available for retries
    // until an invocation succeeds.
    assert_eq!(new_state.agent_chain.current_agent_index, 1);
    assert_eq!(new_state.agent_chain.current_model_index, 1);
    assert_eq!(new_state.agent_chain.retry_cycle, 2);
    assert_eq!(
        new_state.agent_chain.rate_limit_continuation_prompt,
        Some(crate::reducer::state::RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        })
    );
}

#[test]
fn test_agent_invocation_succeeded_preserves_indices() {
    let state = create_test_state();
    let new_state = reduce(
        state.clone(),
        PipelineEvent::agent_invocation_succeeded(AgentRole::Developer, "agent1".to_string()),
    );

    assert_eq!(
        new_state.agent_chain.current_agent_index,
        state.agent_chain.current_agent_index
    );
    assert_eq!(
        new_state.agent_chain.current_model_index,
        state.agent_chain.current_model_index
    );
}

#[test]
fn test_agent_invocation_failed_with_retriable_network_advances_model() {
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: base_state.agent_chain.with_agents(
            vec!["agent1".to_string()],
            vec![vec!["model1".to_string(), "model2".to_string()]],
            AgentRole::Developer,
        ),
        ..base_state
    };

    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            "agent1".to_string(),
            1,
            AgentErrorKind::Network,
            true,
        ),
    );

    // Should advance to next model (0 -> 1)
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 1);
}

#[test]
fn test_agent_fallback_triggered_switches_agent() {
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: base_state.agent_chain.with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec!["model1".to_string()], vec!["model2".to_string()]],
            AgentRole::Developer,
        ),
        ..base_state
    };

    let new_state = reduce(
        state,
        PipelineEvent::agent_fallback_triggered(
            AgentRole::Developer,
            "agent1".to_string(),
            "agent2".to_string(),
        ),
    );

    // Should switch to next agent (0 -> 1) and reset model (0)
    assert_eq!(new_state.agent_chain.current_agent_index, 1);
    assert_eq!(new_state.agent_chain.current_model_index, 0);
}

#[test]
fn test_agent_chain_exhausted_increments_retry_cycle() {
    let state = create_test_state();
    let initial_retry_cycle = state.agent_chain.retry_cycle;

    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_exhausted(AgentRole::Developer),
    );

    assert_eq!(new_state.agent_chain.retry_cycle, initial_retry_cycle + 1);
}

#[test]
fn test_agent_chain_exhausted_resets_indices() {
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: base_state.agent_chain.with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![
                vec!["model1".to_string(), "model2".to_string()],
                vec!["model3".to_string()],
            ],
            AgentRole::Developer,
        ),
        ..base_state
    };

    // Manually set indices to non-zero
    let state = PipelineState {
        agent_chain: state
            .agent_chain
            .advance_to_next_model()
            .switch_to_next_agent(),
        ..state
    };

    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_exhausted(AgentRole::Developer),
    );

    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 0);
}

#[test]
fn test_agent_model_fallback_triggered_advances_to_next_model() {
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: base_state.agent_chain.with_agents(
            vec!["agent1".to_string()],
            vec![vec![
                "model1".to_string(),
                "model2".to_string(),
                "model3".to_string(),
            ]],
            AgentRole::Developer,
        ),
        ..base_state
    };

    // Start at model index 0
    assert_eq!(state.agent_chain.current_model_index, 0);

    let new_state = reduce(
        state,
        PipelineEvent::agent_model_fallback_triggered(
            AgentRole::Developer,
            "agent1".to_string(),
            "model1".to_string(),
            "model2".to_string(),
        ),
    );

    // Should advance to next model (0 -> 1)
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 1);
}

#[test]
fn test_agent_retry_cycle_started_clears_backoff_pending() {
    let mut base_state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 3,
        reviewer_pass: 1,
        ..create_test_state()
    };
    // Set backoff_pending_ms to verify it gets cleared
    base_state.agent_chain.backoff_pending_ms = Some(1000);

    let new_state = reduce(
        base_state.clone(),
        PipelineEvent::agent_retry_cycle_started(AgentRole::Developer, 2),
    );

    // RetryCycleStarted clears backoff_pending_ms to allow the retry cycle to proceed.
    // Other state fields are preserved.
    assert_eq!(new_state.phase, base_state.phase);
    assert_eq!(new_state.iteration, base_state.iteration);
    assert_eq!(new_state.reviewer_pass, base_state.reviewer_pass);
    assert_eq!(
        new_state.agent_chain.current_agent_index,
        base_state.agent_chain.current_agent_index
    );
    assert_eq!(
        new_state.agent_chain.current_model_index,
        base_state.agent_chain.current_model_index
    );
    // Verify backoff_pending_ms is cleared (the critical behavior)
    assert!(
        new_state.agent_chain.backoff_pending_ms.is_none(),
        "backoff_pending_ms should be cleared after RetryCycleStarted"
    );
}

#[test]
fn test_agent_invocation_failed_non_retriable_retries_same_agent_until_budget_exhausted() {
    use crate::reducer::state::ContinuationState;

    let base_state = create_test_state();
    let mut continuation = ContinuationState::with_limits(2, 3, 2);
    continuation.xsd_retry_count = 7;
    continuation.xsd_retry_pending = true;
    let state = PipelineState {
        agent_chain: base_state.agent_chain.with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec!["model1".to_string()], vec!["model2".to_string()]],
            AgentRole::Developer,
        ),
        continuation,
        ..base_state
    };

    // Start on first agent
    assert_eq!(state.agent_chain.current_agent_index, 0);

    let after_first_failure = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            "agent1".to_string(),
            1,
            AgentErrorKind::ParsingError,
            false,
        ),
    );

    assert_eq!(after_first_failure.agent_chain.current_agent_index, 0);
    assert!(after_first_failure.continuation.same_agent_retry_pending);

    let after_second_failure = reduce(
        after_first_failure,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            "agent1".to_string(),
            1,
            AgentErrorKind::ParsingError,
            false,
        ),
    );

    assert_eq!(after_second_failure.agent_chain.current_agent_index, 1);
    assert_eq!(after_second_failure.agent_chain.current_model_index, 0);
    assert_eq!(
        after_second_failure.continuation.xsd_retry_count, 0,
        "XSD retry budget must not carry across agents"
    );
    assert!(
        !after_second_failure.continuation.xsd_retry_pending,
        "XSD retry pending must be cleared when switching agents"
    );
}
