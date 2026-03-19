//! Basic agent fallback tests.
//!
//! Tests simple scenarios where agents fail and the pipeline falls back
//! to the next agent in the chain.

use crate::reducer::state::RateLimitContinuationPrompt;
use crate::reducer::state_reduction::tests::*;

#[test]
fn test_reduce_agent_fallback_to_next_model() {
    let state = create_test_state();
    let initial_agent = state.agent_chain.current_agent().unwrap().clone();
    let initial_model_index = state.agent_chain.current_model_index;

    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            initial_agent,
            1,
            AgentErrorKind::Network,
            true,
        ),
    );

    assert_ne!(
        new_state.agent_chain.current_model_index,
        initial_model_index
    );
}

#[test]
fn test_reduce_all_agent_failure_scenarios() {
    let state = create_test_state();
    let initial_agent_index = state.agent_chain.current_agent_index;
    let initial_model_index = state.agent_chain.current_model_index;
    let agent_name = state.agent_chain.current_agent().unwrap().clone();

    let network_error_state = reduce(
        state.clone(),
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            agent_name.clone(),
            1,
            AgentErrorKind::Network,
            true,
        ),
    );
    assert_eq!(
        network_error_state.agent_chain.current_agent_index,
        initial_agent_index
    );
    assert!(network_error_state.agent_chain.current_model_index > initial_model_index);

    let auth_error_state = reduce(
        state.clone(),
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            agent_name.clone(),
            1,
            AgentErrorKind::Authentication,
            false,
        ),
    );
    assert!(auth_error_state.agent_chain.current_agent_index > initial_agent_index);
    assert_eq!(
        auth_error_state.agent_chain.current_model_index,
        initial_model_index
    );

    let internal_error_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            agent_name,
            139,
            AgentErrorKind::InternalError,
            false,
        ),
    );
    assert_eq!(
        internal_error_state.agent_chain.current_agent_index, initial_agent_index,
        "InternalError should retry same agent first, not immediately fall back"
    );
    assert!(
        internal_error_state.continuation.same_agent_retry_pending,
        "InternalError retry should set same_agent_retry_pending so orchestration can select retry prompt mode"
    );
    assert_eq!(
        internal_error_state.continuation.same_agent_retry_reason,
        Some(SameAgentRetryReason::InternalError)
    );
}

#[test]
fn test_reduce_agent_fallback_triggers_fallback_event() {
    let state = create_test_state();
    let agent = state.agent_chain.current_agent().unwrap().clone();

    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            agent,
            1,
            AgentErrorKind::Authentication,
            false,
        ),
    );

    assert!(new_state.agent_chain.current_agent_index > 0);
}

#[test]
fn test_reduce_model_fallback_triggers_for_network_error() {
    let state = create_test_state();
    let initial_model_index = state.agent_chain.current_model_index;
    let agent_name = state.agent_chain.current_agent().unwrap().clone();

    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            agent_name,
            1,
            AgentErrorKind::Network,
            true,
        ),
    );

    assert!(new_state.agent_chain.current_model_index > initial_model_index);
}

#[test]
fn test_fallback_triggered_respects_to_agent_and_resets_retry_state() {
    let base_state = create_test_state();
    let mut chain = AgentChainState::initial().with_agents(
        vec![
            "agent1".to_string(),
            "agent2".to_string(),
            "agent3".to_string(),
        ],
        vec![vec![], vec![], vec![]],
        AgentRole::Developer,
    );
    chain.rate_limit_continuation_prompt = Some(RateLimitContinuationPrompt {
        drain: crate::agents::AgentDrain::Development,
        role: AgentRole::Developer,
        prompt: "saved prompt".to_string(),
    });
    chain.last_session_id = Some("session-xyz".to_string());

    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: chain,
        continuation: ContinuationState {
            xsd_retry_count: 3,
            xsd_retry_pending: true,
            same_agent_retry_count: 1,
            same_agent_retry_pending: true,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::with_limits(2, 3, 2)
        },
        ..base_state
    };

    let new_state = reduce(
        state,
        PipelineEvent::agent_fallback_triggered(
            AgentRole::Developer,
            "agent1".to_string(),
            "agent3".to_string(),
        ),
    );

    assert_eq!(
        new_state.agent_chain.current_agent().map(String::as_str),
        Some("agent3"),
        "FallbackTriggered should respect to_agent instead of blindly switching to the next agent"
    );
    assert!(new_state
        .agent_chain
        .rate_limit_continuation_prompt
        .is_none());
    assert!(new_state.agent_chain.last_session_id.is_none());
    assert_eq!(new_state.continuation.xsd_retry_count, 0);
    assert!(!new_state.continuation.xsd_retry_pending);
    assert_eq!(new_state.continuation.same_agent_retry_count, 0);
    assert!(!new_state.continuation.same_agent_retry_pending);
    assert!(new_state.continuation.same_agent_retry_reason.is_none());
}
