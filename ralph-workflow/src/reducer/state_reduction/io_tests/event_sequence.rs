// Event sequence tests for determinism.
//
// Tests that verify deterministic behavior: same events produce same state,
// validation failures trigger correct agent switches, etc.

use super::*;

#[test]
fn test_event_sequence_output_validation_retry_then_success() {
    use crate::reducer::state::ContinuationState;

    let mut state = PipelineState {
        continuation: ContinuationState {
            max_xsd_retry_count: 3,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };
    state.phase = PipelinePhase::Development;

    // Simulate: validation fail -> validation fail -> success
    state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 0),
    );
    assert_eq!(state.continuation.invalid_output_attempts, 1);
    assert_eq!(state.phase, PipelinePhase::Development);

    state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 1),
    );
    assert_eq!(state.continuation.invalid_output_attempts, 2);

    assert_eq!(
        state.agent_chain.current_agent_index, 0,
        "Should not switch agents yet"
    );

    // Now succeed
    state = reduce(
        state,
        PipelineEvent::development_iteration_completed(0, true),
    );
    assert_eq!(state.phase, PipelinePhase::CommitMessage);
}

#[test]
fn test_event_sequence_validation_failures_trigger_agent_switch() {
    use crate::reducer::state::ContinuationState;

    let mut state = PipelineState {
        continuation: ContinuationState {
            max_xsd_retry_count: 3,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };
    state.phase = PipelinePhase::Development;

    // First validation failure
    state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 0),
    );

    // Second validation failure
    state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 1),
    );

    // Third validation failure - should trigger agent switch
    state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 2),
    );

    // After max failures, should switch agents and reset counter
    assert_eq!(
        state.continuation.invalid_output_attempts, 0,
        "Counter should reset"
    );
    assert!(
        state.agent_chain.current_agent_index > 0 || state.agent_chain.retry_cycle > 0,
        "Should have advanced to next agent or started retry cycle"
    );
}

#[test]
fn test_determinism_same_events_same_state() {
    use crate::reducer::state::DevelopmentStatus;

    // Create two identical initial states
    let state1 = create_test_state();
    let state2 = create_test_state();

    // Apply the same sequence of events
    let events = vec![
        PipelineEvent::development_iteration_started(0),
        PipelineEvent::development_output_validation_failed(0, 0),
        PipelineEvent::development_iteration_continuation_triggered(
            0,
            DevelopmentStatus::Partial,
            "Work".to_string(),
            None,
            None,
        ),
    ];

    let mut final1 = state1;
    let mut final2 = state2;

    for event in events {
        final1 = reduce(final1, event.clone());
        final2 = reduce(final2, event);
    }

    // States should be identical
    assert_eq!(final1.iteration, final2.iteration);
    assert_eq!(final1.phase, final2.phase);
    assert_eq!(
        final1.continuation.continuation_attempt,
        final2.continuation.continuation_attempt
    );
    assert_eq!(
        final1.continuation.invalid_output_attempts,
        final2.continuation.invalid_output_attempts
    );
}
