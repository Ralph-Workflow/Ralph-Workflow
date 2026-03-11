// Output validation failed tests.
//
// Tests for development and review output validation failures, including
// retry behavior, agent switching, and XSD retry limits.

use super::*;

#[test]
fn test_output_validation_failed_retries_within_limit() {
    let state = create_test_state();
    let new_state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 0),
    );
    assert_eq!(new_state.phase, PipelinePhase::Development);
    assert_eq!(new_state.continuation.invalid_output_attempts, 1);
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(
        new_state.agent_chain.current_mode,
        crate::agents::DrainMode::XsdRetry
    );
}

#[test]
fn test_output_validation_failed_increments_attempt_counter() {
    let mut state = create_test_state();
    state.continuation.invalid_output_attempts = 1;

    let new_state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(0, 1),
    );
    assert_eq!(new_state.phase, PipelinePhase::Development);
    assert_eq!(new_state.continuation.invalid_output_attempts, 2);
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
}

#[test]
fn test_output_validation_failed_switches_agent_at_limit() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        continuation: ContinuationState {
            xsd_retry_count: 1,
            max_xsd_retry_count: 2,
            ..ContinuationState::new()
        },
        ..create_test_state()
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
    use crate::reducer::state::ContinuationState;

    let mut state = PipelineState {
        continuation: ContinuationState {
            xsd_retry_count: 1,
            max_xsd_retry_count: 2,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };
    state.continuation.invalid_output_attempts = 2;

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
    let mut state = create_test_state();
    state.phase = PipelinePhase::Development;

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
fn test_output_validation_failed_respects_configured_xsd_retry_limit() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        continuation: ContinuationState {
            xsd_retry_count: 1,
            max_xsd_retry_count: 5,
            ..ContinuationState::new()
        },
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(1, 0),
    );

    assert_eq!(
        new_state.agent_chain.current_agent_index, 0,
        "Configured XSD retry limit should allow retries before agent fallback"
    );
    assert!(
        new_state.continuation.xsd_retry_pending,
        "Should request XSD retry while under configured limit"
    );
}

#[test]
fn test_review_output_validation_failed_increments_state_counter() {
    let mut state = create_test_state();
    state.phase = PipelinePhase::Review;
    state.reviewer_pass = 0;
    state.total_reviewer_passes = 2;

    let new_state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.reviewer_pass, 0);
    assert_eq!(new_state.continuation.invalid_output_attempts, 1);
    assert_eq!(
        new_state.agent_chain.current_mode,
        crate::agents::DrainMode::XsdRetry
    );
}

#[test]
fn test_review_output_validation_failed_switches_agent_after_limit() {
    use crate::reducer::state::ContinuationState;

    let mut state = PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_pending: true,
            same_agent_retry_reason: Some(
                crate::reducer::state::SameAgentRetryReason::InternalError,
            ),
            xsd_retry_count: 1,
            max_xsd_retry_count: 2,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };
    state.phase = PipelinePhase::Review;
    state.reviewer_pass = 0;
    state.total_reviewer_passes = 2;
    state.continuation.invalid_output_attempts = 2;

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
    let mut state = create_test_state();
    state.phase = PipelinePhase::Review;
    state.reviewer_pass = 0;
    state.total_reviewer_passes = 2;

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
    let mut state = create_test_state();
    state.phase = PipelinePhase::Review;
    state.reviewer_pass = 0;
    state.total_reviewer_passes = 1;
    state.previous_phase = Some(PipelinePhase::Development);

    let new_state = reduce(state, PipelineEvent::review_pass_completed_clean(0));

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::Review));
    assert!(matches!(new_state.commit, CommitState::NotStarted));
}

#[test]
fn test_review_pass_started_does_not_reset_invalid_output_attempts_on_retry() {
    let mut state = create_test_state();
    state.phase = PipelinePhase::Review;
    state.reviewer_pass = 0;
    state.continuation.invalid_output_attempts = 1;

    let new_state = reduce(state, PipelineEvent::review_pass_started(0));

    assert_eq!(new_state.reviewer_pass, 0);
    assert_eq!(
        new_state.continuation.invalid_output_attempts, 1,
        "Retrying the same pass should not clear invalid output attempt counter"
    );
}

#[test]
fn test_review_pass_started_preserves_agent_chain_on_retry() {
    use crate::reducer::state::ContinuationState;

    let mut state = create_test_state();
    state.phase = PipelinePhase::Review;
    state.reviewer_pass = 0;
    state.total_reviewer_passes = 2;
    // Set xsd_retry_count to max-1 so next validation failure triggers agent switch.
    state.continuation = ContinuationState {
        xsd_retry_count: 1,
        max_xsd_retry_count: 2,
        ..ContinuationState::new()
    };

    // Simulate switching agents due to XSD retry limit reached.
    let state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );
    assert!(
        state.agent_chain.current_agent_index > 0,
        "Precondition: review_output_validation_failed should have switched agents when XSD retry limit reached"
    );

    // Orchestration can re-emit PassStarted for the same pass during retries.
    let new_state = reduce(state.clone(), PipelineEvent::review_pass_started(0));

    assert_eq!(
        new_state.agent_chain.current_agent_index, state.agent_chain.current_agent_index,
        "Retrying the same pass should preserve the current agent selection"
    );
}

#[test]
fn test_review_pass_started_resets_invalid_output_attempts_for_new_pass() {
    let mut state = create_test_state();
    state.phase = PipelinePhase::Review;
    state.reviewer_pass = 0;
    state.continuation.invalid_output_attempts = 2;

    let new_state = reduce(state, PipelineEvent::review_pass_started(1));

    assert_eq!(new_state.reviewer_pass, 1);
    assert_eq!(new_state.continuation.invalid_output_attempts, 0);
}

#[test]
fn test_review_phase_completed_resets_commit_state() {
    let mut state = create_test_state();
    state.phase = PipelinePhase::Review;
    state.commit = CommitState::Committed {
        hash: "abc123".to_string(),
    };

    let new_state = reduce(state, PipelineEvent::review_phase_completed(true));

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert!(matches!(new_state.commit, CommitState::NotStarted));
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::Review));
}

#[test]
fn test_review_completed_no_issues_on_last_pass_resets_commit_state() {
    let mut state = create_test_state();
    state.phase = PipelinePhase::Review;
    state.reviewer_pass = 0;
    state.total_reviewer_passes = 1;
    state.commit = CommitState::Committed {
        hash: "abc123".to_string(),
    };

    let new_state = reduce(state, PipelineEvent::review_completed(0, false));

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert!(matches!(new_state.commit, CommitState::NotStarted));
}
