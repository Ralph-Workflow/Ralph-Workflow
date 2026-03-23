//! Loop detection and recovery tests
//! Tests loop detection threshold and recovery reset

use super::*;

#[test]
fn test_planning_xsd_retry_sets_session_reuse_pending() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        iteration: 0,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_session_id(Some("session-123".to_string())),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::planning_output_validation_failed(0, 0),
    );

    assert!(
        new_state.continuation.xsd_retry_session_reuse_pending,
        "XSD retry should set session reuse pending to preserve session for retry"
    );
    // Session ID should still be present (not cleared)
    assert_eq!(
        new_state.agent_chain.last_session_id,
        Some("session-123".to_string()),
        "Session ID should be preserved for XSD retry reuse"
    );
}

/// Test that XSD retry in review phase preserves session ID for reuse.
#[test]
fn test_review_xsd_retry_sets_session_reuse_pending() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_session_id(Some("session-456".to_string())),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );

    assert!(
        new_state.continuation.xsd_retry_session_reuse_pending,
        "XSD retry should set session reuse pending"
    );
    assert_eq!(
        new_state.agent_chain.last_session_id,
        Some("session-456".to_string()),
        "Session ID should be preserved for XSD retry reuse"
    );
}

/// Test that XSD retry in commit phase preserves session ID for reuse.
#[test]
fn test_commit_xsd_retry_sets_session_reuse_pending() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        agent_chain: AgentChainState::initial()
            .with_agents(vec!["agent1".to_string()], vec![vec![]], AgentRole::Commit)
            .with_session_id(Some("session-789".to_string())),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_message_validation_failed("Invalid XML".to_string(), 1),
    );

    assert!(
        new_state.continuation.xsd_retry_session_reuse_pending,
        "XSD retry should set session reuse pending"
    );
    assert_eq!(
        new_state.agent_chain.last_session_id,
        Some("session-789".to_string()),
        "Session ID should be preserved for XSD retry reuse"
    );
}

// =========================================================================
// Agent fallback (XSD exhausted) clears session tests
// =========================================================================

/// Test that planning XSD exhaustion clears session ID when switching agents.
#[test]
fn test_planning_xsd_exhausted_clears_session_id() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Planning,
        iteration: 0,
        continuation: ContinuationState {
            xsd_retry_count: 9,
            max_xsd_retry_count: 10,
            ..ContinuationState::new()
        },
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            )
            .with_session_id(Some("session-123".to_string())),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::planning_output_validation_failed(0, 0),
    );

    // XSD retries exhausted, should switch agent AND clear session
    assert_eq!(
        new_state.agent_chain.current_agent_index, 1,
        "Should switch to next agent"
    );
    assert!(
        new_state.agent_chain.last_session_id.is_none(),
        "Session ID should be cleared when switching agents after XSD exhaustion"
    );
    assert!(
        !new_state.continuation.xsd_retry_session_reuse_pending,
        "Session reuse pending should be cleared after agent switch"
    );
}

/// Test that review XSD exhaustion clears session ID when switching agents.
#[test]
fn test_review_xsd_exhausted_clears_session_id() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        continuation: ContinuationState {
            xsd_retry_count: 9,
            max_xsd_retry_count: 10,
            ..ContinuationState::new()
        },
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Reviewer,
            )
            .with_session_id(Some("session-456".to_string())),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );

    assert_eq!(
        new_state.agent_chain.current_agent_index, 1,
        "Should switch to next agent"
    );
    assert!(
        new_state.agent_chain.last_session_id.is_none(),
        "Session ID should be cleared when switching agents after XSD exhaustion"
    );
}

/// Test that fix XSD exhaustion clears session ID when switching agents.
#[test]
fn test_fix_xsd_exhausted_clears_session_id() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        review_issues_found: true,
        continuation: ContinuationState {
            xsd_retry_count: 9,
            max_xsd_retry_count: 10,
            ..ContinuationState::new()
        },
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Reviewer,
            )
            .with_session_id(Some("session-789".to_string())),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::fix_output_validation_failed(0, 0, None),
    );

    assert_eq!(
        new_state.agent_chain.current_agent_index, 1,
        "Should switch to next agent"
    );
    assert!(
        new_state.agent_chain.last_session_id.is_none(),
        "Session ID should be cleared when switching agents after XSD exhaustion"
    );
}

/// Test that development XSD exhaustion clears session ID when switching agents.
#[test]
fn test_development_xsd_exhausted_clears_session_id() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        continuation: ContinuationState {
            xsd_retry_count: 9,
            max_xsd_retry_count: 10,
            ..ContinuationState::new()
        },
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Analysis,
            )
            .with_session_id(Some("session-analysis".to_string())),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(1, 0),
    );

    assert_eq!(
        new_state.agent_chain.current_agent_index, 1,
        "Should switch to next agent"
    );
    assert!(
        new_state.agent_chain.last_session_id.is_none(),
        "Session ID should be cleared when switching agents after XSD exhaustion"
    );
}

// =========================================================================
// Agent fallback resets full orchestration state tests
// =========================================================================

/// Test that planning XSD exhaustion resets ALL orchestration flags for full rollback.
#[test]
fn test_planning_xsd_exhausted_resets_all_orchestration_flags() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Planning,
        iteration: 0,
        planning_prompt_prepared_iteration: Some(0),
        planning_required_files_cleaned_iteration: Some(0),
        planning_agent_invoked_iteration: Some(0),
        planning_xml_extracted_iteration: Some(0),
        planning_validated_outcome: Some(PlanningValidatedOutcome {
            iteration: 0,
            valid: false,
            markdown: None,
        }),
        planning_markdown_written_iteration: Some(0),
        planning_xml_archived_iteration: Some(0),
        continuation: ContinuationState {
            xsd_retry_count: 9,
            max_xsd_retry_count: 10,
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
        PipelineEvent::planning_output_validation_failed(0, 0),
    );

    // ALL orchestration flags should be reset for full rollback
    assert!(
        new_state.planning_prompt_prepared_iteration.is_none(),
        "planning_prompt_prepared_iteration should be reset"
    );
    assert!(
        new_state
            .planning_required_files_cleaned_iteration
            .is_none(),
        "planning_required_files_cleaned_iteration should be reset"
    );
    assert!(
        new_state.planning_agent_invoked_iteration.is_none(),
        "planning_agent_invoked_iteration should be reset"
    );
    assert!(
        new_state.planning_xml_extracted_iteration.is_none(),
        "planning_xml_extracted_iteration should be reset"
    );
    assert!(
        new_state.planning_validated_outcome.is_none(),
        "planning_validated_outcome should be reset"
    );
    assert!(
        new_state.planning_markdown_written_iteration.is_none(),
        "planning_markdown_written_iteration should be reset"
    );
    assert!(
        new_state.planning_xml_archived_iteration.is_none(),
        "planning_xml_archived_iteration should be reset"
    );
    // Continuation state should be reset
    assert_eq!(
        new_state.continuation.xsd_retry_count, 0,
        "xsd_retry_count should be reset"
    );
    assert!(
        !new_state.continuation.xsd_retry_pending,
        "xsd_retry_pending should be cleared"
    );
}

/// Test that commit XSD exhaustion resets ALL orchestration flags for full rollback.
#[test]
fn test_commit_xsd_exhausted_resets_all_orchestration_flags() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_prompt_prepared: true,
        commit_agent_invoked: true,
        commit_required_files_cleaned: true,
        commit_xml_extracted: true,
        commit_validated_outcome: Some(CommitValidatedOutcome {
            attempt: 1,
            message: None,
            reason: Some("Invalid".to_string()),
        }),
        continuation: ContinuationState {
            xsd_retry_count: 9,
            max_xsd_retry_count: 10,
            ..ContinuationState::new()
        },
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_message_validation_failed("Invalid XML".to_string(), 1),
    );

    // ALL orchestration flags should be reset for full rollback
    assert!(
        !new_state.commit_prompt_prepared,
        "commit_prompt_prepared should be reset"
    );
    assert!(
        !new_state.commit_agent_invoked,
        "commit_agent_invoked should be reset"
    );
    assert!(
        !new_state.commit_required_files_cleaned,
        "commit_required_files_cleaned should be reset"
    );
    assert!(
        !new_state.commit_xml_extracted,
        "commit_xml_extracted should be reset"
    );
    assert!(
        new_state.commit_validated_outcome.is_none(),
        "commit_validated_outcome should be reset"
    );
    // Continuation state should be reset
    assert_eq!(
        new_state.continuation.xsd_retry_count, 0,
        "xsd_retry_count should be reset"
    );
}
