// Basic pipeline transition tests.
//
// Tests for fundamental state transitions: development iteration completed,
// plan generation, phase transitions.

use super::*;

#[test]
fn test_reduce_development_iteration_completed() {
    // Phase 2: DevelopmentIterationCompleted transitions to Review phase
    // After development, the correct path is Review (not CommitMessage directly).
    // The iteration counter stays the same; it gets incremented after Review.
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
    // Iteration stays at 2 (incremented after Review completes)
    assert_eq!(new_state.iteration, 2);
    // Goes to Review phase for code review (Phase 2 change)
    assert_eq!(new_state.phase, PipelinePhase::Review);
    // Previous phase stored for return after review
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::Development));
}

#[test]
fn test_reduce_development_iteration_complete_goes_to_review() {
    // Phase 2: After development iterations complete, route to Review.
    // The transition to CommitMessage happens after Review (and possible Fix).
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
    // Iteration stays at 5 (incremented after Review completes)
    assert_eq!(new_state.iteration, 5);
    // Goes to Review phase first (Phase 2 change: was CommitMessage)
    assert_eq!(new_state.phase, PipelinePhase::Review);
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

// ---------------------------------------------------------------------------
// AnalysisDecision routing tests (Step 4 — Phase 2 TDD)
//
// The analysis artifact carries an explicit `decision` field that overrides
// status-derived routing. These tests verify that typed AnalysisDecision values
// stored in DevelopmentValidatedOutcome.analysis_decision control the next phase.
// ---------------------------------------------------------------------------

#[test]
fn test_iteration_completed_with_needs_replanning_decision_routes_to_planning() {
    // When the analysis artifact carries decision="needs_replanning", the pipeline
    // must route back to the Planning phase, NOT continue in Development.
    use crate::reducer::state::AnalysisDecision;
    use crate::reducer::state::{DevelopmentStatus, DevelopmentValidatedOutcome};

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        development_validated_outcome: Some(DevelopmentValidatedOutcome {
            iteration: 2,
            status: DevelopmentStatus::Completed,
            analysis_decision: Some(AnalysisDecision::NeedsReplanning),
            summary: "done but plan is wrong".to_string(),
            files_changed: None,
            next_steps: None,
        }),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(2, true),
    );

    assert_eq!(
        new_state.phase,
        PipelinePhase::Planning,
        "NeedsReplanning decision must route to Planning phase, got: {:?}",
        new_state.phase
    );
}

#[test]
fn test_iteration_completed_with_ready_for_review_decision_routes_to_review() {
    // When the analysis artifact carries decision="ready_for_review", the pipeline
    // must route to the Review phase.
    use crate::reducer::state::AnalysisDecision;
    use crate::reducer::state::{DevelopmentStatus, DevelopmentValidatedOutcome};

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        development_validated_outcome: Some(DevelopmentValidatedOutcome {
            iteration: 2,
            status: DevelopmentStatus::Completed,
            analysis_decision: Some(AnalysisDecision::ReadyForReview),
            summary: "implementation complete".to_string(),
            files_changed: None,
            next_steps: None,
        }),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(2, true),
    );

    assert_eq!(
        new_state.phase,
        PipelinePhase::Review,
        "ReadyForReview decision must route to Review phase, got: {:?}",
        new_state.phase
    );
}

#[test]
fn test_iteration_completed_with_ready_to_commit_decision_routes_to_commit_message() {
    // When the analysis artifact carries decision="ready_to_commit", the pipeline
    // must route to CommitMessage phase. This is unusual after development (normally
    // only occurs after fix) but the decision field must be respected.
    use crate::reducer::state::AnalysisDecision;
    use crate::reducer::state::{DevelopmentStatus, DevelopmentValidatedOutcome};

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        development_validated_outcome: Some(DevelopmentValidatedOutcome {
            iteration: 2,
            status: DevelopmentStatus::Completed,
            analysis_decision: Some(AnalysisDecision::ReadyToCommit),
            summary: "ready to commit".to_string(),
            files_changed: None,
            next_steps: None,
        }),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(2, true),
    );

    assert_eq!(
        new_state.phase,
        PipelinePhase::CommitMessage,
        "ReadyToCommit decision must route to CommitMessage phase, got: {:?}",
        new_state.phase
    );
}
