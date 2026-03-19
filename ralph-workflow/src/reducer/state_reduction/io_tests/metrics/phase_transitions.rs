//! Phase transition metric tests
//!
//! Tests for phase-specific metric updates:
//! - Planning phase metrics
//! - Development phase metrics
//! - Review phase metrics
//! - Commit phase metrics

use super::*;

#[test]
fn test_same_agent_retry_within_budget_does_increment() {
    let mut state = PipelineState::initial(3, 0);
    state.continuation.max_same_agent_retry_count = 3;
    state.continuation.same_agent_retry_count = 0;

    // First retry (count becomes 1, which is < max) should increment
    let event = PipelineEvent::agent_timed_out(
        AgentRole::Developer,
        "claude".to_string(),
        TimeoutOutputKind::PartialOutput,
        Some(".agent/logs/developer_0.log".to_string()),
        None,
    );
    let state = reduce(state, event);

    assert_eq!(state.metrics.same_agent_retry_attempts_total, 1);
    assert_eq!(state.continuation.same_agent_retry_count, 1);
    assert!(state.continuation.same_agent_retry_pending);
}

// ==============================================================================
// New tests for per-iteration/pass continuation attempt tracking
// ==============================================================================

#[test]
fn test_dev_continuation_attempt_increments_on_continuation_triggered() {
    let mut state = PipelineState::initial(1, 0);
    state = reduce(state, PipelineEvent::development_iteration_started(0));

    assert_eq!(state.metrics.dev_continuation_attempt, 0);

    // Trigger continuation
    let event = PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
        iteration: 0,
        status: DevelopmentStatus::Partial,
        summary: "Partial work".to_string(),
        files_changed: None,
        next_steps: None,
    });
    state = reduce(state, event);

    assert_eq!(state.metrics.dev_continuation_attempt, 1);
    assert_eq!(state.metrics.dev_iterations_completed, 0); // Not completed yet
}

#[test]
fn test_dev_continuation_attempt_resets_on_new_iteration() {
    let mut state = PipelineState::initial(2, 0);

    // Iteration 0 with continuation
    state = reduce(state, PipelineEvent::development_iteration_started(0));
    state = reduce(
        state,
        PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
            iteration: 0,
            status: DevelopmentStatus::Partial,
            summary: "Partial".to_string(),
            files_changed: None,
            next_steps: None,
        }),
    );
    assert_eq!(state.metrics.dev_continuation_attempt, 1);

    // New iteration should reset
    state = reduce(state, PipelineEvent::development_iteration_started(1));
    assert_eq!(state.metrics.dev_continuation_attempt, 0);
}

#[test]
fn test_dev_iterations_completed_increments_on_continuation_succeeded() {
    let mut state = PipelineState::initial(1, 0);
    state = reduce(state, PipelineEvent::development_iteration_started(0));

    assert_eq!(state.metrics.dev_iterations_completed, 0);

    // Continuation succeeded
    let event = PipelineEvent::Development(DevelopmentEvent::ContinuationSucceeded {
        iteration: 0,
        total_continuation_attempts: 2,
    });
    state = reduce(state, event);

    assert_eq!(state.metrics.dev_iterations_completed, 1);
    assert_eq!(
        state.phase,
        crate::reducer::event::PipelinePhase::CommitMessage
    );
}

#[test]
fn test_dev_iterations_completed_not_incremented_on_continuation_triggered() {
    let mut state = PipelineState::initial(1, 0);
    state = reduce(state, PipelineEvent::development_iteration_started(0));

    // Trigger continuation - should NOT increment completed counter
    let event = PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
        iteration: 0,
        status: DevelopmentStatus::Partial,
        summary: "Partial".to_string(),
        files_changed: None,
        next_steps: None,
    });
    state = reduce(state, event);

    assert_eq!(state.metrics.dev_iterations_completed, 0);
    assert_eq!(state.metrics.dev_continuation_attempt, 1);
}

#[test]
fn test_review_pass_increments_current_pass_on_pass_started() {
    let state = PipelineState::initial(0, 3);

    let event = PipelineEvent::review_pass_started(1);
    let state = reduce(state, event);

    assert_eq!(state.metrics.current_review_pass, 1);
    assert_eq!(state.metrics.review_passes_started, 1);
}

#[test]
fn test_pass_started_retry_does_not_reset_fix_continuation_attempt_metric() {
    use crate::reducer::state::FixStatus;

    let mut state = PipelineState::initial(0, 1);
    state = reduce(state, PipelineEvent::review_pass_started(1));

    // Drive fix continuation attempt within pass.
    state = reduce(
        state,
        PipelineEvent::Review(ReviewEvent::FixContinuationTriggered {
            pass: 1,
            status: FixStatus::IssuesRemain,
            summary: None,
        }),
    );
    assert_eq!(state.metrics.fix_continuation_attempt, 1);

    // Orchestration may re-emit PassStarted for the same pass on retry.
    state = reduce(state, PipelineEvent::review_pass_started(1));
    assert_eq!(state.metrics.fix_continuation_attempt, 1);
}

#[test]
fn test_fix_continuation_attempt_increments_on_continuation_triggered() {
    use crate::reducer::state::FixStatus;

    let mut state = PipelineState::initial(0, 1);
    state = reduce(state, PipelineEvent::review_pass_started(1));

    assert_eq!(state.metrics.fix_continuation_attempt, 0);

    // Trigger fix continuation
    let event = PipelineEvent::Review(ReviewEvent::FixContinuationTriggered {
        pass: 1,
        status: FixStatus::IssuesRemain,
        summary: Some("Fixed some issues".to_string()),
    });
    state = reduce(state, event);

    assert_eq!(state.metrics.fix_continuation_attempt, 1);
    assert_eq!(state.metrics.fix_continuations_total, 1);
}

#[test]
fn test_fix_continuation_attempt_resets_on_new_pass() {
    use crate::reducer::state::FixStatus;

    let mut state = PipelineState::initial(0, 2);

    // Pass 1 with fix continuation
    state = reduce(state, PipelineEvent::review_pass_started(1));
    state = reduce(
        state,
        PipelineEvent::Review(ReviewEvent::FixContinuationTriggered {
            pass: 1,
            status: FixStatus::IssuesRemain,
            summary: None,
        }),
    );
    assert_eq!(state.metrics.fix_continuation_attempt, 1);

    // New pass should reset
    state = reduce(state, PipelineEvent::review_pass_started(2));
    assert_eq!(state.metrics.fix_continuation_attempt, 0);
}

#[test]
fn test_review_passes_completed_increments_on_clean_pass() {
    let mut state = PipelineState::initial(0, 1);
    state = reduce(state, PipelineEvent::review_pass_started(1));

    assert_eq!(state.metrics.review_passes_completed, 0);

    // Clean pass
    let event = PipelineEvent::Review(ReviewEvent::PassCompletedClean { pass: 1 });
    state = reduce(state, event);

    assert_eq!(state.metrics.review_passes_completed, 1);
}

#[test]
fn test_review_passes_completed_increments_on_fix_attempt_completed() {
    let mut state = PipelineState::initial(0, 1);
    state = reduce(state, PipelineEvent::review_pass_started(1));

    assert_eq!(state.metrics.review_passes_completed, 0);

    // Fix attempt completed
    let event = PipelineEvent::Review(ReviewEvent::FixAttemptCompleted {
        pass: 1,
        changes_made: true,
    });
    state = reduce(state, event);

    assert_eq!(state.metrics.review_passes_completed, 1);
}

#[test]
fn test_review_passes_completed_not_incremented_when_issues_found() {
    let mut state = PipelineState::initial(0, 1);
    state = reduce(state, PipelineEvent::review_pass_started(1));

    assert_eq!(state.metrics.review_passes_completed, 0);

    // Pass completed with issues found - should NOT increment
    let event = PipelineEvent::Review(ReviewEvent::Completed {
        pass: 1,
        issues_found: true,
    });
    state = reduce(state, event);

    assert_eq!(state.metrics.review_passes_completed, 0);
}

#[test]
fn test_review_passes_completed_increments_on_fix_continuation_succeeded() {
    let mut state = PipelineState::initial(0, 1);
    state = reduce(state, PipelineEvent::review_pass_started(1));

    assert_eq!(state.metrics.review_passes_completed, 0);

    // Fix continuation succeeded
    let event = PipelineEvent::Review(ReviewEvent::FixContinuationSucceeded {
        pass: 1,
        total_attempts: 2,
    });
    state = reduce(state, event);

    assert_eq!(state.metrics.review_passes_completed, 1);
    assert_eq!(
        state.phase,
        crate::reducer::event::PipelinePhase::CommitMessage
    );
}

// ==============================================================================
// Checkpoint compatibility tests
// ==============================================================================

#[test]
fn test_new_metrics_fields_checkpoint_compatible() {
    let mut state = PipelineState::initial(2, 2);

    // Set new metrics fields
    state.metrics.dev_continuation_attempt = 2;
    state.metrics.fix_continuation_attempt = 1;
    state.metrics.current_review_pass = 1;

    // Serialize
    let serialized = serde_json::to_string(&state).expect("serialize failed");

    // Deserialize
    let restored: PipelineState = serde_json::from_str(&serialized).expect("deserialize failed");

    // Verify new fields are preserved
    assert_eq!(restored.metrics.dev_continuation_attempt, 2);
    assert_eq!(restored.metrics.fix_continuation_attempt, 1);
    assert_eq!(restored.metrics.current_review_pass, 1);
}

#[test]
fn test_old_checkpoint_loads_with_new_metrics_fields_defaulted() {
    // Simulate old checkpoint JSON without new fields
    let old_checkpoint_json = r#"{
        "phase": "Development",
        "previous_phase": null,
        "iteration": 1,
        "total_iterations": 2,
        "reviewer_pass": 0,
        "total_reviewer_passes": 2,
        "review_issues_found": false,
        "context_cleaned": false,
        "agent_chain": {
            "agents": [],
            "current_agent_index": 0,
            "models_per_agent": [],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 1,
            "retry_delay_ms": 0,
            "backoff_multiplier": 1.0,
            "max_backoff_ms": 0,
            "backoff_pending_ms": null,
            "current_role": "Developer",
            "rate_limit_continuation_prompt": null,
            "last_session_id": null
        },
        "rebase": "NotStarted",
        "commit": "NotStarted",
        "execution_history": [],
        "checkpoint_saved_count": 0,
        "continuation": {
            "previous_status": null,
            "previous_summary": null,
            "previous_files_changed": null,
            "previous_next_steps": null,
            "continuation_attempt": 0,
            "invalid_output_attempts": 0,
            "context_write_pending": false,
            "context_cleanup_pending": false,
            "xsd_retry_count": 0,
            "xsd_retry_pending": false,
            "xsd_retry_session_reuse_pending": false,
            "max_xsd_retry_count": 10,
            "max_same_agent_retry_count": 2,
            "max_continue_count": 3
        },
        "dev_fix_triggered": false,
        "prompt_inputs": {},
        "metrics": {
            "dev_iterations_started": 1,
            "dev_iterations_completed": 0,
            "dev_attempts_total": 3,
            "analysis_attempts_total": 1,
            "analysis_attempts_in_current_iteration": 1,
            "review_passes_started": 0,
            "review_passes_completed": 0,
            "review_runs_total": 0,
            "fix_runs_total": 0,
            "fix_continuations_total": 0,
            "xsd_retry_attempts_total": 2,
            "xsd_retry_planning": 0,
            "xsd_retry_development": 2,
            "xsd_retry_review": 0,
            "xsd_retry_fix": 0,
            "xsd_retry_commit": 0,
            "same_agent_retry_attempts_total": 0,
            "agent_fallbacks_total": 0,
            "model_fallbacks_total": 0,
            "retry_cycles_started_total": 0,
            "commits_created_total": 0,
            "max_dev_iterations": 2,
            "max_review_passes": 2
        }
    }"#;

    let restored: PipelineState = serde_json::from_str(old_checkpoint_json)
        .expect("old checkpoint should deserialize with defaults");

    // New fields should default to 0
    assert_eq!(restored.metrics.dev_continuation_attempt, 0);
    assert_eq!(restored.metrics.fix_continuation_attempt, 0);
    assert_eq!(restored.metrics.current_review_pass, 0);

    // Existing fields should be preserved
    assert_eq!(restored.metrics.dev_iterations_started, 1);
    assert_eq!(restored.metrics.xsd_retry_attempts_total, 2);
}
// ============================================================================
// XSD Retry Metrics Tests (Step 13)
// ============================================================================

// ============================================================================
// TimeoutOutputKind Serde Round-trip Tests (AC-1)
// ============================================================================

#[test]
fn test_timeout_output_kind_no_output_serde_roundtrip() {
    let original = TimeoutOutputKind::NoOutput;
    let json = serde_json::to_string(&original).expect("serialize NoOutput");
    assert_eq!(json, r#""NoOutput""#);
    let restored: TimeoutOutputKind = serde_json::from_str(&json).expect("deserialize NoOutput");
    assert_eq!(restored, original);
}

#[test]
fn test_timeout_output_kind_partial_output_serde_roundtrip() {
    let original = TimeoutOutputKind::PartialOutput;
    let json = serde_json::to_string(&original).expect("serialize PartialOutput");
    assert_eq!(json, r#""PartialOutput""#);
    let restored: TimeoutOutputKind =
        serde_json::from_str(&json).expect("deserialize PartialOutput");
    assert_eq!(restored, original);
}

#[test]
fn test_timeout_output_kind_defaults_to_partial_output_when_missing() {
    // When a TimedOut event is received without output_kind (old checkpoint),
    // it should default to PartialOutput.
    #[derive(serde::Deserialize)]
    struct TimedOutWithoutOutputKind {
        // These fields are in the JSON but not needed for the test assertion.
        // Underscore prefix indicates intentionally unused.
        #[serde(rename = "role")]
        _role: crate::agents::AgentRole,
        #[serde(rename = "agent")]
        _agent: String,
        #[serde(default = "crate::reducer::event::default_timeout_output_kind")]
        output_kind: TimeoutOutputKind,
    }
    let json = r#"{"role":"Developer","agent":"claude"}"#;
    let event: TimedOutWithoutOutputKind =
        serde_json::from_str(json).expect("deserialize without output_kind");
    assert_eq!(event.output_kind, TimeoutOutputKind::PartialOutput);
}
