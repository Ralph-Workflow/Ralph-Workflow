//! Integration tests for summary consistency.
//!
//! Verifies that the final pipeline summary is derived exclusively from
//! reducer state with no drift from runtime counters.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (summary derives from state)
//! - Tests are deterministic and isolated
//! - Tests behavior, not implementation details

use ralph_workflow::app::finalization::build_pipeline_summary;
use ralph_workflow::banner::PipelineSummary;
use ralph_workflow::config::Config;
use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent, ReviewEvent};
use ralph_workflow::reducer::state::{DevelopmentStatus, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;

use crate::test_timeout::with_default_timeout;

fn summary_from_state(state: &PipelineState) -> PipelineSummary {
    build_pipeline_summary("0m 00s".to_string(), &Config::test_default(), state)
}

#[test]
fn test_summary_matches_reducer_state_simple_run() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(2, 1);

        // Simulate 2 dev iterations
        for i in 0..2 {
            state = reduce(state, PipelineEvent::development_iteration_started(i));
            state = reduce(state, PipelineEvent::development_agent_invoked(i));
            state = reduce(
                state,
                PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: i }),
            );
            state = reduce(
                state,
                PipelineEvent::development_iteration_completed(i, true),
            );
            state = reduce(
                state,
                PipelineEvent::commit_created(format!("commit{i}"), format!("Commit message {i}")),
            );
        }

        // Simulate 1 review pass (clean, 0-indexed)
        state = reduce(state, PipelineEvent::review_pass_started(0));
        state = reduce(state, PipelineEvent::review_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::Review(ReviewEvent::PassCompletedClean { pass: 0 }),
        );

        // Verify summary through production summary builder.
        let summary = summary_from_state(&state);
        assert_eq!(summary.dev_runs_completed, 2);
        assert_eq!(summary.dev_runs_total, 2);
        assert_eq!(summary.review_passes_completed, 1);
        assert_eq!(summary.review_passes_total, 1);
        assert_eq!(summary.review_runs, 1);
        assert_eq!(summary.changes_detected, 2);
    });
}

#[test]
fn test_summary_matches_reducer_state_with_continuations() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1, 1);

        // Iteration 0 with 2 continuations
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        state = reduce(state, PipelineEvent::development_agent_invoked(0)); // Attempt 1
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "partial".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );

        state = reduce(state, PipelineEvent::development_agent_invoked(0)); // Attempt 2
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationSucceeded {
                iteration: 0,
                total_continuation_attempts: 2,
            }),
        );

        // Commit after dev
        state = reduce(
            state,
            PipelineEvent::commit_created("hash0".to_string(), "Dev work".to_string()),
        );

        // Review with fix (pass 0)
        state = reduce(state, PipelineEvent::review_pass_started(0));
        state = reduce(state, PipelineEvent::review_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::Review(ReviewEvent::FixAgentInvoked { pass: 0 }),
        );
        state = reduce(
            state,
            PipelineEvent::Review(ReviewEvent::FixAttemptCompleted {
                pass: 0,
                changes_made: true,
            }),
        );

        // Commit after fix
        state = reduce(
            state,
            PipelineEvent::commit_created("hash1".to_string(), "Fix issues".to_string()),
        );

        // Verify summary through production summary builder.
        let summary = summary_from_state(&state);
        assert_eq!(summary.dev_runs_completed, 1);
        assert_eq!(summary.dev_runs_total, 1);
        assert_eq!(summary.review_passes_completed, 1);
        assert_eq!(summary.review_passes_total, 1);
        assert_eq!(summary.review_runs, 1);
        assert_eq!(summary.changes_detected, 2); // Dev commit + fix commit
    });
}

#[test]
fn test_summary_matches_with_multiple_review_passes() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1, 3);

        // Dev iteration 0
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::development_iteration_completed(0, true),
        );
        state = reduce(
            state,
            PipelineEvent::commit_created("hash0".to_string(), "Dev work".to_string()),
        );

        // Review passes (clean, 0-indexed)
        for pass in 0..3 {
            state = reduce(state, PipelineEvent::review_pass_started(pass));
            state = reduce(state, PipelineEvent::review_agent_invoked(pass));
            state = reduce(
                state,
                PipelineEvent::Review(ReviewEvent::Completed {
                    pass,
                    issues_found: false,
                }),
            );
        }

        // Verify summary through production summary builder.
        let summary = summary_from_state(&state);
        assert_eq!(summary.review_passes_completed, 3);
        assert_eq!(summary.review_passes_total, 3);
        assert_eq!(summary.review_runs, 3);
    });
}

#[test]
fn test_summary_consistency_with_xsd_retries() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1, 0);

        // Dev iteration with XSD retry
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));

        // Simulate XSD validation failure (triggers retry)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutputValidationFailed {
                iteration: 0,
                attempt: 0,
            }),
        );

        // Retry succeeds
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );
        state = reduce(
            state,
            PipelineEvent::development_iteration_completed(0, true),
        );
        state = reduce(
            state,
            PipelineEvent::commit_created("hash0".to_string(), "Dev work".to_string()),
        );

        // Summary should still show 1 iteration completed and reflect the retry in output statistics.
        let summary = summary_from_state(&state);
        assert_eq!(summary.dev_runs_completed, 1);
        assert_eq!(summary.changes_detected, 1);
    });
}

#[test]
fn test_summary_zero_when_no_work_done() {
    with_default_timeout(|| {
        let state = PipelineState::initial(5, 3);

        // No events, no work done - summary should project zero progress.
        let summary = summary_from_state(&state);
        assert_eq!(summary.dev_runs_completed, 0);
        assert_eq!(summary.dev_runs_total, 5);
        assert_eq!(summary.review_passes_completed, 0);
        assert_eq!(summary.review_passes_total, 3);
        assert_eq!(summary.review_runs, 0);
        assert_eq!(summary.changes_detected, 0);
    });
}

#[test]
fn test_fix_continuation_metrics_tracked_in_reducer() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::FixStatus;

        let mut state = PipelineState::initial(1, 1);

        // Complete dev iteration
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::development_iteration_completed(0, true),
        );
        state = reduce(
            state,
            PipelineEvent::commit_created("hash0".to_string(), "Dev work".to_string()),
        );

        // Start review pass (0-indexed)
        state = reduce(state, PipelineEvent::review_pass_started(0));

        state = reduce(state, PipelineEvent::review_agent_invoked(0));

        // Issues found, start fix
        state = reduce(
            state,
            PipelineEvent::Review(ReviewEvent::FixAgentInvoked { pass: 0 }),
        );

        // Fix reports issues remain - trigger continuation
        state = reduce(
            state,
            PipelineEvent::Review(ReviewEvent::FixContinuationTriggered {
                pass: 0,
                status: FixStatus::IssuesRemain,
                summary: Some("Fixed some issues".to_string()),
            }),
        );

        // Second fix attempt
        state = reduce(
            state,
            PipelineEvent::Review(ReviewEvent::FixAgentInvoked { pass: 0 }),
        );

        // Fix succeeds
        state = reduce(
            state,
            PipelineEvent::Review(ReviewEvent::FixContinuationSucceeded {
                pass: 0,
                total_attempts: 2,
            }),
        );

        let summary = summary_from_state(&state);
        assert_eq!(summary.review_passes_completed, 1);
        assert_eq!(summary.review_runs, 1);
        assert_eq!(summary.changes_detected, 1);
    });
}
