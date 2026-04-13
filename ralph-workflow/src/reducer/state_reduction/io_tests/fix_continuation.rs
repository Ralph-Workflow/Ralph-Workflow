// Fix continuation tests.
//
// Tests for fix continuation triggered, succeeded, budget exhausted events,
// template variables invalid, and fix output validation failures.

use crate::agents::{AgentDrain, AgentRole, DrainMode};
use crate::reducer::create_test_state;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::PipelinePhase;
use crate::reducer::state::AgentChainState;
use crate::reducer::state::ContinuationState;
use crate::reducer::state::FixStatus;
use crate::reducer::state::PipelineState;
use crate::reducer::state::SameAgentRetryReason;
use crate::reducer::state_reduction::reduce;

#[test]
fn test_fix_continuation_triggered_sets_pending() {
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
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            )
            .with_session_id(Some("ses_abc123".to_string())),
        continuation: ContinuationState::with_limits(3, 2),
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
fn test_fix_continuation_from_analysis_reinitializes_fix_chain() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        review_issues_found: true,
        reviewer_pass: 0,
        agent_chain: AgentChainState::initial().with_agents(
            vec![
                "analysis-agent".to_string(),
                "analysis-fallback".to_string(),
            ],
            vec![vec![], vec![]],
            AgentRole::Analysis,
        ),
        continuation: ContinuationState::new(),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::fix_continuation_triggered(
            0,
            FixStatus::IssuesRemain,
            Some("Need another fix pass".to_string()),
        ),
    );

    assert_eq!(
        new_state.runtime_drain(),
        crate::agents::AgentDrain::Fix,
        "Fix continuation must keep Fix as the runtime drain"
    );
    assert_eq!(
        new_state.agent_chain.current_mode,
        crate::agents::DrainMode::Continuation,
        "Fix continuation should stay in continuation mode"
    );
    assert!(
        new_state.agent_chain.agents.is_empty(),
        "Fix continuation after analysis must clear loaded analysis agents so Fix drain is reinitialized"
    );
}

#[test]
fn test_fix_continuation_from_analysis_clears_stale_chain_and_retry_state() {
    let mut agent_chain = AgentChainState::initial()
        .with_agents(
            vec![
                "analysis-agent".to_string(),
                "analysis-fallback".to_string(),
            ],
            vec![vec!["m1".to_string()], vec!["m2".to_string()]],
            AgentRole::Analysis,
        )
        .with_mode(DrainMode::SameAgentRetry)
        .with_session_id(Some("ses_analysis_stale".to_string()));
    agent_chain.current_agent_index = 1;
    agent_chain.retry_cycle = 2;
    agent_chain.last_failure_reason = Some("analysis timeout".to_string());

    let state = PipelineState {
        phase: PipelinePhase::Review,
        review_issues_found: true,
        reviewer_pass: 0,
        agent_chain,
        continuation: ContinuationState {
            same_agent_retry_pending: true,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            same_agent_retry_count: 2,
            invalid_output_attempts: 5,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::fix_continuation_triggered(
            0,
            FixStatus::IssuesRemain,
            Some("Need another fix pass".to_string()),
        ),
    );

    assert_eq!(
        new_state.agent_chain.current_drain,
        AgentDrain::Review,
        "Analysis->fix continuation should stage Review drain so orchestration reinitializes the Fix chain"
    );
    assert_eq!(
        new_state.runtime_drain(),
        AgentDrain::Fix,
        "Runtime drain must still resolve to Fix during continuation"
    );
    assert_eq!(
        new_state.agent_chain.current_role,
        AgentRole::Reviewer,
        "Fix drain should use reviewer role semantics"
    );
    assert_eq!(
        new_state.agent_chain.current_agent_index, 0,
        "Continuation handoff must restart chain at first fix agent"
    );
    assert_eq!(
        new_state.agent_chain.retry_cycle, 0,
        "Continuation handoff must reset retry cycle"
    );
    assert_eq!(
        new_state.agent_chain.current_mode,
        DrainMode::Continuation,
        "Fix continuation should run in continuation mode"
    );
    assert!(
        new_state.agent_chain.last_session_id.is_none(),
        "Analysis session id must not leak into fix continuation"
    );

    assert!(
        !new_state.continuation.same_agent_retry_pending,
        "Same-agent retry pending must be cleared for new fix continuation"
    );
    assert!(
        new_state.continuation.same_agent_retry_reason.is_none(),
        "Same-agent retry reason must be cleared for new fix continuation"
    );
    assert_eq!(
        new_state.continuation.same_agent_retry_count, 0,
        "Same-agent retry count must reset at analysis->fix continuation handoff"
    );
}

#[test]
fn test_fix_continuation_from_analysis_success_path_preserves_metrics_integrity() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        review_issues_found: true,
        reviewer_pass: 0,
        agent_chain: AgentChainState::initial().with_agents(
            vec![
                "analysis-agent".to_string(),
                "analysis-fallback".to_string(),
            ],
            vec![vec![], vec![]],
            AgentRole::Analysis,
        ),
        continuation: ContinuationState {
            fix_continuation_attempt: 1,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    let triggered = reduce(
        state,
        PipelineEvent::fix_continuation_triggered(
            0,
            FixStatus::IssuesRemain,
            Some("Need another fix pass".to_string()),
        ),
    );
    assert_eq!(triggered.metrics.fix_continuations_total, 1);
    assert_eq!(triggered.metrics.fix_continuation_attempt, 1);

    let completed = reduce(triggered, PipelineEvent::fix_continuation_succeeded(0, 2));

    assert_eq!(
        completed.phase,
        PipelinePhase::CommitMessage,
        "Successful fix continuation should advance to commit"
    );
    assert_eq!(
        completed.metrics.fix_continuations_total, 1,
        "Success branch must not double-count fix continuations"
    );
    assert_eq!(
        completed.metrics.fix_continuation_attempt, 1,
        "Success branch should preserve attempt metric for run summary"
    );
    assert_eq!(
        completed.metrics.review_passes_completed, 1,
        "Success branch should increment completed review pass metric"
    );
}

#[test]
fn test_fix_continuation_from_analysis_budget_exhausted_path_keeps_metrics_consistent() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        review_issues_found: true,
        reviewer_pass: 0,
        agent_chain: AgentChainState::initial().with_agents(
            vec![
                "analysis-agent".to_string(),
                "analysis-fallback".to_string(),
            ],
            vec![vec![], vec![]],
            AgentRole::Analysis,
        ),
        continuation: ContinuationState::new(),
        ..create_test_state()
    };

    let triggered = reduce(
        state,
        PipelineEvent::fix_continuation_triggered(
            0,
            FixStatus::IssuesRemain,
            Some("Need another fix pass".to_string()),
        ),
    );
    assert_eq!(triggered.metrics.fix_continuations_total, 1);
    assert_eq!(triggered.metrics.fix_continuation_attempt, 1);

    let exhausted = reduce(
        triggered,
        PipelineEvent::fix_continuation_budget_exhausted(0, 1, FixStatus::IssuesRemain),
    );

    assert_eq!(
        exhausted.phase,
        PipelinePhase::CommitMessage,
        "Exhaustion path should still advance to commit"
    );
    assert_eq!(
        exhausted.metrics.review_passes_completed, 0,
        "Exhaustion path must not increment completed review passes"
    );
    assert_eq!(
        exhausted.metrics.fix_continuations_total, 1,
        "Exhaustion path must preserve continuation counting"
    );
    assert_eq!(
        exhausted.metrics.fix_continuation_attempt, 1,
        "Exhaustion path should preserve attempt metric for run summary"
    );
    assert_eq!(
        exhausted.continuation.fix_continuation_attempt, 0,
        "Commit transition should reset continuation state after exhaustion"
    );
}
