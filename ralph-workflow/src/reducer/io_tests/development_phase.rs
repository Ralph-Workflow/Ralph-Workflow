//! Tests for development phase events (iterations, plan generation).

use super::*;
use crate::reducer::state::{
    AgentChainState, ContinuationState, DevelopmentStatus, MAX_DEV_INVALID_OUTPUT_RERUNS,
};

#[test]
fn test_development_phase_started_sets_development_phase() {
    let state = create_test_state();
    let new_state = reduce(state, PipelineEvent::development_phase_started());

    assert_eq!(new_state.phase, PipelinePhase::Development);
}

#[test]
fn test_development_iteration_started_sets_iteration() {
    let state = create_test_state();
    let new_state = reduce(state, PipelineEvent::development_iteration_started(3));

    assert_eq!(new_state.iteration, 3);
}

#[test]
fn test_development_iteration_started_resets_agent_chain() {
    let base_state = create_test_state();
    // Setup agent chain with multiple agents, models, and retry_cycle
    let mut agent_chain = base_state.agent_chain.with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![
            vec!["model1".to_string(), "model2".to_string()],
            vec!["model3".to_string()],
        ],
        crate::agents::AgentRole::Developer,
    );
    agent_chain = agent_chain.switch_to_next_agent(); // Move to agent index 1
    agent_chain.retry_cycle = 5; // Manually set retry_cycle to verify preservation

    let state = PipelineState {
        agent_chain,
        ..base_state
    };

    // Verify we're at agent 1 with retry_cycle = 5
    assert_eq!(state.agent_chain.current_agent_index, 1);
    assert_eq!(state.agent_chain.retry_cycle, 5);

    let new_state = reduce(state, PipelineEvent::development_iteration_started(1));

    // Iteration should be set
    assert_eq!(new_state.iteration, 1);

    // Agent chain should be reset (indices to 0, but retry_cycle preserved)
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 0);
    assert_eq!(new_state.agent_chain.retry_cycle, 5); // Preserved, not reset
}

#[test]
fn test_development_iteration_completed_increments_iteration() {
    // After dev iteration completes, go to CommitMessage (don't increment yet)
    // Iteration increments after commit is created
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

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert_eq!(new_state.iteration, 2); // Don't increment yet

    // After commit, increment and go to Planning
    let new_state = reduce(
        new_state,
        PipelineEvent::commit_created("abc123".to_string(), "test".to_string()),
    );

    assert_eq!(new_state.iteration, 3); // NOW increment
    assert_eq!(new_state.phase, PipelinePhase::Planning);
}

#[test]
fn test_development_iteration_completed_does_not_transition_when_output_invalid() {
    let continuation = ContinuationState::new().trigger_continuation(
        DevelopmentStatus::Partial,
        "partial work".to_string(),
        Some(vec!["src/lib.rs".to_string()]),
        Some("do more".to_string()),
    );
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        total_iterations: 5,
        continuation: continuation.clone(),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(0, false),
    );

    assert_eq!(new_state.phase, PipelinePhase::Development);
    assert_eq!(new_state.continuation.invalid_output_attempts, 1);
    assert_eq!(
        new_state.continuation.previous_status,
        continuation.previous_status
    );
}

#[test]
fn test_development_iteration_invalid_output_retries_then_falls_back() {
    let agent_chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        crate::agents::AgentRole::Developer,
    );

    let mut state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        total_iterations: 5,
        agent_chain,
        ..create_test_state()
    };

    for attempt in 1..=MAX_DEV_INVALID_OUTPUT_RERUNS {
        state = reduce(
            state,
            PipelineEvent::development_iteration_completed(0, false),
        );
        assert_eq!(state.phase, PipelinePhase::Development);
        assert_eq!(state.continuation.invalid_output_attempts, attempt);
    }

    state = reduce(
        state,
        PipelineEvent::development_iteration_completed(0, false),
    );

    assert_eq!(state.phase, PipelinePhase::Development);
    assert_eq!(state.agent_chain.current_agent_index, 1);
    assert_eq!(state.continuation.invalid_output_attempts, 0);
}

#[test]
fn test_development_iteration_completed_stays_in_development_when_more_iterations() {
    // Dev iteration complete -> CommitMessage -> Planning (next iteration)
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

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert_eq!(new_state.iteration, 2);

    // After commit, go to Planning for next iteration
    let new_state = reduce(
        new_state,
        PipelineEvent::commit_created("abc".to_string(), "test".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::Planning);
    assert_eq!(new_state.iteration, 3);
}

#[test]
fn test_development_iteration_completed_transitions_to_review_when_done() {
    // Last dev iteration -> CommitMessage -> Review (all iterations done)
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 3,
        ..create_test_state()
    };
    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(2, true),
    );

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);

    // After commit, go to Review (all dev iterations done)
    let new_state = reduce(
        new_state,
        PipelineEvent::commit_created("abc".to_string(), "test".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.iteration, 3);
}

#[test]
fn test_development_iteration_continuation_succeeded_transitions_to_commit_message() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        continuation: ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "partial work".to_string(),
            None,
            Some("finish it".to_string()),
        ),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_succeeded(2, 1),
    );

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::Development));
    assert!(matches!(new_state.commit, CommitState::NotStarted));
    assert_eq!(
        new_state.continuation,
        ContinuationState {
            context_cleanup_pending: true,
            ..ContinuationState::new()
        }
    );
}

#[test]
fn test_development_iteration_completed_with_large_iteration_number() {
    // Large iteration numbers - but not at the edge to avoid overflow
    // Use MAX-2 iteration with MAX total so we can still have one more
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: u32::MAX - 2,
        total_iterations: u32::MAX,
        ..create_test_state()
    };
    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(u32::MAX - 2, true),
    );

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);

    // After commit, go to Planning with next iteration (MAX-1)
    let new_state = reduce(
        new_state,
        PipelineEvent::commit_created("abc".to_string(), "test".to_string()),
    );

    assert_eq!(new_state.iteration, u32::MAX - 1);
    assert_eq!(new_state.phase, PipelinePhase::Planning);
}

#[test]
fn test_development_phase_completed_transitions_to_review() {
    let state = create_state_in_phase(PipelinePhase::Development);
    let new_state = reduce(state, PipelineEvent::development_phase_completed());

    assert_eq!(new_state.phase, PipelinePhase::Review);
}

#[test]
fn test_development_iteration_completed_with_zero_total_iterations() {
    // Edge case: iteration 0 with total 0 -> CommitMessage -> Review
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        total_iterations: 0,
        ..create_test_state()
    };
    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(0, true),
    );

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);

    // After commit, go to Review
    let new_state = reduce(
        new_state,
        PipelineEvent::commit_created("abc".to_string(), "test".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.iteration, 1);
}

#[test]
fn test_development_iteration_started_with_max_u32() {
    let state = create_test_state();
    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_started(u32::MAX),
    );

    assert_eq!(new_state.iteration, u32::MAX);
}

#[test]
fn test_development_iteration_with_commit_cycle() {
    // Test the full cycle: Planning -> Dev -> CommitMessage -> Planning (next iter)
    let mut state = PipelineState::initial(3, 0); // 3 dev iterations, 0 reviews

    // Start at Planning phase, iteration 0
    assert_eq!(state.phase, PipelinePhase::Planning);
    assert_eq!(state.iteration, 0);

    // After plan generated, transition to Development
    state = reduce(state, PipelineEvent::plan_generation_completed(0, true));
    assert_eq!(state.phase, PipelinePhase::Development);

    // After dev iteration completes, go to CommitMessage
    state = reduce(
        state,
        PipelineEvent::development_iteration_completed(0, true),
    );
    assert_eq!(state.phase, PipelinePhase::CommitMessage);
    assert_eq!(state.iteration, 0); // Don't increment yet!

    // After commit created, go back to Planning for next iteration
    state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "test".to_string()),
    );
    assert_eq!(state.phase, PipelinePhase::Planning);
    assert_eq!(state.iteration, 1); // NOW increment!

    // Repeat for iteration 1
    state = reduce(state, PipelineEvent::plan_generation_completed(1, true));
    assert_eq!(state.phase, PipelinePhase::Development);

    state = reduce(
        state,
        PipelineEvent::development_iteration_completed(1, true),
    );
    assert_eq!(state.phase, PipelinePhase::CommitMessage);

    state = reduce(
        state,
        PipelineEvent::commit_created("def456".to_string(), "test2".to_string()),
    );
    assert_eq!(state.phase, PipelinePhase::Planning);
    assert_eq!(state.iteration, 2);
}

// =========================================================================
// ContinuationContextWritten and ContinuationContextCleaned event tests
// =========================================================================

#[test]
fn test_continuation_context_written_preserves_state() {
    let mut state = create_test_state();
    state.iteration = 2;
    state.continuation = ContinuationState::new().trigger_continuation(
        DevelopmentStatus::Partial,
        "partial work".to_string(),
        Some(vec!["file.rs".to_string()]),
        Some("next steps".to_string()),
    );

    let new_state = reduce(
        state,
        PipelineEvent::development_continuation_context_written(2, 1),
    );

    // Iteration should be set from event
    assert_eq!(new_state.iteration, 2);
    // Continuation state should be preserved (already set by ContinuationTriggered)
    assert!(new_state.continuation.is_continuation());
    assert_eq!(new_state.continuation.continuation_attempt, 1);
}

#[test]
fn test_continuation_context_written_sets_iteration_from_event() {
    let mut state = create_test_state();
    state.iteration = 99; // Different from event

    let new_state = reduce(
        state,
        PipelineEvent::development_continuation_context_written(5, 2),
    );

    // Should use iteration from event
    assert_eq!(new_state.iteration, 5);
}

#[test]
fn test_continuation_context_cleaned_preserves_state() {
    let mut state = create_test_state();
    state.phase = PipelinePhase::Development;
    state.iteration = 3;

    let new_state = reduce(
        state.clone(),
        PipelineEvent::development_continuation_context_cleaned(),
    );

    // State should be unchanged
    assert_eq!(new_state.phase, state.phase);
    assert_eq!(new_state.iteration, state.iteration);
}

#[test]
fn test_continuation_context_event_sequence() {
    // Test the full sequence: ContinuationTriggered -> WriteContinuationContext -> ContinuationContextWritten
    let state = create_test_state();

    // 1. ContinuationTriggered sets continuation state
    let state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            0,
            DevelopmentStatus::Partial,
            "Did some work".to_string(),
            Some(vec!["src/lib.rs".to_string()]),
            Some("Continue with more".to_string()),
        ),
    );
    assert!(state.continuation.is_continuation());
    assert_eq!(state.continuation.continuation_attempt, 1);

    // 2. ContinuationContextWritten confirms the write (state already set)
    let state = reduce(
        state,
        PipelineEvent::development_continuation_context_written(0, 1),
    );
    // State should still be valid
    assert!(state.continuation.is_continuation());
    assert_eq!(state.continuation.continuation_attempt, 1);
}

#[test]
fn test_continuation_context_cleanup_sequence() {
    // Test cleanup on success: ContinuationSucceeded -> cleanup
    let mut state = create_test_state();
    state.continuation = ContinuationState::new().trigger_continuation(
        DevelopmentStatus::Partial,
        "work".to_string(),
        None,
        None,
    );
    state.phase = PipelinePhase::Development;

    // ContinuationSucceeded resets continuation state and schedules cleanup
    let state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_succeeded(0, 1),
    );
    assert!(!state.continuation.is_continuation());
    assert!(state.continuation.context_cleanup_pending);

    // ContinuationContextCleaned clears cleanup pending
    let state = reduce(
        state,
        PipelineEvent::development_continuation_context_cleaned(),
    );
    assert!(!state.continuation.is_continuation());
    assert!(!state.continuation.context_cleanup_pending);
}
