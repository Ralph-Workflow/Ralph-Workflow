//! Iteration counter invariant tests.
//!
//! CRITICAL: Verify that only the commit phase increments iteration counter.
//! Analysis and continuation must NOT increment iteration.
//!
//! These tests enforce the semantic requirement that `-D N` means exactly N
//! planning cycles, regardless of how many analyses or continuations occur.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::state::{DevelopmentStatus, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;

use crate::test_timeout::with_default_timeout;

/// Test that `AnalysisAgentInvoked` event does NOT increment iteration counter.
///
/// This is CRITICAL: analysis is verification only, not a development iteration.
#[test]
fn test_analysis_agent_does_not_increment_iteration() {
    with_default_timeout(|| {
        // Given: State at iteration 0 in Development phase (driven through events)
        let mut state = PipelineState::initial(3, 0);
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));

        // When: AnalysisAgentInvoked event is processed
        let event =
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 });
        let new_state = reduce(state, event);

        // Then: Iteration counter should NOT change
        assert_eq!(
            new_state.iteration, 0,
            "Analysis agent invocation must NOT increment iteration counter"
        );
    });
}

/// Test that `ContinuationTriggered` event does NOT increment iteration counter.
///
/// Continuation is retrying the same iteration, not advancing to a new one.
#[test]
fn test_continuation_does_not_increment_iteration() {
    with_default_timeout(|| {
        // Given: State at iteration 1 with partial status (driven through events)
        let mut state = PipelineState::initial(3, 0);
        // Drive to iteration 1 via events
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::development_iteration_completed(0, true),
        );
        state = reduce(
            state,
            PipelineEvent::commit_created("hash".to_string(), "msg".to_string()),
        );
        state = reduce(state, PipelineEvent::development_iteration_started(1));

        // When: ContinuationTriggered event is processed
        let event = PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
            iteration: 1,
            status: DevelopmentStatus::Partial,
            summary: "Partial work".to_string(),
            files_changed: None,
            next_steps: None,
        });
        let new_state = reduce(state, event);

        // Then: Iteration counter should NOT change
        assert_eq!(
            new_state.iteration, 1,
            "Continuation must NOT increment iteration counter"
        );
    });
}

/// Test that multiple analysis invocations across iterations do NOT increment counter.
///
/// Verifies that analysis running after every dev iteration doesn't affect iteration count.
/// State is driven through events rather than direct field mutation.
#[test]
fn test_multiple_analyses_do_not_increment_iteration() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(3, 0);

        // Simulate analysis running after iterations 0, 1, 2 via events
        for iter in 0..3 {
            // Drive state to the correct iteration via events
            state = reduce(state, PipelineEvent::development_iteration_started(iter));
            state = reduce(state, PipelineEvent::development_agent_invoked(iter));

            let iteration_before = state.iteration;
            let event = PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked {
                iteration: iter,
            });
            state = reduce(state, event);

            // Verify iteration didn't change
            assert_eq!(
                state.iteration, iteration_before,
                "Analysis at iteration {iter} must not increment counter"
            );

            // Complete iteration and commit to advance to next Planning cycle
            if iter < 2 {
                state = reduce(
                    state,
                    PipelineEvent::development_iteration_completed(iter, true),
                );
                state = reduce(
                    state,
                    PipelineEvent::commit_created("hash".to_string(), "msg".to_string()),
                );
            }
        }

        // Final check: should still be at iteration 2 (last iteration started)
        assert_eq!(state.iteration, 2);
    });
}

/// Test that `ContinuationSucceeded` event does NOT increment iteration counter.
///
/// When continuation finally succeeds, we stay on the same iteration.
#[test]
fn test_continuation_succeeded_does_not_increment_iteration() {
    with_default_timeout(|| {
        // Given: State at iteration 0 in Development phase (driven through events)
        let mut state = PipelineState::initial(2, 0);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // When: ContinuationSucceeded event is processed
        let event = PipelineEvent::Development(DevelopmentEvent::ContinuationSucceeded {
            iteration: 0,
            total_continuation_attempts: 2,
        });
        let new_state = reduce(state, event);

        // Then: Iteration counter should NOT change
        assert_eq!(
            new_state.iteration, 0,
            "ContinuationSucceeded must NOT increment iteration counter"
        );
    });
}

/// Test that `XmlExtracted` event does NOT increment iteration counter.
///
/// XML extraction is just reading files, not advancing iterations.
#[test]
fn test_xml_extracted_does_not_increment_iteration() {
    with_default_timeout(|| {
        // Given: State at iteration 1 in Development phase (driven through events)
        let mut state = PipelineState::initial(2, 0);
        // Drive to iteration 1
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::development_iteration_completed(0, true),
        );
        state = reduce(
            state,
            PipelineEvent::commit_created("hash".to_string(), "msg".to_string()),
        );
        state = reduce(state, PipelineEvent::development_iteration_started(1));

        // When: XmlExtracted event is processed
        let event = PipelineEvent::Development(DevelopmentEvent::XmlExtracted { iteration: 1 });
        let new_state = reduce(state, event);

        // Then: Iteration counter should NOT change
        assert_eq!(
            new_state.iteration, 1,
            "XmlExtracted must NOT increment iteration counter"
        );
    });
}

/// Test that `XmlValidated` event with completed status does NOT increment iteration counter.
///
/// Even when work is complete, iteration counter shouldn't change until commit phase.
#[test]
fn test_xml_validated_completed_does_not_increment_iteration() {
    with_default_timeout(|| {
        // Given: State at iteration 0 in Development phase (driven through events)
        let mut state = PipelineState::initial(2, 0);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // When: XmlValidated event with completed status is processed
        let event = PipelineEvent::Development(DevelopmentEvent::XmlValidated {
            iteration: 0,
            status: DevelopmentStatus::Completed,
            analysis_decision: None,
            summary: "All work done".to_string(),
            files_changed: Some(vec!["src/main.rs".to_string()]),
            next_steps: None,
        });
        let new_state = reduce(state, event);

        // Then: Iteration counter should NOT change
        assert_eq!(
            new_state.iteration, 0,
            "XmlValidated with completed status must NOT increment iteration counter"
        );
    });
}

/// Test that analysis running after every iteration doesn't affect `-D N` semantics.
///
/// This is the key integration test: verify that with `-D 2`, we get exactly
/// 2 development cycles, even though analysis runs after each one.
/// State is driven entirely through events.
#[test]
fn test_analysis_after_every_iteration_preserves_d_flag_semantics() {
    with_default_timeout(|| {
        // Given: Pipeline with 2 iterations (-D 2)
        let total_iterations: u32 = 2;
        let mut state = PipelineState::initial(total_iterations, 0);

        // Simulate full flow for each iteration via events
        for iter in 0..total_iterations {
            // Start iteration via event
            state = reduce(state, PipelineEvent::development_iteration_started(iter));

            // Development agent runs
            state = reduce(state, PipelineEvent::development_agent_invoked(iter));
            assert_eq!(
                state.iteration, iter,
                "AgentInvoked should not change iteration"
            );

            // Analysis agent runs (THIS IS THE KEY TEST POINT)
            let iteration_before_analysis = state.iteration;
            state = reduce(
                state,
                PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked {
                    iteration: iter,
                }),
            );

            // Verify analysis did NOT increment iteration
            assert_eq!(
                state.iteration, iteration_before_analysis,
                "Analysis at iteration {iter} must not increment counter"
            );

            // Complete iteration and commit to advance to next cycle
            if iter < total_iterations - 1 {
                state = reduce(
                    state,
                    PipelineEvent::development_iteration_completed(iter, true),
                );
                state = reduce(
                    state,
                    PipelineEvent::commit_created("hash".to_string(), "msg".to_string()),
                );
            }
        }

        // After processing all iterations, should be at last iteration (1, 0-indexed)
        assert_eq!(
            state.iteration,
            total_iterations - 1,
            "Should be at the last iteration"
        );
    });
}

/// Test that analysis does NOT affect continuation budget.
///
/// Analysis is independent of continuation attempts; they use separate counters.
/// State is driven through events to establish the continuation in progress.
#[test]
fn test_analysis_does_not_affect_continuation_budget() {
    with_default_timeout(|| {
        // Given: State with continuation in progress (driven by events)
        let mut state = PipelineState::initial(2, 0);
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));

        // Trigger a continuation to set continuation_attempt = 1
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
        assert_eq!(state.continuation.continuation_attempt, 1);

        // When: AnalysisAgentInvoked event is processed
        let event =
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 });
        let new_state = reduce(state, event);

        // Then: Continuation attempt count should NOT change
        assert_eq!(
            new_state.continuation.continuation_attempt, 1,
            "Analysis must NOT affect continuation budget"
        );
    });
}

// ============================================================================
// Step 14: Edge-case nesting rules (dev iterations vs continuations)
// ============================================================================

/// Test that continuations do NOT increment `dev_iterations_started` counter.
///
/// CRITICAL: Continuations are attempts within the same iteration, not new iterations.
#[test]
fn test_continuation_does_not_increment_iterations_started() {
    with_default_timeout(|| {
        // Given: State at iteration 0 with metrics tracking
        let mut state = PipelineState::initial(3, 0);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // Verify initial state
        assert_eq!(state.metrics.dev_iterations_started, 1);
        assert_eq!(state.metrics.dev_continuation_attempt, 0);

        // When: Trigger first continuation
        let event = PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
            iteration: 0,
            status: DevelopmentStatus::Partial,
            summary: "partial work".to_string(),
            files_changed: None,
            next_steps: None,
        });
        let state = reduce(state, event);

        // When: Trigger second continuation
        let event = PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
            iteration: 0,
            status: DevelopmentStatus::Partial,
            summary: "more partial work".to_string(),
            files_changed: None,
            next_steps: None,
        });
        let state = reduce(state, event);

        // Then: dev_iterations_started should still be 1 (not 3)
        assert_eq!(
            state.metrics.dev_iterations_started, 1,
            "Continuations must NOT increment dev_iterations_started"
        );

        // And: dev_continuation_attempt should track continuation count
        assert_eq!(
            state.metrics.dev_continuation_attempt, 2,
            "Continuations should increment dev_continuation_attempt"
        );
    });
}

/// Test that dev iteration completed semantics work correctly.
///
/// A dev iteration is "completed" when the reducer advances to commit phase,
/// regardless of whether an actual git commit is created.
#[test]
fn test_dev_iteration_completed_semantics() {
    with_default_timeout(|| {
        // Given: State at iteration 0
        let mut state = PipelineState::initial(1, 0);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        assert_eq!(state.metrics.dev_iterations_completed, 0);

        // When: Iteration completes with valid output (advances to commit phase)
        let event = PipelineEvent::development_iteration_completed(0, true);
        let state = reduce(state, event);

        // Then: dev_iterations_completed should increment
        assert_eq!(
            state.metrics.dev_iterations_completed, 1,
            "Dev iteration completion should increment completed counter"
        );

        // And: Phase should advance to Review (Phase 2 default routing: Dev → Review → CommitMessage)
        assert_eq!(
            state.phase,
            PipelinePhase::Review,
            "Completed dev iteration should advance to Review phase (Phase 2)"
        );
    });
}

/// Test that analysis attempts reset per iteration and count correctly within each iteration.
#[test]
fn test_analysis_attempts_reset_per_iteration() {
    with_default_timeout(|| {
        // Given: State starting iteration 0
        let mut state = PipelineState::initial(2, 0);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // When: Run 2 analysis attempts in iteration 0
        let event =
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 });
        let state = reduce(state, event);

        let event =
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 });
        let state = reduce(state, event);

        // Then: Should have 2 total and 2 in current iteration
        assert_eq!(state.metrics.analysis_attempts_total, 2);
        assert_eq!(state.metrics.analysis_attempts_in_current_iteration, 2);

        // When: Start iteration 1
        let state = reduce(state, PipelineEvent::development_iteration_started(1));

        // Then: Per-iteration counter should reset, total should not
        assert_eq!(
            state.metrics.analysis_attempts_total, 2,
            "Total analysis attempts should NOT reset"
        );
        assert_eq!(
            state.metrics.analysis_attempts_in_current_iteration, 0,
            "Per-iteration analysis attempts should reset at iteration start"
        );

        // When: Run 1 analysis in iteration 1
        let event =
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 1 });
        let state = reduce(state, event);

        // Then: Total should be 3, current iteration should be 1
        assert_eq!(state.metrics.analysis_attempts_total, 3);
        assert_eq!(state.metrics.analysis_attempts_in_current_iteration, 1);
    });
}

/// Test that pipeline completes exactly at `total_iterations`, not before.
#[test]
fn test_exactly_completes_at_total_iterations() {
    with_default_timeout(|| {
        // Given: Configure 3 iterations
        let mut state = PipelineState::initial(3, 0);

        // Run iterations 0, 1, 2 with a uniform event sequence.
        // Phase 2 routing: Development → Review → CommitMessage (per iteration).
        for i in 0..3 {
            state = reduce(state, PipelineEvent::development_iteration_started(i));
            assert_eq!(state.iteration, i);

            state = reduce(
                state,
                PipelineEvent::development_iteration_completed(i, true),
            );
            // Phase 2: Dev completion routes to Review first (with 0 reviewer passes, Review
            // immediately routes to CommitMessage after review_phase_completed).
            assert_eq!(state.phase, PipelinePhase::Review);

            // With total_reviewer_passes=0, review phase completes immediately.
            state = reduce(state, PipelineEvent::review_phase_completed(false));
            assert_eq!(state.phase, PipelinePhase::CommitMessage);

            state = reduce(
                state,
                PipelineEvent::commit_created("hash".to_string(), "msg".to_string()),
            );
        }

        // Final observable outcome: after processing all configured iterations,
        // pipeline should have advanced beyond Planning into final validation.
        assert_eq!(
            state.phase,
            PipelinePhase::FinalValidation,
            "After completing all iterations (and with 0 review passes), should advance to final validation"
        );
        assert_eq!(
            state.metrics.dev_iterations_completed, 3,
            "Should have completed all 3 iterations"
        );
    });
}
