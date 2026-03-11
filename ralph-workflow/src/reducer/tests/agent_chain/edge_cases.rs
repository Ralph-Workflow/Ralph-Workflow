//! Edge case tests for agent chain.
//!
//! Tests for wrapping behavior, single-agent chains, timeout handling,
//! and integration-style event loop simulations.

use crate::agents::AgentRole;
use crate::reducer::event::{AgentErrorKind, TimeoutOutputKind};
use crate::reducer::tests::*;

#[test]
fn test_agent_invocation_failed_retriable_network_on_last_model_wraps_to_first_model() {
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: base_state
            .agent_chain
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec!["model1".to_string(), "model2".to_string()]],
                AgentRole::Developer,
            )
            .advance_to_next_model(), // Move to model index 1 (last model)
        ..base_state
    };

    // Verify we're on the last model
    assert_eq!(state.agent_chain.current_model_index, 1);

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

    // Should wrap back to first model (1 -> 0)
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 0);
}

#[test]
fn test_agent_fallback_from_last_agent_wraps_and_increments_cycle() {
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: base_state
            .agent_chain
            .with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec!["model1".to_string()], vec!["model2".to_string()]],
                AgentRole::Developer,
            )
            .switch_to_next_agent(), // Move to agent index 1 (last agent)
        ..base_state
    };

    // Verify we're on the last agent and cycle is 0
    assert_eq!(state.agent_chain.current_agent_index, 1);
    assert_eq!(state.agent_chain.retry_cycle, 0);

    let new_state = reduce(
        state,
        PipelineEvent::agent_fallback_triggered(
            AgentRole::Developer,
            "agent2".to_string(),
            "agent1".to_string(),
        ),
    );

    // Should wrap back to first agent (1 -> 0) and increment retry_cycle (0 -> 1)
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 0);
    assert_eq!(new_state.agent_chain.retry_cycle, 1);
}

#[test]
fn test_agent_invocation_failed_retriable_network_on_single_model_wraps() {
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: base_state.agent_chain.with_agents(
            vec!["agent1".to_string()],
            vec![vec!["model1".to_string()]], // Only one model
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

    // With only one model, should wrap back to index 0
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 0);
}

// ============================================================================
// Timeout Fallback Tests
// ============================================================================

#[test]
fn test_timed_out_retries_same_agent_before_fallback() {
    // Setup: two agents, each with two models. Same-agent retry budget 2 means:
    // - First timeout retries same agent
    // - Second timeout falls back to next agent
    let base_state = create_test_state();
    let state = PipelineState {
        continuation: crate::reducer::state::ContinuationState::with_limits(2, 3, 2),
        agent_chain: base_state.agent_chain.with_agents(
            vec!["agent-a".to_string(), "agent-b".to_string()],
            vec![
                vec!["model-a1".to_string(), "model-a2".to_string()],
                vec!["model-b1".to_string()],
            ],
            AgentRole::Developer,
        ),
        ..base_state
    };

    assert_eq!(
        state.agent_chain.current_agent().map(String::as_str),
        Some("agent-a")
    );
    assert_eq!(state.agent_chain.current_model_index, 0);

    let after_first_timeout = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            "agent-a".to_string(),
            TimeoutOutputKind::PartialOutput,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    // Timeout retries MUST NOT reuse XSD retry mechanism.
    assert!(
        !after_first_timeout.continuation.xsd_retry_pending,
        "Timeout retry should not set xsd_retry_pending (XSD retry is only for invalid XML)"
    );
    assert_eq!(
        after_first_timeout.continuation.xsd_retry_count, 0,
        "Timeout retry should not increment xsd_retry_count (XSD retry is only for invalid XML)"
    );

    assert_eq!(
        after_first_timeout
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent-a"),
        "Timeout should retry same agent first"
    );
    assert_eq!(
        after_first_timeout.agent_chain.current_model_index, 0,
        "Timeout retry should not advance model"
    );

    let after_second_timeout = reduce(
        after_first_timeout,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            "agent-a".to_string(),
            TimeoutOutputKind::PartialOutput,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    assert_eq!(
        after_second_timeout
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent-b"),
        "After retry budget exhaustion, timeout should fall back to next agent"
    );
    assert_eq!(
        after_second_timeout.agent_chain.current_model_index, 0,
        "Model index should reset to 0 when switching agents"
    );
}

#[test]
fn test_internal_error_retries_same_agent_before_fallback_without_xsd_retry() {
    use crate::reducer::event::AgentErrorKind;

    let base_state = create_test_state();
    let state = PipelineState {
        continuation: crate::reducer::state::ContinuationState::with_limits(2, 3, 2),
        agent_chain: base_state.agent_chain.with_agents(
            vec!["agent-a".to_string(), "agent-b".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        ..base_state
    };

    let after_first_failure = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            "agent-a".to_string(),
            1,
            AgentErrorKind::InternalError,
            false,
        ),
    );

    assert_eq!(
        after_first_failure
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent-a"),
        "Internal error should retry same agent first"
    );
    assert!(
        !after_first_failure.continuation.xsd_retry_pending,
        "Internal error retry should not set xsd_retry_pending (XSD retry is only for invalid XML)"
    );
    assert_eq!(
        after_first_failure.continuation.xsd_retry_count, 0,
        "Internal error retry should not increment xsd_retry_count (XSD retry is only for invalid XML)"
    );
}

#[test]
fn test_timed_out_partial_output_preserves_session_id_for_context_retry() {
    // AC-1: PartialOutput timeout should preserve session ID for context reuse
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: base_state
            .agent_chain
            .with_agents(
                vec!["agent-a".to_string(), "agent-b".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            )
            .with_session_id(Some("session-123".to_string())),
        ..base_state
    };

    // Verify session ID is set
    assert_eq!(
        state.agent_chain.last_session_id,
        Some("session-123".to_string())
    );

    // Apply PartialOutput timeout - should preserve session for context reuse
    let new_state = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            "agent-a".to_string(),
            TimeoutOutputKind::PartialOutput,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    assert!(
        !new_state.continuation.xsd_retry_pending,
        "Timeout retry should not set xsd_retry_pending (XSD retry is only for invalid XML)"
    );

    // Session ID should be PRESERVED for TimeoutWithContext (PartialOutput)
    assert_eq!(
        new_state.agent_chain.last_session_id,
        Some("session-123".to_string()),
        "PartialOutput timeout should preserve session ID for context reuse"
    );

    // Should set session reuse pending flag
    assert!(
        new_state.continuation.xsd_retry_session_reuse_pending,
        "PartialOutput timeout should set xsd_retry_session_reuse_pending"
    );
}

#[test]
fn test_timed_out_no_output_clears_session_id_for_immediate_switch() {
    // AC-2: NoOutput timeout should clear session ID (immediate agent switch)
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: base_state
            .agent_chain
            .with_agents(
                vec!["agent-a".to_string(), "agent-b".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            )
            .with_session_id(Some("session-123".to_string())),
        ..base_state
    };

    // Verify session ID is set
    assert_eq!(
        state.agent_chain.last_session_id,
        Some("session-123".to_string())
    );

    // Apply NoOutput timeout - should clear session and switch agents immediately
    let new_state = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            "agent-a".to_string(),
            TimeoutOutputKind::NoOutput,
            None,
            None,
        ),
    );

    // Session ID should be CLEARED for NoOutput (immediate agent switch)
    assert_eq!(
        new_state.agent_chain.last_session_id, None,
        "NoOutput timeout should clear session ID (immediate agent switch)"
    );

    // Should NOT set session reuse pending flag
    assert!(
        !new_state.continuation.xsd_retry_session_reuse_pending,
        "NoOutput timeout should not set xsd_retry_session_reuse_pending"
    );
}

#[test]
fn test_timed_out_from_last_agent_increments_retry_cycle_when_budget_exhausted() {
    // This test verifies behavior when same-agent retry budget is exhausted and we're on the
    // last agent in the chain.
    //
    // With the "retry same agent first" policy:
    // - First timeout => retry same agent (same_agent_retry_count=1)
    // - Second timeout => retry budget exhausted (count=2 >= max=2), fall back
    // - Falling back from last agent => wrap to first agent and increment retry_cycle
    let base_state = create_test_state();
    let state = PipelineState {
        continuation: crate::reducer::state::ContinuationState::with_limits(1, 3, 2)
            .with_max_same_agent_retry(2), // Fallback on the 2nd timeout when max=2
        agent_chain: base_state
            .agent_chain
            .with_agents(
                vec!["agent-a".to_string(), "agent-b".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            )
            .switch_to_next_agent(), // Move to last agent (agent-b)
        ..base_state
    };

    // Verify we're on the last agent
    assert_eq!(
        state.agent_chain.current_agent().map(String::as_str),
        Some("agent-b")
    );
    assert_eq!(state.agent_chain.retry_cycle, 0);
    assert_eq!(state.continuation.same_agent_retry_count, 0);

    // First timeout: should retry same agent, not fall back yet
    let after_first_timeout = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            "agent-b".to_string(),
            TimeoutOutputKind::PartialOutput,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    assert!(
        !after_first_timeout.continuation.xsd_retry_pending,
        "Timeout retry should not set xsd_retry_pending (XSD retry is only for invalid XML)"
    );
    assert_eq!(
        after_first_timeout
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent-b"),
        "First timeout should retry same agent, not fall back"
    );
    assert_eq!(after_first_timeout.continuation.same_agent_retry_count, 1);
    assert!(after_first_timeout.continuation.same_agent_retry_pending);

    // Second timeout: same-agent retry budget exhausted (count=2 >= max=2), fall back
    // Since we're on the last agent, this should wrap to first agent and increment retry_cycle
    let after_second_timeout = reduce(
        after_first_timeout,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            "agent-b".to_string(),
            TimeoutOutputKind::PartialOutput,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    // Should wrap back to first agent and increment retry cycle
    assert_eq!(
        after_second_timeout
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent-a"),
        "Should wrap back to first agent after exhausting retry budget on last agent"
    );
    assert_eq!(
        after_second_timeout.agent_chain.retry_cycle, 1,
        "Should increment retry cycle when wrapping"
    );
    // Retry counters should be reset after agent switch
    assert_eq!(after_second_timeout.continuation.same_agent_retry_count, 0);
    assert!(!after_second_timeout.continuation.same_agent_retry_pending);
}

// ============================================================================
// Integration-Style Tests (Event Loop Simulation)
// ============================================================================

/// Simulates running the event loop to verify backoff wait does not cause infinite loops.
///
/// This test starts with a state that has `backoff_pending_ms=Some(...)` and runs
/// through the effect/reduce cycle to verify the pipeline progresses correctly
/// without getting stuck repeating `BackoffWait` effects.
#[test]
fn test_backoff_wait_does_not_cause_infinite_loop_in_event_loop_simulation() {
    use crate::reducer::effect::Effect;
    use crate::reducer::orchestration::determine_next_effect;
    use crate::reducer::state::{AgentChainState, ContinuationState};

    let mut state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_max_cycles(2),
        continuation: ContinuationState::default(),
        development_context_prepared_iteration: Some(1),
        development_prompt_prepared_iteration: Some(1),
        development_required_files_cleaned_iteration: Some(1),
        ..create_test_state()
    };
    // Set backoff pending to trigger the backoff wait path
    state.agent_chain.backoff_pending_ms = Some(100);

    let max_iterations = 20;
    let mut backoff_wait_count = 0;

    for _ in 0..max_iterations {
        let effect = determine_next_effect(&state);

        match effect {
            Effect::BackoffWait { role, cycle, .. } => {
                backoff_wait_count += 1;
                assert!(
                    backoff_wait_count <= 2,
                    "BackoffWait repeated {backoff_wait_count} times - potential infinite loop"
                );
                // Simulate handler emitting RetryCycleStarted
                state = reduce(state, PipelineEvent::agent_retry_cycle_started(role, cycle));
            }
            _ => {
                // Successfully progressed past backoff - test passes
                break;
            }
        }
    }

    // Verify backoff_pending_ms was cleared
    assert!(
        state.agent_chain.backoff_pending_ms.is_none(),
        "backoff_pending_ms should be cleared after RetryCycleStarted event"
    );
}
