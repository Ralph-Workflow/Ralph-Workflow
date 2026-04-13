// Property-based tests for key reducer state invariants.
//
// Each `proptest!` block asserts an invariant that must hold for all
// inputs within the specified range.  The invariants target the *pure*
// reducer layer (`reduce`) – they drive it with generated inputs and
// check that structural guarantees are never violated.
//
// Invariants covered:
//   1. `dev_iterations_started` increments exactly once per `IterationStarted` event.
//   2. `dev_iterations_started >= dev_iterations_completed` after any prefix of events.
//   3. `continuation_attempt < max_continue_count` after any number of continuations.

use super::*;
use crate::reducer::event::DevelopmentEvent;
use crate::reducer::state::DevelopmentStatus;
use proptest::prelude::*;

proptest! {
    // -----------------------------------------------------------------------
    // Invariant 1 — dev_iterations_started increments exactly once per event
    //
    // For any initial `total_iters` and any `iteration` value, applying a
    // single `IterationStarted` event must increment `dev_iterations_started`
    // by exactly 1, regardless of what the iteration number is.
    // -----------------------------------------------------------------------
    #[test]
    fn dev_iterations_started_increments_once_per_event(
        total_iters in 1u32..=10u32,
        iteration in 0u32..=9u32,
    ) {
        let state = PipelineState::initial(total_iters, 0);
        let before = state.metrics.dev_iterations_started;
        let event = PipelineEvent::Development(DevelopmentEvent::IterationStarted { iteration });
        let after = reduce(state, event);
        prop_assert_eq!(
            after.metrics.dev_iterations_started,
            before + 1,
            "IterationStarted must increment dev_iterations_started by 1"
        );
    }

    // -----------------------------------------------------------------------
    // Invariant 2 — started >= completed at all times
    //
    // After applying N `IterationStarted` events (with no completion events),
    // `dev_iterations_started` must be >= `dev_iterations_completed`.
    // This holds trivially in this scenario (completed stays 0) but exercises
    // that the counter never wraps or goes negative.
    // -----------------------------------------------------------------------
    #[test]
    fn dev_iterations_started_gte_completed(
        n in 0u32..=8u32,
    ) {
        let total = n.max(1);
        let mut state = PipelineState::initial(total, 0);
        for i in 0..n {
            let event = PipelineEvent::Development(DevelopmentEvent::IterationStarted {
                iteration: i,
            });
            state = reduce(state, event);
        }
        prop_assert!(
            state.metrics.dev_iterations_started >= state.metrics.dev_iterations_completed,
            "dev_iterations_started ({}) must be >= dev_iterations_completed ({})",
            state.metrics.dev_iterations_started,
            state.metrics.dev_iterations_completed
        );
    }

    // -----------------------------------------------------------------------
    // Invariant 3 — continuation_attempt stays below max_continue_count
    //
    // Applying up to 15 consecutive `ContinuationTriggered` events must
    // never allow `continuation_attempt` to reach or exceed `max_continue_count`.
    // The reducer's `trigger_continuation` method stops incrementing once the
    // budget boundary is hit, keeping the value strictly less than the limit.
    // -----------------------------------------------------------------------
    #[test]
    fn continuation_attempt_never_reaches_max(
        max_cont in 1u32..=5u32,
        num_continuations in 1u32..=15u32,
    ) {
        let continuation = ContinuationState::with_limits(max_cont, 3);
        let mut state = PipelineState::initial_with_continuation(3, 0, &continuation);
        // Enter development phase.
        state = reduce(state, PipelineEvent::Development(DevelopmentEvent::PhaseStarted));
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::IterationStarted { iteration: 0 }),
        );
        for _ in 0..num_continuations {
            state = reduce(
                state,
                PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                    iteration: 0,
                    status: DevelopmentStatus::Partial,
                    summary: "work in progress".to_string(),
                    files_changed: None,
                    next_steps: None,
                }),
            );
            prop_assert!(
                state.continuation.continuation_attempt < state.continuation.max_continue_count,
                "continuation_attempt ({}) must be < max_continue_count ({}) – reducer must clamp",
                state.continuation.continuation_attempt,
                state.continuation.max_continue_count
            );
        }
    }
}
