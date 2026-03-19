// Fix continuation tests.
//
// Tests for fix continuation triggered, succeeded, budget exhausted events,
// template variables invalid, and fix output validation failures.

use super::*;

#[test]
fn test_fix_continuation_triggered_sets_pending() {
    use crate::reducer::state::{ContinuationState, FixStatus};

    let state = PipelineState {
        phase: PipelinePhase::Review,
        review_issues_found: true,
        reviewer_pass: 0,
        continuation: ContinuationState {
            invalid_output_attempts: 3, // Set non-zero to verify reset
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::fix_continuation_triggered(
            0,
            FixStatus::IssuesRemain,
            Some("Fixed 2 of 5 issues".to_string()),
        ),
    );

    assert!(
        new_state.continuation.fix_continue_pending,
        "Fix continue pending should be set"
    );
    assert_eq!(
        new_state.continuation.fix_continuation_attempt, 1,
        "Fix continuation attempt should be incremented"
    );
    assert_eq!(
        new_state.continuation.fix_status,
        Some(FixStatus::IssuesRemain),
        "Fix status should be stored"
    );
    assert_eq!(
        new_state.continuation.invalid_output_attempts, 0,
        "Invalid output attempts should be reset for new continuation"
    );
    assert_eq!(
        new_state.agent_chain.current_mode,
        crate::agents::DrainMode::Continuation,
        "Fix continuation should be tracked as a drain-local continuation mode"
    );
}

#[test]
fn test_fix_continuation_succeeded_transitions_to_commit() {
    use crate::reducer::state::{ContinuationState, FixStatus};

    let state = PipelineState {
        phase: PipelinePhase::Review,
        review_issues_found: true,
        reviewer_pass: 0,
        continuation: ContinuationState {
            fix_continue_pending: true,
            fix_continuation_attempt: 2,
            fix_status: Some(FixStatus::IssuesRemain),
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    let new_state = reduce(state, PipelineEvent::fix_continuation_succeeded(0, 2));

    assert_eq!(
        new_state.phase,
        PipelinePhase::CommitMessage,
        "Should transition to CommitMessage phase"
    );
    assert!(
        !new_state.continuation.fix_continue_pending,
        "Fix continue pending should be cleared"
    );

    assert!(
        !new_state.commit_diff_prepared,
        "Entering commit phase should reset commit diff tracking"
    );
    assert!(
        !new_state.commit_diff_empty,
        "Entering commit phase should reset commit diff tracking"
    );
    assert!(
        new_state.commit_diff_content_id_sha256.is_none(),
        "Entering commit phase should reset commit diff tracking"
    );
    assert_eq!(
        new_state.agent_chain.current_mode,
        crate::agents::DrainMode::Normal
    );
}

#[test]
fn test_fix_continuation_budget_exhausted_transitions_to_commit() {
    use crate::reducer::state::{ContinuationState, FixStatus};

    let state = PipelineState {
        phase: PipelinePhase::Review,
        review_issues_found: true,
        reviewer_pass: 0,
        continuation: ContinuationState {
            fix_continue_pending: true,
            fix_continuation_attempt: 3,
            fix_status: Some(FixStatus::IssuesRemain),
            max_fix_continue_count: 3,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::fix_continuation_budget_exhausted(0, 3, FixStatus::IssuesRemain),
    );

    assert_eq!(
        new_state.phase,
        PipelinePhase::CommitMessage,
        "Should transition to CommitMessage even when budget exhausted"
    );

    assert!(
        !new_state.commit_diff_prepared,
        "Entering commit phase should reset commit diff tracking"
    );
    assert!(
        !new_state.commit_diff_empty,
        "Entering commit phase should reset commit diff tracking"
    );
    assert!(
        new_state.commit_diff_content_id_sha256.is_none(),
        "Entering commit phase should reset commit diff tracking"
    );
}

#[test]
fn test_template_variables_invalid_retries_same_agent_until_budget_exhausted() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            )
            .with_session_id(Some("ses_abc123".to_string())),
        continuation: ContinuationState::with_limits(2, 3, 2),
        ..PipelineState::initial(5, 2)
    };

    let after_first_invalid = reduce(
        state,
        PipelineEvent::agent_template_variables_invalid(
            AgentRole::Developer,
            "dev_iteration".to_string(),
            vec!["PLAN".to_string()],
            vec!["{{XSD_ERROR}}".to_string()],
        ),
    );

    assert_eq!(
        after_first_invalid.agent_chain.current_agent_index, 0,
        "First TemplateVariablesInvalid should retry same agent, not immediately fall back"
    );
    assert!(
        after_first_invalid.agent_chain.last_session_id.is_none(),
        "Session ID should be cleared when retrying after a transient invocation failure"
    );
    assert!(after_first_invalid.continuation.same_agent_retry_pending);

    let after_second_invalid = reduce(
        after_first_invalid,
        PipelineEvent::agent_template_variables_invalid(
            AgentRole::Developer,
            "dev_iteration".to_string(),
            vec!["PLAN".to_string()],
            vec!["{{XSD_ERROR}}".to_string()],
        ),
    );

    assert_eq!(
        after_second_invalid.agent_chain.current_agent_index, 1,
        "After exhausting retry budget, TemplateVariablesInvalid should fall back to next agent"
    );
}

#[test]
fn test_fix_output_validation_failed_sets_xsd_retry_pending() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Review,
        review_issues_found: true,
        reviewer_pass: 0,
        continuation: ContinuationState::new(),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::fix_output_validation_failed(0, 0, None),
    );

    assert!(
        new_state.continuation.xsd_retry_pending,
        "XSD retry pending should be set"
    );
    assert_eq!(
        new_state.continuation.xsd_retry_count, 1,
        "XSD retry count should be incremented"
    );
}

#[test]
fn test_fix_output_validation_exhausted_switches_agent() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Review,
        review_issues_found: true,
        reviewer_pass: 0,
        continuation: ContinuationState {
            xsd_retry_count: 9,
            max_xsd_retry_count: 10,
            ..ContinuationState::new()
        },
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Reviewer,
        ),
        ..PipelineState::initial(5, 2)
    };

    let new_state = reduce(
        state,
        PipelineEvent::fix_output_validation_failed(0, 2, None),
    );

    // Should have switched to next agent
    assert_eq!(
        new_state.agent_chain.current_agent_index, 1,
        "Should switch to next agent when XSD retries exhausted"
    );
    assert_eq!(
        new_state.continuation.xsd_retry_count, 0,
        "XSD retry count should be reset"
    );
}
