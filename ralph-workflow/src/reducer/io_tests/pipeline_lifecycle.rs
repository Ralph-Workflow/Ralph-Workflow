//! Tests for pipeline lifecycle events (start, resume, complete).

use super::*;

#[test]
fn test_pipeline_started_preserves_all_state() {
    let state = create_test_state();
    let original_phase = state.phase;
    let original_iteration = state.iteration;

    let new_state = reduce(state, PipelineEvent::pipeline_started());

    assert_eq!(new_state.phase, original_phase);
    assert_eq!(new_state.iteration, original_iteration);
}

#[test]
fn test_pipeline_resumed_from_checkpoint_preserves_all_state() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        iteration: 3,
        reviewer_pass: 1,
        review_issues_found: true,
        ..create_test_state()
    };
    let new_state = reduce(state.clone(), PipelineEvent::pipeline_resumed(true));

    // All state should be preserved
    assert_eq!(new_state.phase, state.phase);
    assert_eq!(new_state.iteration, state.iteration);
    assert_eq!(new_state.reviewer_pass, state.reviewer_pass);
    assert_eq!(new_state.review_issues_found, state.review_issues_found);
}

#[test]
fn test_pipeline_resumed_not_from_checkpoint_preserves_all_state() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        reviewer_pass: 0,
        ..create_test_state()
    };
    let new_state = reduce(state.clone(), PipelineEvent::pipeline_resumed(false));

    // All state should be preserved (from_checkpoint parameter doesn't affect reducer)
    assert_eq!(new_state.phase, state.phase);
    assert_eq!(new_state.iteration, state.iteration);
    assert_eq!(new_state.reviewer_pass, state.reviewer_pass);
}

#[test]
fn test_pipeline_completed_transitions_to_complete_phase() {
    let state = create_state_in_phase(PipelinePhase::FinalValidation);
    let new_state = reduce(state, PipelineEvent::pipeline_completed());

    assert_eq!(new_state.phase, PipelinePhase::Complete);
}
