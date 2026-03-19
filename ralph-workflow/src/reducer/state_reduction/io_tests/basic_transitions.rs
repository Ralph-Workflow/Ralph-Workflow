// Basic pipeline transition tests.
//
// Tests for fundamental state transitions: pipeline started/completed,
// development iteration completed, plan generation, phase transitions.

use super::*;

#[test]
fn test_reduce_pipeline_started() {
    let state = create_test_state();
    let new_state = reduce(state, PipelineEvent::pipeline_started());
    assert_eq!(new_state.phase, PipelinePhase::Planning);
}

#[test]
fn test_reduce_pipeline_completed() {
    let state = create_test_state();
    let new_state = reduce(state, PipelineEvent::pipeline_completed());
    assert_eq!(new_state.phase, PipelinePhase::Complete);
}

#[test]
fn test_reduce_development_iteration_completed() {
    // DevelopmentIterationCompleted transitions to CommitMessage phase
    // The iteration counter stays the same; it gets incremented by CommitCreated
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        ..create_test_state()
    };
    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(2, true),
    );
    // Iteration stays at 2 (incremented by CommitCreated later)
    assert_eq!(new_state.iteration, 2);
    // Goes to CommitMessage phase to create a commit
    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    // Previous phase stored for return after commit
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::Development));
}

#[test]
fn test_reduce_development_iteration_complete_goes_to_commit() {
    // Even on last iteration, DevelopmentIterationCompleted goes to CommitMessage
    // The transition to Review happens after CommitCreated
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 5,
        total_iterations: 5,
        ..create_test_state()
    };
    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(5, true),
    );
    // Iteration stays at 5 (incremented by CommitCreated later)
    assert_eq!(new_state.iteration, 5);
    // Goes to CommitMessage phase first
    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
}

#[test]
fn test_reduce_development_iteration_completed_invalid_output_switch_clears_agent_scoped_state() {
    use crate::reducer::state::{ContinuationState, MAX_DEV_INVALID_OUTPUT_RERUNS};

    let agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["agent-a".to_string(), "agent-b".to_string()],
            vec![vec!["model-a".to_string()], vec!["model-b".to_string()]],
            AgentRole::Developer,
        )
        .with_session_id(Some("session-a".to_string()));

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        agent_chain,
        continuation: ContinuationState {
            invalid_output_attempts: MAX_DEV_INVALID_OUTPUT_RERUNS,
            xsd_retry_count: 7,
            xsd_retry_pending: true,
            xsd_retry_session_reuse_pending: true,
            same_agent_retry_count: 2,
            same_agent_retry_pending: true,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(0, false),
    );

    assert_eq!(new_state.phase, PipelinePhase::Development);
    assert_eq!(new_state.agent_chain.current_agent_index, 1);
    assert_eq!(
        new_state.agent_chain.last_session_id, None,
        "Switching agents must clear the previous agent session id"
    );
    assert_eq!(new_state.continuation.invalid_output_attempts, 0);
    assert_eq!(new_state.continuation.xsd_retry_count, 0);
    assert!(!new_state.continuation.xsd_retry_pending);
    assert!(!new_state.continuation.xsd_retry_session_reuse_pending);
    assert_eq!(new_state.continuation.same_agent_retry_count, 0);
    assert!(!new_state.continuation.same_agent_retry_pending);
    assert_eq!(new_state.continuation.same_agent_retry_reason, None);
}

#[test]
fn test_plan_generation_completed_invalid_does_not_transition_to_development() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        ..create_test_state()
    };

    let new_state = reduce(state, PipelineEvent::plan_generation_completed(1, false));

    assert_eq!(
        new_state.phase,
        PipelinePhase::Planning,
        "Invalid plan should keep pipeline in Planning phase"
    );
}

#[test]
fn test_reduce_phase_transitions() {
    let mut state = create_test_state();

    state = reduce(state, PipelineEvent::planning_phase_completed());
    assert_eq!(state.phase, PipelinePhase::Development);

    state = reduce(state, PipelineEvent::development_phase_started());
    assert_eq!(state.phase, PipelinePhase::Development);

    state = reduce(state, PipelineEvent::development_phase_completed());
    assert_eq!(state.phase, PipelinePhase::Review);

    state = reduce(state, PipelineEvent::review_phase_started());
    assert_eq!(state.phase, PipelinePhase::Review);

    state = reduce(state, PipelineEvent::review_phase_completed(false));
    assert_eq!(state.phase, PipelinePhase::CommitMessage);
}
