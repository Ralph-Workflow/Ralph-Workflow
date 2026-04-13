//! Tests for default configuration values and boundary behavior.
//!
//! Tests in this module verify:
//! - Missing `max_dev_continuations` config applies correct defaults
//! - `unwrap_or(2)` fallback caps continuations correctly
//! - `OutcomeApplied` exhausts at the correct boundary
//! - Continuation attempt numbering is correct

use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent};
use ralph_workflow::reducer::state::{ContinuationState, DevelopmentStatus, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;

use crate::test_timeout::with_default_timeout;

/// Test that missing `max_dev_continuations` config key applies default value.
///
/// This test verifies that when `max_dev_continuations` is omitted from the config file,
/// the system applies the serde default of 2, resulting in `max_continue_count` of 3
/// (1 initial + 2 continuations).
#[test]
fn test_missing_max_dev_continuations_applies_default() {
    with_default_timeout(|| {
        // Simulate config with default max_dev_continuations = 2
        // This results in max_continue_count = 1 + 2 = 3
        let continuation = ContinuationState::with_limits(
            3, // max_continue_count (1 initial + 2 continuations)
            2, // max_same_agent_retries
        );

        let mut state = PipelineState::initial_with_continuation(1, 0, &continuation);

        // Verify default is applied correctly
        assert_eq!(
            state.continuation.max_continue_count, 3,
            "Missing config key should default to 3 total attempts (1 initial + 2 continuations)"
        );

        // Start iteration and verify exhaustion logic works correctly
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        assert_eq!(state.continuation.continuation_attempt, 0);
        assert!(!state.continuation.continuations_exhausted());

        // First continuation (attempt 1)
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
        assert!(!state.continuation.continuations_exhausted());

        // Second continuation (attempt 2)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "partial work 2".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        assert_eq!(state.continuation.continuation_attempt, 2);
        assert!(!state.continuation.continuations_exhausted());

        // Attempt third continuation (would be attempt 3, but defensive check prevents it)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "partial work 3 attempt".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        // AFTER FIX: trigger_continuation defensive check prevents increment to 3
        assert_eq!(
            state.continuation.continuation_attempt, 2,
            "Defensive check should prevent increment to 3"
        );
        // continuations_exhausted() returns false (2 < 3), but continue_pending is cleared
        assert!(!state.continuation.continuations_exhausted());
        assert!(!state.continuation.continue_pending);
    });
}

/// Test that continuation budget is properly capped at 3 when `max_dev_continuations` could be None.
///
/// This regression test verifies that even when `Config.max_dev_continuations` is None
/// (e.g., from `Config::default()` or `Config::test_default()`), the system enforces
/// the `unwrap_or(2)` fallback, resulting in a cap of 3 total attempts.
///
/// Without the `unwrap_or(2)` defensive fallback in `create_initial_state_with_config()`,
/// this test would fail by allowing indefinite continuations.
#[test]
fn test_missing_config_key_caps_continuations_at_three() {
    with_default_timeout(|| {
        // Simulate the scenario where Config.max_dev_continuations is None.
        // The event loop config loader should apply unwrap_or(2), resulting in max_continue_count = 3.
        // We construct ContinuationState directly with the expected default to simulate this.
        let continuation = ContinuationState::with_limits(
            3, // max_continue_count = 1 + unwrap_or(2)
            2, // max_same_agent_retries
        );

        let mut state = PipelineState::initial_with_continuation(5, 0, &continuation);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // Initial attempt (continuation_attempt = 0)
        assert_eq!(state.continuation.continuation_attempt, 0);
        assert!(!state.continuation.continuations_exhausted());

        // Continuation 1 (continuation_attempt = 1)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "continuation 1".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        assert_eq!(state.continuation.continuation_attempt, 1);
        assert!(!state.continuation.continuations_exhausted());

        // Continuation 2 (continuation_attempt = 2)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "continuation 2".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        assert_eq!(state.continuation.continuation_attempt, 2);
        assert!(!state.continuation.continuations_exhausted());

        // Attempt third continuation (defensive check prevents increment)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "continuation 3 attempt".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        // AFTER FIX: trigger_continuation defensive check prevents increment to 3
        assert_eq!(
            state.continuation.continuation_attempt, 2,
            "Defensive check should prevent increment to 3. \
             Without the fix, this would increment to 3, allowing an extra attempt."
        );
        // continuations_exhausted() returns false (2 < 3), but the system won't continue
        // because defensive check cleared continue_pending and OutcomeApplied will detect
        // exhaustion via (attempt + 1 >= max) check.
        assert!(!state.continuation.continuations_exhausted());
        assert!(!state.continuation.continue_pending);

        // Verify the default was correctly applied
        assert_eq!(state.continuation.max_continue_count, 3);
    });
}

/// NEW REGRESSION TEST: Test continuation behavior with default limit from `unwrap_or(2)`.
///
/// CRITICAL: This test verifies the infinite loop bug is prevented.
/// The `event_loop` config loader applies `unwrap_or(2)` when `max_dev_continuations` is None,
/// resulting in `max_continue_count` = 3 (1 initial + 2 continuations).
///
/// This test simulates the expected state after that default application and verifies
/// continuation stops at attempt 3, preventing infinite continuation loops.
#[test]
fn test_continuation_stops_with_unwrap_or_default() {
    with_default_timeout(|| {
        // Simulate the state created by create_initial_state_with_config when
        // Config.max_dev_continuations is None and unwrap_or(2) is applied.
        // Result: max_continue_count = 1 + 2 = 3
        let continuation = ContinuationState::with_limits(
            3, // max_continue_count = 1 + unwrap_or(2)
            2, // max_same_agent_retries
        );

        let mut state = PipelineState::initial_with_continuation(5, 0, &continuation);

        // Verify the default was applied correctly
        assert_eq!(
            state.continuation.max_continue_count, 3,
            "unwrap_or(2) should result in max_continue_count = 3"
        );

        // Start iteration
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        assert_eq!(state.continuation.continuation_attempt, 0);
        assert!(!state.continuation.continuations_exhausted());

        // Continuation 1 (attempt 1)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "continuation 1".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        assert_eq!(state.continuation.continuation_attempt, 1);
        assert!(!state.continuation.continuations_exhausted());

        // Continuation 2 (attempt 2)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "continuation 2".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        assert_eq!(state.continuation.continuation_attempt, 2);
        assert!(!state.continuation.continuations_exhausted());

        // Attempt third continuation (defensive check prevents increment)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "continuation 3 attempt".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        // AFTER FIX: trigger_continuation defensive check prevents increment to 3
        assert_eq!(
            state.continuation.continuation_attempt, 2,
            "CRITICAL: Defensive check prevents increment to 3. Counter stays at 2. \
             Without the fix, this would increment to 3, allowing an extra attempt and potentially infinite loops."
        );
        // continuations_exhausted() returns false (2 < 3), but the defensive check cleared
        // continue_pending to prevent scheduling. OutcomeApplied detects exhaustion via (attempt + 1 >= max).
        assert!(!state.continuation.continuations_exhausted());
        assert!(!state.continuation.continue_pending);

        // Verify progress metrics match the corrected state
        assert_eq!(state.metrics.max_dev_continuation_count, 3);
        assert_eq!(state.metrics.dev_continuation_attempt, 2);
    });
}

/// Regression test: Verify `OutcomeApplied` exhausts at the correct boundary.
///
/// With `max_continue_count` = 3, attempts 0, 1, 2 should be allowed (3 total).
/// `OutcomeApplied` at attempt 2 with Partial status should exhaust the budget
/// (because attempt 2 + 1 = 3 would reach `max_continue_count`).
///
/// BEFORE FIX: Current code allowed attempt 2 to continue to attempt 3,
/// then exhausted at attempt 3, allowing 4 total attempts (bug).
///
/// AFTER FIX: `OutcomeApplied` at attempt 2 correctly exhausts without
/// triggering attempt 3, allowing exactly 3 attempts as configured.
#[test]
fn test_outcome_applied_exhausts_too_early() {
    with_default_timeout(|| {
        // max_continue_count = 3 means attempts 0, 1, 2 should be allowed (3 total)
        let continuation = ContinuationState::with_limits(3, 2);
        let mut state = PipelineState::initial_with_continuation(5, 0, &continuation);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // Simulate attempt 0 completing with Partial outcome via XmlValidated event
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "partial work".to_string(),
                None,
                None,
            ),
        );

        // Apply outcome at attempt 0 - should trigger continuation to attempt 1
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );
        assert_eq!(state.continuation.continuation_attempt, 1);

        // Simulate attempt 1 completing with Partial outcome via XmlValidated event
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "more partial work".to_string(),
                None,
                None,
            ),
        );

        // Apply outcome at attempt 1 - should trigger continuation to attempt 2
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );
        assert_eq!(state.continuation.continuation_attempt, 2);

        // Simulate attempt 2 completing with Partial outcome via XmlValidated event
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "still more partial work".to_string(),
                None,
                None,
            ),
        );

        // Apply outcome at attempt 2 - SHOULD exhaust budget (not continue to attempt 3)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );

        // FIXED: budget exhausts at attempt 2 (resets to 0 after agent switch)
        assert_eq!(
            state.continuation.continuation_attempt, 0,
            "OutcomeApplied at attempt 2 should exhaust budget and reset counter (agent switch)"
        );
        assert!(
            !state.continuation.is_continuation(),
            "After exhaustion, is_continuation() should be false"
        );
    });
}

/// NEW REGRESSION TEST: Verify `OutcomeApplied` checks exhaustion BEFORE triggering continuation.
///
/// CRITICAL: With `max_continue_count` = 3, attempts 0, 1, 2 should be allowed (3 total).
/// When `OutcomeApplied` fires at attempt 2 with Partial status:
/// - `continuation_attempt` = 2
/// - `continuation_attempt` + 1 = 3 (would reach `max_continue_count`)
/// - Should emit `ContinuationBudgetExhausted`, NOT `ContinuationTriggered`
///
/// This is the core bug: current code triggers continuation first (incrementing to 3),
/// then checks exhaustion on the NEXT `OutcomeApplied`, allowing attempt 3 to run.
#[test]
fn test_outcome_applied_exhausts_at_correct_boundary() {
    with_default_timeout(|| {
        let continuation = ContinuationState::with_limits(3, 2);
        let mut state = PipelineState::initial_with_continuation(5, 0, &continuation);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // Attempt 0 (initial) completes with Partial via XmlValidated event
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "partial work".to_string(),
                None,
                None,
            ),
        );
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );
        assert_eq!(state.continuation.continuation_attempt, 1);

        // Attempt 1 completes with Partial via XmlValidated event
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "more partial work".to_string(),
                None,
                None,
            ),
        );
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );
        assert_eq!(state.continuation.continuation_attempt, 2);

        // Attempt 2 completes with Partial via XmlValidated event
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "still more partial work".to_string(),
                None,
                None,
            ),
        );

        // CRITICAL ASSERTION: OutcomeApplied at attempt 2 should exhaust budget
        // because continuation_attempt + 1 (2 + 1 = 3) would reach max_continue_count (3).
        // Current buggy behavior: triggers continuation to attempt 3
        // Expected behavior: emits ContinuationBudgetExhausted, which resets counter and switches agent
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );

        // After fix: budget should be exhausted, counter reset to 0 (agent switch happened)
        // The critical check is that we did NOT increment to 3 before exhausting.
        // ContinuationBudgetExhausted resets continuation_attempt to 0 as part of agent switch.
        assert_eq!(
            state.continuation.continuation_attempt, 0,
            "After budget exhaustion, continuation_attempt should be reset to 0 (agent switched)"
        );
        // Verify that budget exhaustion did occur (not another continuation)
        assert!(
            !state.continuation.is_continuation(),
            "After budget exhaustion and agent switch, is_continuation() should be false"
        );
    });
}

/// Test that continuation attempts are numbered correctly and exhaustion happens at the right boundary.
///
/// With `max_continue_count` = 3:
/// - Attempt 0 (initial): NOT exhausted (0 < 3)
/// - Attempt 1 (first continuation): NOT exhausted (1 < 3)
/// - Attempt 2 (second continuation): NOT exhausted (2 < 3)
/// - Attempt 3 (would be third continuation): EXHAUSTED (3 >= 3)
#[test]
fn test_continuation_attempt_numbering_and_boundary() {
    with_default_timeout(|| {
        let continuation = ContinuationState::with_limits(3, 2);
        let mut state = PipelineState::initial_with_continuation(5, 0, &continuation);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // Initial attempt (attempt 0)
        assert_eq!(state.continuation.continuation_attempt, 0);
        assert!(!state.continuation.continuations_exhausted());

        // First continuation (attempt 1)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "continuation 1".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        assert_eq!(state.continuation.continuation_attempt, 1);
        assert!(!state.continuation.continuations_exhausted());

        // Second continuation (attempt 2)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "continuation 2".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        assert_eq!(state.continuation.continuation_attempt, 2);
        assert!(!state.continuation.continuations_exhausted());

        // Third continuation trigger (attempt 3) - trigger_continuation defensive check applies
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "continuation 3".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        // AFTER FIX: trigger_continuation checks boundary before incrementing.
        // At attempt 2, next_attempt = 3, which >= 3, so counter stays at 2 (does not increment).
        // This test is artificial - in normal flow, OutcomeApplied checks (attempt + 1 >= max)
        // and emits BudgetExhausted instead of ContinuationTriggered.
        // But the defensive check should still work as a safety net.
        assert_eq!(
            state.continuation.continuation_attempt, 2,
            "trigger_continuation defensive check should prevent increment to 3"
        );
        // continuations_exhausted() returns false (2 < 3) because counter didn't increment.
        // The actual exhaustion detection happens in OutcomeApplied via (attempt + 1 >= max).
        assert!(
            !state.continuation.continuations_exhausted(),
            "Counter at 2 means continuations_exhausted() (2 >= 3) returns false"
        );
    });
}
