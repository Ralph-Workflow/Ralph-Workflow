// Basic pipeline transition tests.
//
// Tests for fundamental state transitions: development iteration completed,
// plan generation, phase transitions.

use super::*;

#[test]
fn test_reduce_development_iteration_completed() {
    // Commit-gated progression: DevelopmentIterationCompleted transitions to CommitMessage.
    // After development_commit completes, compute_post_commit_transition routes to
    // Planning (if cycles remain) or Review (if budget exhausted).
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
    // Iteration stays at 2 (incremented after commit completes)
    assert_eq!(new_state.iteration, 2);
    // Goes to CommitMessage for development_commit before Planning/Review
    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    // Previous phase stored for post-commit routing
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::Development));
}

#[test]
fn test_reduce_development_iteration_complete_goes_to_commit_message() {
    // Commit-gated progression: after development cycles complete, route to CommitMessage.
    // Post-commit routing (compute_post_commit_transition) then routes to Review when budget
    // is exhausted.
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
    // Iteration stays at 5 (incremented after commit completes)
    assert_eq!(new_state.iteration, 5);
    // Goes to CommitMessage first (commit-gated progression)
    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
}

#[test]
fn test_reduce_development_iteration_completed_invalid_output_switch_clears_agent_scoped_state() {
    use crate::reducer::state::{ContinuationState, MAX_VALIDATION_RETRY_ATTEMPTS};

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
            invalid_output_attempts: MAX_VALIDATION_RETRY_ATTEMPTS,
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
    let state = create_test_state();
    let state = reduce(state, PipelineEvent::planning_phase_completed());
    assert_eq!(state.phase, PipelinePhase::Development);

    let state = reduce(state, PipelineEvent::development_phase_started());
    assert_eq!(state.phase, PipelinePhase::Development);

    let state = reduce(state, PipelineEvent::development_phase_completed());
    assert_eq!(state.phase, PipelinePhase::Review);

    let state = reduce(state, PipelineEvent::review_phase_started());
    assert_eq!(state.phase, PipelinePhase::Review);

    let state = reduce(state, PipelineEvent::review_phase_completed(false));
    assert_eq!(state.phase, PipelinePhase::CommitMessage);
}
