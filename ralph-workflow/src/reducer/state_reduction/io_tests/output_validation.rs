// Output validation failed tests.
//
// Tests for development and review output validation failures, including
// retry behavior and agent switching.

use super::*;

#[test]
fn test_output_validation_failed_sets_incremented_attempt_from_event() {
    // When the attempt count has not yet reached the switching threshold the
    // counter is set to attempt.saturating_add(1) and the agent is NOT switched.
    let state = create_test_state();
    let new_state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 1),
    );
    assert_eq!(new_state.phase, PipelinePhase::Development);
    assert_eq!(new_state.continuation.invalid_output_attempts, 2);
}

#[test]
fn test_output_validation_failed_switches_agent_at_limit() {
    use crate::reducer::state::ContinuationState;

    // With a large number of invalid output attempts, the agent should switch
    let base = create_test_state();
    let state = PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 9,
            ..base.continuation.clone()
        },
        ..base
    };
    let new_state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 0),
    );
    assert_eq!(new_state.continuation.invalid_output_attempts, 0);
    assert!(
        new_state.agent_chain.current_agent_index > 0,
        "Should switch to next agent after max invalid output attempts"
    );
}

#[test]
fn test_output_validation_failed_resets_counter_on_agent_switch() {
    let base = create_test_state();
    let state = PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 9,
            ..base.continuation.clone()
        },
        ..base
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 0),
    );
    assert_eq!(
        new_state.continuation.invalid_output_attempts, 0,
        "Counter should reset when switching agents"
    );
}

#[test]
fn test_output_validation_failed_stays_in_development_phase() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Development,
            ..base
        }
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 0),
    );
    assert_eq!(
        new_state.phase,
        PipelinePhase::Development,
        "Should stay in Development phase for retry"
    );
}

#[test]
fn test_review_output_validation_failed_increments_state_counter() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            ..base
        }
    };

    let new_state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.reviewer_pass, 0);
    assert_eq!(new_state.continuation.invalid_output_attempts, 1);
}

#[test]
fn test_review_output_validation_failed_switches_agent_after_limit() {
    let base = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_pending: true,
            same_agent_retry_reason: Some(SameAgentRetryReason::InternalError),
            invalid_output_attempts: 9,
            ..base.continuation.clone()
        },
        ..base
    };

    let new_state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.reviewer_pass, 0);
    assert_eq!(
        new_state.continuation.invalid_output_attempts, 0,
        "Counter should reset when switching agents"
    );
    assert!(
        new_state.agent_chain.current_agent_index > 0,
        "Should switch to next agent after max invalid output attempts"
    );
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
}

#[test]
fn test_review_pass_completed_clean_exits_review_phase() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            ..base
        }
    };

    let new_state = reduce(state, PipelineEvent::review_pass_completed_clean(0));

    assert_eq!(
        new_state.phase,
        PipelinePhase::Review,
        "Clean pass should not exit review when passes remain"
    );
    assert_eq!(new_state.reviewer_pass, 1);
    assert!(!new_state.review_issues_found);
}

#[test]
fn test_review_pass_completed_clean_on_last_pass_clears_previous_phase() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 1,
            previous_phase: Some(PipelinePhase::Development),
            ..base
        }
    };

    let new_state = reduce(state, PipelineEvent::review_pass_completed_clean(0));

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::Review));
    assert!(matches!(new_state.commit, CommitState::NotStarted));
}

#[test]
fn test_review_pass_started_does_not_reset_invalid_output_attempts_on_retry() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            continuation: ContinuationState {
                invalid_output_attempts: 1,
                ..base.continuation.clone()
            },
            ..base
        }
    };

    let new_state = reduce(state, PipelineEvent::review_pass_started(0));

    assert_eq!(new_state.reviewer_pass, 0);
    assert_eq!(
        new_state.continuation.invalid_output_attempts, 1,
        "Retrying the same pass should not clear invalid output attempt counter"
    );
}

#[test]
fn test_review_pass_started_preserves_agent_chain_on_retry() {
    let base = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        continuation: ContinuationState {
            invalid_output_attempts: 9,
            ..base.continuation.clone()
        },
        ..base
    };

    let state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );
    assert!(
        state.agent_chain.current_agent_index > 0,
        "Precondition: review_output_validation_failed should have switched agents after max attempts"
    );

    let new_state = reduce(state.clone(), PipelineEvent::review_pass_started(0));

    assert_eq!(
        new_state.agent_chain.current_agent_index, state.agent_chain.current_agent_index,
        "Retrying the same pass should preserve the current agent selection"
    );
}

#[test]
fn test_review_pass_started_resets_invalid_output_attempts_for_new_pass() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            continuation: ContinuationState {
                invalid_output_attempts: 2,
                ..base.continuation.clone()
            },
            ..base
        }
    };

    let new_state = reduce(state, PipelineEvent::review_pass_started(1));

    assert_eq!(new_state.reviewer_pass, 1);
    assert_eq!(new_state.continuation.invalid_output_attempts, 0);
}

#[test]
fn test_review_phase_completed_resets_commit_state() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            commit: CommitState::Committed {
                hash: "abc123".to_string(),
            },
            ..base
        }
    };

    let new_state = reduce(state, PipelineEvent::review_phase_completed(true));

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert!(matches!(new_state.commit, CommitState::NotStarted));
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::Review));
}

#[test]
fn test_review_completed_no_issues_on_last_pass_resets_commit_state() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 1,
            commit: CommitState::Committed {
                hash: "abc123".to_string(),
            },
            ..base
        }
    };

    let new_state = reduce(state, PipelineEvent::review_completed(0, false));

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert!(matches!(new_state.commit, CommitState::NotStarted));
}
