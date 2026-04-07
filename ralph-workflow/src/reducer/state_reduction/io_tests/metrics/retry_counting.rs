//! Retry and continuation counter tests
//!
//! Tests for retry/continuation/fallback counters:
//! - XSD retry counters (total and phase-specific)
//! - Same-agent retry counters
//! - Continuation budget tracking
//! - Agent fallback counters

use super::*;
#[test]
fn test_phase_specific_xsd_retry_increments_review_metrics() {
    let mut state = PipelineState::initial(0, 3);
    state.phase = crate::reducer::event::PipelinePhase::Review;
    assert_eq!(state.metrics.xsd_retry_review, 0);
    assert_eq!(state.metrics.xsd_retry_attempts_total, 0);

    let event = PipelineEvent::Review(ReviewEvent::OutputValidationFailed {
        pass: 0,
        attempt: 0,
        error_detail: None,
    });
    let state = reduce(state, event);

    assert_eq!(state.metrics.xsd_retry_review, 1);
    assert_eq!(state.metrics.xsd_retry_attempts_total, 1);
}

#[test]
fn test_phase_specific_xsd_retry_increments_fix_metrics() {
    let mut state = PipelineState::initial(0, 3);
    state.phase = crate::reducer::event::PipelinePhase::Review;
    assert_eq!(state.metrics.xsd_retry_fix, 0);
    assert_eq!(state.metrics.xsd_retry_attempts_total, 0);

    let event = PipelineEvent::Review(ReviewEvent::FixOutputValidationFailed {
        pass: 0,
        attempt: 0,
        error_detail: None,
    });
    let state = reduce(state, event);

    assert_eq!(state.metrics.xsd_retry_fix, 1);
    assert_eq!(state.metrics.xsd_retry_attempts_total, 1);
}

#[test]
fn test_xsd_retry_does_not_increment_when_exhausted() {
    let mut state = PipelineState::initial(3, 0);
    state.phase = crate::reducer::event::PipelinePhase::Development;
    state.continuation.xsd_retry_count = 98; // One below max (default is 99)
    state.continuation.max_xsd_retry_count = 99;

    // This retry should NOT increment: (98 + 1 = 99) is treated as exhausted.
    let event = PipelineEvent::Development(DevelopmentEvent::OutputValidationFailed {
        iteration: 0,
        attempt: 0,
    });
    let state = reduce(state, event);

    // (98 + 1) hits exhaustion and switches agents, so metrics do not increment.
    assert_eq!(state.metrics.xsd_retry_development, 0);
    assert_eq!(state.metrics.xsd_retry_attempts_total, 0);

    // Try another validation failure. Because XSD retry count is reset when switching
    // agents, this one is a retry attempt and should increment.
    let event = PipelineEvent::Development(DevelopmentEvent::OutputValidationFailed {
        iteration: 0,
        attempt: 0,
    });
    let state = reduce(state, event);

    assert_eq!(state.metrics.xsd_retry_development, 1);
    assert_eq!(state.metrics.xsd_retry_attempts_total, 1);
}

#[test]
fn test_fix_continuation_triggered_increments_counter() {
    use crate::reducer::state::FixStatus;

    let state = PipelineState::initial(0, 3);
    let event = PipelineEvent::Review(ReviewEvent::FixContinuationTriggered {
        pass: 0,
        status: FixStatus::IssuesRemain,
        summary: Some("more work needed".to_string()),
    });
    let state = reduce(state, event);

    assert_eq!(state.metrics.fix_continuations_total, 1);
}

#[test]
fn test_review_pass_completed_clean_increments_counter() {
    let mut state = PipelineState::initial(0, 3);
    state = reduce(state, PipelineEvent::review_pass_started(1));
    assert_eq!(state.metrics.review_passes_completed, 0);

    // Simulate clean pass completion
    let event = PipelineEvent::Review(ReviewEvent::PassCompletedClean { pass: 1 });
    let state = reduce(state, event);

    assert_eq!(state.metrics.review_passes_completed, 1);
    assert_eq!(state.reviewer_pass, 2); // Advances to next pass
}

#[test]
fn test_multiple_review_passes_increment_completed() {
    let mut state = PipelineState::initial(0, 3);

    // Pass 1
    state = reduce(state, PipelineEvent::review_pass_started(1));
    state = reduce(
        state,
        PipelineEvent::Review(ReviewEvent::PassCompletedClean { pass: 1 }),
    );
    assert_eq!(state.metrics.review_passes_completed, 1);

    // Pass 2
    state = reduce(state, PipelineEvent::review_pass_started(2));
    state = reduce(
        state,
        PipelineEvent::Review(ReviewEvent::PassCompletedClean { pass: 2 }),
    );
    assert_eq!(state.metrics.review_passes_completed, 2);
}

#[test]
fn test_fix_attempt_completed_increments_review_passes_completed() {
    let mut state = PipelineState::initial(0, 3);
    state = reduce(state, PipelineEvent::review_pass_started(1));
    assert_eq!(state.metrics.review_passes_completed, 0);

    // Simulate fix completing the pass
    let event = PipelineEvent::Review(ReviewEvent::FixAttemptCompleted {
        pass: 1,
        changes_made: true,
    });
    let state = reduce(state, event);

    assert_eq!(state.metrics.review_passes_completed, 1);
}

#[test]
fn test_continuation_succeeded_increments_dev_completed() {
    let mut state = PipelineState::initial(3, 0);
    state = reduce(state, PipelineEvent::development_iteration_started(0));
    assert_eq!(state.metrics.dev_iterations_completed, 0);

    // Trigger continuation
    state = reduce(
        state,
        PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
            iteration: 0,
            status: DevelopmentStatus::Partial,
            summary: "partial work".to_string(),
            files_changed: None,
            next_steps: None,
        }),
    );

    // Continuation succeeds
    let event = PipelineEvent::Development(DevelopmentEvent::ContinuationSucceeded {
        iteration: 0,
        total_continuation_attempts: 1,
    });
    let state = reduce(state, event);

    assert_eq!(state.metrics.dev_iterations_completed, 1);
}

#[test]
fn test_same_agent_retry_increments_counter() {
    let state = PipelineState::initial(3, 0);
    let event = PipelineEvent::agent_timed_out(
        AgentRole::Developer,
        "claude".to_string(),
        TimeoutOutputKind::PartialResult,
        Some(".agent/logs/developer_0.log".to_string()),
        None,
    );
    let state = reduce(state, event);

    assert_eq!(state.metrics.same_agent_retry_attempts_total, 1);
}

#[test]
fn test_model_fallback_increments_counter() {
    let state = PipelineState::initial(3, 0);
    let event = PipelineEvent::agent_model_fallback_triggered(
        AgentRole::Developer,
        "claude".to_string(),
        "claude-sonnet".to_string(),
        "gpt-4".to_string(),
    );
    let state = reduce(state, event);

    assert_eq!(state.metrics.model_fallbacks_total, 1);
}

#[test]
fn test_retry_cycle_started_increments_counter() {
    let state = PipelineState::initial(3, 0);
    let event = PipelineEvent::agent_retry_cycle_started(AgentRole::Developer, 1);
    let state = reduce(state, event);

    assert_eq!(state.metrics.retry_cycles_started_total, 1);
}

#[test]
fn test_metrics_survive_checkpoint_serialization() {
    let mut state = PipelineState::initial(5, 3);

    // Simulate some progress
    state.metrics.dev_iterations_started = 2;
    state.metrics.dev_iterations_completed = 1;
    state.metrics.dev_attempts_total = 3;
    state.metrics.analysis_attempts_total = 2;
    state.metrics.review_passes_started = 1;
    state.metrics.review_runs_total = 1;
    state.metrics.xsd_retry_attempts_total = 5;
    state.metrics.xsd_retry_development = 3;
    state.metrics.same_agent_retry_attempts_total = 2;
    state.metrics.commits_created_total = 1;

    // Serialize and deserialize
    let json = serde_json::to_string(&state).expect("serialization failed");
    let restored: PipelineState = serde_json::from_str(&json).expect("deserialization failed");

    // Verify all metrics are preserved
    assert_eq!(restored.metrics.dev_iterations_started, 2);
    assert_eq!(restored.metrics.dev_iterations_completed, 1);
    assert_eq!(restored.metrics.dev_attempts_total, 3);
    assert_eq!(restored.metrics.analysis_attempts_total, 2);
    assert_eq!(restored.metrics.review_passes_started, 1);
    assert_eq!(restored.metrics.review_runs_total, 1);
    assert_eq!(restored.metrics.xsd_retry_attempts_total, 5);
    assert_eq!(restored.metrics.xsd_retry_development, 3);
    assert_eq!(restored.metrics.same_agent_retry_attempts_total, 2);
    assert_eq!(restored.metrics.commits_created_total, 1);
}

#[test]
fn test_metrics_default_for_old_checkpoints() {
    // Simulate an old checkpoint without metrics field
    let json = r#"{
        "phase": "Development",
        "iteration": 1,
        "total_iterations": 5,
        "reviewer_pass": 0,
        "total_reviewer_passes": 2,
        "agent_chain": {
            "agents": [],
            "current_agent_index": 0,
            "models_per_agent": [],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 1,
            "retry_delay_ms": 1000,
            "backoff_multiplier": 2.0,
            "max_backoff_ms": 60000,
            "backoff_pending_ms": null,
            "current_role": "Developer",
            "rate_limit_continuation_prompt": null,
            "last_session_id": null
        },
        "continuation": {
            "invalid_output_attempts": 0,
            "continuation_attempt": 0,
            "max_continue_count": 3,
            "continue_pending": false,
            "context_write_pending": false,
            "context_cleanup_pending": false,
            "xsd_retry_count": 0,
            "xsd_retry_pending": false,
            "xsd_retry_session_reuse_pending": false,
            "max_xsd_retry_count": 99,
            "same_agent_retry_count": 0,
            "same_agent_retry_pending": false,
            "same_agent_retry_reason": null,
            "max_same_agent_retry_count": 3,
            "fix_continuation_attempt": 0,
            "max_fix_continue_count": 3,
            "fix_continue_pending": false,
            "last_xsd_error": null,
            "last_review_xsd_error": null,
            "last_fix_xsd_error": null,
            "dev_continuation_context": null
        },
        "rebase": "NotStarted",
        "commit": "NotStarted",
        "execution_history": [],
        "prompt_inputs": {
            "development": null,
            "review": null,
            "commit": null
        },
        "review_issues_found": false,
        "commit_prompt_prepared": false,
        "commit_diff_prepared": false,
        "commit_diff_empty": false,
        "commit_diff_content_id_sha256": null,
        "commit_agent_invoked": false,
        "commit_required_files_cleaned": false,
        "commit_xml_extracted": false,
        "commit_validated_outcome": null,
        "commit_xml_archived": false,
        "context_cleaned": false,
        "dev_fix_triggered": false,
        "previous_phase": null,
        "planning_prompt_prepared_iteration": null,
        "planning_required_files_cleaned_iteration": null,
        "planning_agent_invoked_iteration": null,
        "planning_xml_extracted_iteration": null,
        "planning_validated_outcome": null,
        "planning_markdown_written_iteration": null,
        "planning_xml_archived_iteration": null,
        "development_context_prepared_iteration": null,
        "development_prompt_prepared_iteration": null,
        "development_required_files_cleaned_iteration": null,
        "development_agent_invoked_iteration": null,
        "analysis_agent_invoked_iteration": null,
        "development_xml_extracted_iteration": null,
        "development_validated_outcome": null,
        "development_xml_archived_iteration": null,
        "review_context_prepared_pass": null,
        "review_prompt_prepared_pass": null,
        "review_required_files_cleaned_pass": null,
        "review_agent_invoked_pass": null,
        "review_issues_xml_extracted_pass": null,
        "review_validated_outcome": null,
        "review_issues_markdown_written_pass": null,
        "review_issue_snippets_extracted_pass": null,
        "review_issues_xml_archived_pass": null,
        "fix_prompt_prepared_pass": null,
        "fix_required_files_cleaned_pass": null,
        "fix_agent_invoked_pass": null,
        "fix_result_xml_extracted_pass": null,
        "fix_validated_outcome": null,
        "fix_result_xml_archived_pass": null,
        "checkpoint_saved_count": 0
    }"#;

    let restored: PipelineState = serde_json::from_str(json).expect(
        "deserialization failed: legacy JSON in test must include required AgentChainState fields",
    );

    // Verify metrics field is present with defaults
    assert_eq!(restored.metrics.dev_iterations_started, 0);
    assert_eq!(restored.metrics.max_dev_iterations, 0); // Not initialized from config in this test
}

#[test]
fn test_new_metrics_backward_compatible() {
    // Simulate checkpoint from before review_passes_completed was added
    let mut state = PipelineState::initial(5, 3);
    state.metrics.review_passes_started = 2;
    state.metrics.review_runs_total = 2;
    // review_passes_completed field didn't exist in old checkpoint

    let json = serde_json::to_string(&state).unwrap();
    let mut json_obj: serde_json::Value = serde_json::from_str(&json).unwrap();

    // Remove the new field to simulate old checkpoint
    if let Some(metrics) = json_obj.get_mut("metrics") {
        if let Some(metrics_obj) = metrics.as_object_mut() {
            metrics_obj.remove("review_passes_completed");
        }
    }

    let restored: PipelineState =
        serde_json::from_value(json_obj).expect("should deserialize with defaults");

    // New field should default to 0
    assert_eq!(restored.metrics.review_passes_completed, 0);
    // Existing fields should be preserved
    assert_eq!(restored.metrics.review_passes_started, 2);
    assert_eq!(restored.metrics.review_runs_total, 2);
}

#[test]
fn test_same_agent_retry_exhausted_does_not_increment() {
    let mut state = PipelineState::initial(3, 0);
    state.continuation.max_same_agent_retry_count = 3;
    state.continuation.same_agent_retry_count = 2; // One below max

    // First retry (count becomes 3, which is >= max) should NOT increment because will_retry = false
    let event = PipelineEvent::agent_timed_out(
        AgentRole::Developer,
        "claude".to_string(),
        TimeoutOutputKind::PartialResult,
        Some(".agent/logs/developer_0.log".to_string()),
        None,
    );
    let state = reduce(state, event);

    // Should be 0 because new_retry_count (3) >= max (3), so we fall back without incrementing
    assert_eq!(state.metrics.same_agent_retry_attempts_total, 0);
    // Verify agent chain switched
    assert!(state.agent_chain.current_agent_index > 0 || state.agent_chain.retry_cycle > 0);
}

// ========================================================================
// Timeout output kind metric tests (AC-8)
// ========================================================================

#[test]
fn test_no_output_timeout_increments_timeout_no_output_agent_switches_total() {
    let state = PipelineState::initial(3, 0);
    let event = PipelineEvent::agent_timed_out(
        AgentRole::Developer,
        "claude".to_string(),
        TimeoutOutputKind::NoResult,
        None,
        None,
    );
    let state = reduce(state, event);

    assert_eq!(
        state.metrics.timeout_no_output_agent_switches_total, 1,
        "NoResult timeout should increment timeout_no_output_agent_switches_total"
    );
}

#[test]
fn test_partial_output_timeout_does_not_increment_timeout_no_output_agent_switches_total() {
    let state = PipelineState::initial(3, 0);
    let event = PipelineEvent::agent_timed_out(
        AgentRole::Developer,
        "claude".to_string(),
        TimeoutOutputKind::PartialResult,
        Some(".agent/logs/developer_0.log".to_string()),
        None,
    );
    let state = reduce(state, event);

    assert_eq!(
        state.metrics.timeout_no_output_agent_switches_total, 0,
        "PartialResult timeout should NOT increment timeout_no_output_agent_switches_total"
    );
    // But same_agent_retry_attempts_total should be incremented
    assert_eq!(
        state.metrics.same_agent_retry_attempts_total, 1,
        "PartialResult timeout should increment same_agent_retry_attempts_total"
    );
}

#[test]
fn test_no_output_timeout_does_not_increment_same_agent_retry_attempts_total() {
    let state = PipelineState::initial(3, 0);
    let event = PipelineEvent::agent_timed_out(
        AgentRole::Developer,
        "claude".to_string(),
        TimeoutOutputKind::NoResult,
        None,
        None,
    );
    let state = reduce(state, event);

    assert_eq!(
        state.metrics.same_agent_retry_attempts_total, 0,
        "NoResult timeout should NOT increment same_agent_retry_attempts_total"
    );
}
