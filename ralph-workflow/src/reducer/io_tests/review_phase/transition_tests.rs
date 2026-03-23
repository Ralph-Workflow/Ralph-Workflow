// Tests for phase transition scenarios
//
// These tests validate:
// - PassCompletedClean event handling
// - OutputValidationFailed event handling
// - Retry and agent switching logic

#[test]
fn test_review_pass_completed_clean_increments_pass() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 3,
        review_issues_found: false,
        ..create_test_state()
    };
    let new_state = reduce(state, PipelineEvent::review_pass_completed_clean(0));

    assert_eq!(new_state.reviewer_pass, 1);
    assert!(!new_state.review_issues_found);
}

#[test]
fn test_review_pass_completed_clean_on_last_pass_transitions_to_commit() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 2,
        total_reviewer_passes: 3,
        review_issues_found: false,
        ..create_test_state()
    };
    let new_state = reduce(state, PipelineEvent::review_pass_completed_clean(2));

    // 2 + 1 = 3, 3 >= 3, should transition to CommitMessage
    assert_eq!(new_state.reviewer_pass, 2);
    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::Review));
}

#[test]
fn test_review_output_validation_failed_retries_within_limit() {
    let agent_chain = crate::reducer::state::AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        crate::agents::AgentRole::Reviewer,
    );
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        agent_chain,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    // Should stay on same agent when within retry limit
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
}

#[test]
fn test_review_output_validation_failed_switches_agent_at_limit() {
    use crate::reducer::state::ContinuationState;

    // The reducer switches agents when XSD retry count reaches the configured limit.
    let agent_chain = crate::reducer::state::AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        crate::agents::AgentRole::Reviewer,
    );
    let continuation = ContinuationState {
        xsd_retry_count: 1,
        max_xsd_retry_count: 2,
        ..ContinuationState::new()
    };
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        agent_chain,
        continuation,
        ..create_test_state()
    };

    // This validation failure should trigger agent switch since xsd_retry_count is at the limit.
    let new_state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.agent_chain.current_agent_index, 1);
    assert_eq!(
        new_state.continuation.invalid_output_attempts, 0,
        "Invalid output attempts should be reset after switching agents"
    );
}

#[test]
fn test_review_output_validation_failed_stays_in_review_phase() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(1, 0, None),
    );

    assert_eq!(
        new_state.phase,
        PipelinePhase::Review,
        "Should stay in Review phase for retry"
    );
    assert_eq!(new_state.reviewer_pass, 1, "Should preserve reviewer_pass");
}

#[test]
fn test_review_output_validation_event_sequence_retry_then_success() {
    let agent_chain = crate::reducer::state::AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        crate::agents::AgentRole::Reviewer,
    );
    let mut state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        agent_chain,
        ..create_test_state()
    };

    // First validation failure - retry
    state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );
    assert_eq!(state.phase, PipelinePhase::Review);
    assert_eq!(state.agent_chain.current_agent_index, 0);

    // Second validation failure - switch to next agent
    state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 1, None),
    );
    assert_eq!(state.phase, PipelinePhase::Review);
    assert_eq!(state.agent_chain.current_agent_index, 0);

    // Success after retries
    state = reduce(state, PipelineEvent::review_completed(0, false));
    assert_eq!(state.phase, PipelinePhase::Review);
    assert_eq!(state.reviewer_pass, 1);
}
