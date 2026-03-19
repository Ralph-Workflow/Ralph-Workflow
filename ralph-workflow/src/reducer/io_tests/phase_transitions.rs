//! Tests for phase transitions and state preservation across the pipeline.

use super::*;

#[test]
fn test_complete_pipeline_phase_flow() {
    let mut state = create_test_state();
    // This test validates the minimal end-to-end phase progression.
    // Use a single review pass so the post-review commit advances to FinalValidation.
    state.total_reviewer_passes = 1;

    // Planning -> Development
    state = reduce(state, PipelineEvent::planning_phase_started());
    assert_eq!(state.phase, PipelinePhase::Planning);

    state = reduce(state, PipelineEvent::planning_phase_completed());
    assert_eq!(state.phase, PipelinePhase::Development);

    // Development -> Review
    state = reduce(state, PipelineEvent::development_phase_started());
    assert_eq!(state.phase, PipelinePhase::Development);

    state = reduce(state, PipelineEvent::development_phase_completed());
    assert_eq!(state.phase, PipelinePhase::Review);

    // Review -> CommitMessage
    state = reduce(state, PipelineEvent::review_phase_started());
    assert_eq!(state.phase, PipelinePhase::Review);

    state = reduce(state, PipelineEvent::review_phase_completed(false));
    assert_eq!(state.phase, PipelinePhase::CommitMessage);

    // CommitMessage -> FinalValidation
    state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "test".to_string()),
    );
    assert_eq!(state.phase, PipelinePhase::FinalValidation);

    // FinalValidation -> Complete
    state = reduce(state, PipelineEvent::pipeline_completed());
    assert_eq!(state.phase, PipelinePhase::Complete);
}

#[test]
fn test_phase_transitions_preserve_iteration_count() {
    let mut state = PipelineState {
        iteration: 3,
        total_iterations: 5,
        ..create_test_state()
    };

    let original_iteration = state.iteration;

    // Transition through phases
    state = reduce(state, PipelineEvent::planning_phase_started());
    assert_eq!(state.iteration, original_iteration);

    state = reduce(state, PipelineEvent::planning_phase_completed());
    assert_eq!(state.iteration, original_iteration);

    state = reduce(state, PipelineEvent::development_phase_started());
    assert_eq!(state.iteration, original_iteration);
}

#[test]
fn test_phase_transitions_preserve_reviewer_pass() {
    let mut state = PipelineState {
        reviewer_pass: 2,
        total_reviewer_passes: 3,
        ..create_test_state()
    };

    let original_pass = state.reviewer_pass;

    // Transition to commit phase
    state = reduce(state, PipelineEvent::review_phase_completed(false));
    assert_eq!(state.reviewer_pass, original_pass);

    // CommitCreated is the moment the pipeline advances the reviewer pass.
    state = reduce(
        state,
        PipelineEvent::commit_created("abc".to_string(), "test".to_string()),
    );
    assert_eq!(state.reviewer_pass, original_pass + 1);
}

#[test]
fn test_commit_skipped_transitions_preserve_phase_history() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        iteration: 5,
        reviewer_pass: 2,
        ..create_test_state()
    };

    let new_state = reduce(
        state.clone(),
        PipelineEvent::commit_skipped("No changes".to_string()),
    );

    // Should transition to FinalValidation
    assert_eq!(new_state.phase, PipelinePhase::FinalValidation);
    // Should preserve counters
    assert_eq!(new_state.iteration, state.iteration);
    assert_eq!(new_state.reviewer_pass, state.reviewer_pass);
}

#[test]
fn test_state_preservation_through_agent_events() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 3,
        reviewer_pass: 1,
        review_issues_found: true,
        ..create_test_state()
    };

    // Agent events should not modify phase or counters
    let new_state = reduce(
        state.clone(),
        PipelineEvent::agent_invocation_started(
            crate::agents::AgentRole::Developer,
            "test".to_string(),
            Some("test-model".to_string()),
        ),
    );

    assert_eq!(new_state.phase, state.phase);
    assert_eq!(new_state.iteration, state.iteration);
    assert_eq!(new_state.reviewer_pass, state.reviewer_pass);
    assert_eq!(new_state.review_issues_found, state.review_issues_found);
}
