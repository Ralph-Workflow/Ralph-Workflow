//! Tests for planning phase events.

use std::sync::Arc;

use super::*;

#[test]
fn test_planning_phase_started_sets_planning_phase() {
    let state = create_test_state();
    let new_state = reduce(state, PipelineEvent::planning_phase_started());

    assert_eq!(new_state.phase, PipelinePhase::Planning);
}

#[test]
fn test_planning_phase_completed_transitions_to_development() {
    let state = create_state_in_phase(PipelinePhase::Planning);
    let new_state = reduce(state, PipelineEvent::planning_phase_completed());

    assert_eq!(new_state.phase, PipelinePhase::Development);
}

#[test]
fn test_planning_prompt_prepared_sets_iteration() {
    let state = create_test_state();
    let new_state = reduce(state, PipelineEvent::planning_prompt_prepared(1));

    assert_eq!(new_state.planning_prompt_prepared_iteration, Some(1));
}

#[test]
fn test_plan_generation_completed_transitions_to_development() {
    let state = create_state_in_phase(PipelinePhase::Planning);
    let new_state = reduce(state, PipelineEvent::plan_generation_completed(1, true));

    assert_eq!(new_state.phase, PipelinePhase::Development);
}

// ============================================================================
// XSD Retry Tests
// ============================================================================

#[test]
fn test_planning_output_validation_failed_increments_attempt() {
    let state = create_state_in_phase(PipelinePhase::Planning);
    assert_eq!(state.continuation.invalid_output_attempts, 0);

    // First validation failure should increment to 1
    let new_state = reduce(
        state,
        PipelineEvent::planning_output_validation_failed(1, 0),
    );

    assert_eq!(new_state.continuation.invalid_output_attempts, 1);
    assert_eq!(new_state.phase, PipelinePhase::Planning);
    assert_eq!(new_state.iteration, 1);
}

#[test]
fn test_planning_output_validation_failed_stays_in_planning_phase() {
    let mut state = create_state_in_phase(PipelinePhase::Planning);
    state.iteration = 1;

    // Validation failure should keep us in Planning phase
    let new_state = reduce(
        state,
        PipelineEvent::planning_output_validation_failed(1, 0),
    );

    assert_eq!(new_state.phase, PipelinePhase::Planning);
}

#[test]
fn test_planning_output_validation_failed_switches_agent_at_max_attempts() {
    use crate::reducer::state::ContinuationState;

    let mut state = create_state_in_phase(PipelinePhase::Planning);
    state.iteration = 1;
    state.continuation = ContinuationState {
        same_agent_retry_count: 1,
        same_agent_retry_pending: true,
        same_agent_retry_reason: Some(crate::reducer::state::SameAgentRetryReason::Timeout),
        xsd_retry_count: 1,
        max_xsd_retry_count: 2,
        ..ContinuationState::new()
    };

    // Initialize agent chain with multiple agents
    state.agent_chain.agents = Arc::from(vec!["agent1".to_string(), "agent2".to_string()]);
    state.agent_chain.current_agent_index = 0;

    // Attempt at MAX should trigger agent switch
    let new_state = reduce(
        state,
        PipelineEvent::planning_output_validation_failed(1, 0),
    );

    // Agent chain should have moved to next agent
    assert_eq!(new_state.agent_chain.current_agent_index, 1);
    // Attempts should be reset
    assert_eq!(new_state.continuation.invalid_output_attempts, 0);
    assert_eq!(
        new_state.continuation.same_agent_retry_count, 0,
        "Same-agent retry budget must not carry across agents"
    );
    assert!(
        !new_state.continuation.same_agent_retry_pending,
        "Same-agent retry pending must be cleared when switching agents"
    );
    assert!(
        new_state.continuation.same_agent_retry_reason.is_none(),
        "Same-agent retry reason must be cleared when switching agents"
    );
    // Still in Planning phase
    assert_eq!(new_state.phase, PipelinePhase::Planning);
}

#[test]
fn test_planning_output_validation_failed_preserves_iteration() {
    let mut state = create_state_in_phase(PipelinePhase::Planning);
    state.iteration = 3;

    let new_state = reduce(
        state,
        PipelineEvent::planning_output_validation_failed(3, 1),
    );

    assert_eq!(new_state.iteration, 3);
}

#[test]
fn test_planning_output_validation_failed_multiple_attempts_before_switch() {
    use crate::reducer::state::ContinuationState;

    let mut state = create_state_in_phase(PipelinePhase::Planning);
    state.continuation = ContinuationState {
        max_xsd_retry_count: 3,
        ..ContinuationState::new()
    };
    state.agent_chain.agents = Arc::from(vec!["agent1".to_string(), "agent2".to_string()]);
    state.agent_chain.current_agent_index = 0;

    // First failure (attempt 0) should not switch agent
    let state = reduce(
        state,
        PipelineEvent::planning_output_validation_failed(1, 0),
    );
    assert_eq!(state.agent_chain.current_agent_index, 0);
    assert_eq!(state.continuation.invalid_output_attempts, 1);

    // Second failure (attempt 1) should not switch agent when max is 3
    let state = reduce(
        state,
        PipelineEvent::planning_output_validation_failed(1, 1),
    );
    assert_eq!(state.agent_chain.current_agent_index, 0);
    assert_eq!(state.continuation.invalid_output_attempts, 2);
}

#[test]
fn test_planning_phase_started_resets_invalid_output_attempts() {
    let mut state = create_state_in_phase(PipelinePhase::Development);
    state.continuation.invalid_output_attempts = 3;

    let new_state = reduce(state, PipelineEvent::planning_phase_started());

    assert_eq!(new_state.continuation.invalid_output_attempts, 0);
    assert_eq!(new_state.phase, PipelinePhase::Planning);
}

#[test]
fn test_planning_phase_completed_resets_invalid_output_attempts() {
    let mut state = create_state_in_phase(PipelinePhase::Planning);
    state.continuation.invalid_output_attempts = 2;

    let new_state = reduce(state, PipelineEvent::planning_phase_completed());

    assert_eq!(new_state.continuation.invalid_output_attempts, 0);
    assert_eq!(new_state.phase, PipelinePhase::Development);
}

#[test]
fn test_plan_generation_completed_valid_resets_invalid_output_attempts() {
    let mut state = create_state_in_phase(PipelinePhase::Planning);
    state.continuation.invalid_output_attempts = 1;

    let new_state = reduce(state, PipelineEvent::plan_generation_completed(1, true));

    assert_eq!(new_state.continuation.invalid_output_attempts, 0);
    assert_eq!(new_state.phase, PipelinePhase::Development);
}
