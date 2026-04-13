//! Regression tests and defensive guard tests for continuation budget.
//!
//! Tests in this module verify:
//! - Default continuation limits prevent infinite loops
//! - `trigger_continuation` defensive check prevents counter increment
//! - Missing config keys don't cause infinite continuation loops
//! - Budget exhaustion correctly completes iterations

use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent};
use ralph_workflow::reducer::state::{ContinuationState, DevelopmentStatus, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;

use crate::test_timeout::with_default_timeout;

/// Integration test: Verify default `max_dev_continuations` prevents infinite loops.
///
/// This test simulates the production scenario where `max_dev_continuations` is missing
/// from config, and every attempt returns Partial status. The system MUST stop after
/// exactly 3 attempts (the default), not continue indefinitely.
///
/// Reproduces the infinite loop bug from the user report.
#[test]
fn test_default_continuation_limit_prevents_infinite_loop() {
    with_default_timeout(|| {
        // Simulate config with default: max_dev_continuations = 2 -> max_continue_count = 3
        let continuation = ContinuationState::with_limits(3, 2);
        let mut state = PipelineState::initial_with_continuation(5, 0, &continuation);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // Simulate a scenario where EVERY attempt returns Partial
        // (mimicking the infinite loop from the bug report where status never reaches Complete)

        // Attempt 0: Partial via XmlValidated event
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "Verification found only a subset implemented".to_string(),
                Some(vec!["src/file1.rs".to_string(), "src/file2.rs".to_string()]),
                Some("Complete missing plan domains".to_string()),
            ),
        );
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );
        assert_eq!(
            state.continuation.continuation_attempt, 1,
            "After attempt 0 with Partial, should continue to attempt 1"
        );
        assert!(!state.continuation.continuations_exhausted());

        // Attempt 1: Partial again via XmlValidated event
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "Most implementation slices present but not complete".to_string(),
                Some(vec!["src/file3.rs".to_string(), "src/file4.rs".to_string()]),
                Some("Add explicit parity map artifact".to_string()),
            ),
        );
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );
        assert_eq!(
            state.continuation.continuation_attempt, 2,
            "After attempt 1 with Partial, should continue to attempt 2"
        );
        assert!(!state.continuation.continuations_exhausted());

        // Attempt 2: Partial yet again via XmlValidated event
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "Still not fully satisfied".to_string(),
                Some(vec!["src/file5.rs".to_string()]),
                Some("Provide explicit evidence".to_string()),
            ),
        );
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );

        // CRITICAL: After attempt 2 with Partial, budget MUST be exhausted
        // (continuation_attempt resets to 0 after agent switch)
        assert_eq!(
            state.continuation.continuation_attempt, 0,
            "CRITICAL: After attempt 2, budget should be exhausted and counter reset (agent switch). \
             Without the fix, this would continue indefinitely."
        );
        assert!(
            !state.continuation.is_continuation(),
            "After exhaustion, is_continuation() should be false (agent switched). \
             This is the core fix that prevents the infinite loop bug."
        );

        // Verify that the next OutcomeApplied would NOT trigger another continuation
        // (because we've switched agents and reset the continuation state)
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                0,
                DevelopmentStatus::Partial,
                "New agent attempt".to_string(),
                None,
                None,
            ),
        );
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
        );
        // After switching agents, we start counting attempts again from 0
        // This first attempt on the new agent should continue normally
        assert_eq!(
            state.continuation.continuation_attempt, 1,
            "After agent switch, new agent's attempts start from 0, first continuation goes to 1"
        );
    });
}

/// Regression test: Verify `trigger_continuation` defensive check doesn't increment counter.
///
/// CRITICAL BUG: `trigger_continuation` sets `continuation_attempt` = `next_attempt` EVEN when
/// the defensive check triggers (line 128 in budget.rs). This allows the counter to reach
/// the boundary value (3) instead of staying below it (2).
///
/// BEFORE FIX: At attempt 2, `next_attempt` = 3, check `3 >= 3` is true, BUT line 128 still
/// executes `self.continuation_attempt = 3` before returning. The counter reaches 3, which
/// is AT the boundary, not over it. `continuations_exhausted()` returns true (3 >= 3), but
/// the counter shouldn't have reached 3 in the first place.
///
/// AFTER FIX: At attempt 2, `next_attempt` = 3, check `3 >= 3` is true, counter stays at 2,
/// method returns without updating the counter. This prevents the off-by-one bug.
///
/// This test directly simulates the scenario where `trigger_continuation`'s defensive check
/// is reached (which shouldn't happen in normal flow, but this tests the safety mechanism).
#[test]
fn test_trigger_continuation_defensive_check_prevents_counter_increment() {
    with_default_timeout(|| {
        let continuation = ContinuationState::with_limits(3, 2);
        let mut state = PipelineState::initial_with_continuation(5, 0, &continuation);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // Manually trigger continuations to test trigger_continuation's defensive check.
        // In normal flow, OutcomeApplied would prevent this by emitting BudgetExhausted.
        // But the defensive check should still work if somehow reached.

        // Continuation 1 (attempt 0 -> 1)
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

        // Continuation 2 (attempt 1 -> 2)
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

        // Attempt continuation 3 (attempt 2 -> should stay at 2, not go to 3)
        // The defensive check in trigger_continuation should prevent the increment.
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

        // CRITICAL ASSERTION: trigger_continuation defensive check must NOT increment counter.
        // BEFORE FIX: continuation_attempt would be 3 (line 128 sets it before returning)
        // AFTER FIX: continuation_attempt should stay at 2 (defensive check returns early)
        assert_eq!(
            state.continuation.continuation_attempt, 2,
            "BUG: trigger_continuation defensive check at budget.rs:128 increments counter to 3 \
             before returning. This is the off-by-one bug. The counter should stay at 2 when the \
             defensive check triggers (next_attempt = 3 >= max_continue_count = 3)."
        );

        // NOTE: continuations_exhausted() returns false (2 < 3) because counter stayed at 2.
        // The exhaustion is detected by OutcomeApplied checking (continuation_attempt + 1 >= max),
        // which correctly identifies that the NEXT attempt would exceed the budget.
        // The defensive check in trigger_continuation is a safety net that prevents scheduling
        // when somehow reached, but doesn't mark the state as exhausted via the counter.
        assert!(
            !state.continuation.continuations_exhausted(),
            "Counter is at 2, so continuations_exhausted() (2 >= 3) returns false. \
             Exhaustion is detected by OutcomeApplied checking (2 + 1 >= 3), not by the counter."
        );

        // Verify pending flags were cleared by defensive check
        assert!(
            !state.continuation.continue_pending,
            "Defensive check should clear continue_pending"
        );
        assert!(
            !state.continuation.context_write_pending,
            "Defensive check should clear context_write_pending"
        );
    });
}

/// Regression test for infinite continuation loop bug (wt-39).
///
/// # Bug Description
///
/// When `max_dev_continuations` is missing from config (relying on default value of 2,
/// yielding `max_continue_count` = 3), the system allowed continuations to proceed indefinitely
/// when work status remained non-Complete (e.g., always Partial or Failed).
///
/// # Root Cause
///
/// The defensive check in `trigger_continuation` (budget.rs:137-147) incremented the counter
/// BEFORE checking the boundary, allowing the counter to reach the max value instead of staying
/// below it. This off-by-one bug caused the system to allow one extra attempt beyond the limit.
///
/// # Fix
///
/// The boundary check was moved BEFORE the counter increment (budget.rs:138-147), ensuring the
/// counter stays at 2 when the defensive check triggers, not 3. The `OutcomeApplied` exhaustion
/// check (iteration_reducer.rs:169-180) detects exhaustion via `(attempt + 1 >= max)`, which
/// correctly identifies that the next attempt would exceed the budget.
///
/// # Test Strategy
///
/// This test simulates the exact scenario from the bug report:
/// 1. Config has `max_dev_continuations` = None, defaulting to 2 (`max_continue_count` = 3)
/// 2. Every attempt returns Partial status (never Complete)
/// 3. Verify continuation stops after exactly 3 attempts (0, 1, 2), not indefinitely
///
/// The test tracks total attempts and uses a safety limit to prevent an actual infinite loop
/// in the test itself. Before the fix, this test would fail because the counter would continue
/// past 3 attempts.
#[test]
fn test_missing_max_dev_continuations_prevents_infinite_loop() {
    with_default_timeout(|| {
        const MAX_SAFE_ATTEMPTS: usize = 10;

        // Reproduce the infinite loop bug from user report:
        // Config.max_dev_continuations is None (missing from config)
        // After unwrap_or(2) in create_initial_state_with_config, max_continue_count = 3
        let continuation = ContinuationState::with_limits(3, 2);
        let mut state = PipelineState::initial_with_continuation(5, 0, &continuation);
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // Track total attempt count to detect infinite loop
        let mut total_attempts = 0;

        // Simulate the scenario from logs: every attempt returns Partial (never Complete)
        for attempt in 0..MAX_SAFE_ATTEMPTS {
            // Simulate OutcomeApplied with Partial status via XmlValidated event
            state = reduce(
                state,
                PipelineEvent::development_xml_validated(
                    0,
                    DevelopmentStatus::Partial,
                    format!("Partial work attempt {attempt}"),
                    None,
                    Some("Continue work".to_string()),
                ),
            );

            state = reduce(
                state,
                PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
            );

            total_attempts += 1;

            // After exactly 3 attempts (0, 1, 2), continuation should exhaust
            // and agent chain should advance (resetting continuation_attempt to 0)
            if total_attempts == 3 {
                assert_eq!(
                    state.continuation.continuation_attempt, 0,
                    "CRITICAL BUG: After 3 attempts, continuation should be exhausted and counter reset. \
                     Instead, continuation continues indefinitely. This is the infinite loop bug."
                );
                break;
            }

            // If we reach more than 3 attempts without exhaustion, the bug persists
            assert!(
                total_attempts <= 3,
                "INFINITE LOOP BUG REPRODUCED: Continuation continued past 3 attempts (attempt {total_attempts}). \
                 Expected exhaustion at attempt 3 with max_continue_count=3."
            );
        }

        // Verify we stopped at exactly 3 attempts
        assert_eq!(
            total_attempts, 3,
            "Continuation should stop after exactly 3 attempts, not run indefinitely"
        );
    });
}

/// Regression test for infinite continuation loop across agent fallback (wt-39 v2).
///
/// # Bug Description
///
/// When continuation budget is exhausted but agents remain available, the system
/// switches to the next agent and stays in Development phase with reset continuation
/// state (`continuation_attempt=0`). If status remains non-Complete, this creates an
/// infinite loop pattern: attempt 1->2->exhaust->switch agent->restart->attempt 1->2->exhaust...
///
/// # Reproduction from Logs
///
/// The user's logs show:
/// - attempt 1 (Partial) -> attempt 2 (Failed) -> cleanup -> attempt 1 (Partial) -> ...
/// - All within iteration 0, never advancing to iteration 1
/// - Agent chain has multiple agents, so exhaustion isn't detected
///
/// # Expected Behavior
///
/// After continuation budget exhaustion with non-Complete status, the system should:
/// 1. Complete the current iteration (even with incomplete work)
/// 2. Advance to the next iteration if `dev_iters` remain
/// 3. Transition to `AwaitingDevFix` if both continuations AND iterations are exhausted
///
/// The system MUST NOT restart the continuation cycle with a fresh agent within the same iteration.
#[test]
fn test_continuation_budget_exhaustion_completes_iteration() {
    with_default_timeout(|| {
        const MAX_CYCLES: usize = 5;

        // Set up state with default continuation limit (max_continue_count = 3)
        // and multiple agents in the chain
        use ralph_workflow::agents::fallback::AgentRole;
        use ralph_workflow::reducer::state::AgentChainState;
        let agent_chain = AgentChainState::initial().with_agents(
            vec![
                "agent1".to_string(),
                "agent2".to_string(),
                "agent3".to_string(),
            ],
            vec![vec![], vec![], vec![]],
            AgentRole::Developer,
        );
        let continuation = ContinuationState::with_limits(3, 2);
        let mut state = PipelineState::initial_with_continuation(5, 0, &continuation);
        state.agent_chain = agent_chain;
        state = reduce(state, PipelineEvent::development_iteration_started(0));

        // Track cycle restarts to detect the infinite loop pattern
        let mut cycle_count = 0;

        for cycle in 0..MAX_CYCLES {
            // Simulate attempt 0 (initial): Partial status via XmlValidated event
            state = reduce(
                state,
                PipelineEvent::development_xml_validated(
                    0,
                    DevelopmentStatus::Partial,
                    format!("Partial work cycle {cycle} attempt 0"),
                    None,
                    Some("Continue".to_string()),
                ),
            );
            state = reduce(
                state,
                PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
            );

            // Should trigger continuation: attempt 0 -> 1
            assert_eq!(
                state.continuation.continuation_attempt, 1,
                "After attempt 0, should be at attempt 1"
            );

            // Simulate attempt 1: Partial status via XmlValidated event
            state = reduce(
                state,
                PipelineEvent::development_xml_validated(
                    0,
                    DevelopmentStatus::Partial,
                    format!("Partial work cycle {cycle} attempt 1"),
                    None,
                    Some("Continue".to_string()),
                ),
            );
            state = reduce(
                state,
                PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
            );

            // Should trigger continuation: attempt 1 -> 2
            assert_eq!(
                state.continuation.continuation_attempt, 2,
                "After attempt 1, should be at attempt 2"
            );

            // Simulate attempt 2: Partial status (will exhaust budget) via XmlValidated event
            state = reduce(
                state,
                PipelineEvent::development_xml_validated(
                    0,
                    DevelopmentStatus::Partial,
                    format!("Partial work cycle {cycle} attempt 2"),
                    None,
                    Some("Continue".to_string()),
                ),
            );
            state = reduce(
                state,
                PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
            );

            // After attempt 2, budget should be exhausted (attempt 2 + 1 = 3 >= max_continue_count 3)
            // The system should either:
            // (a) Complete iteration 0 and transition to CommitMessage (or another phase), OR
            // (b) Transition to AwaitingDevFix if agents exhausted
            //
            // The system MUST NOT restart continuation at attempt 1 within iteration 0 in Development phase.

            cycle_count += 1;

            // If we changed phase away from Development, the fix is working - break out
            if state.phase != ralph_workflow::reducer::event::PipelinePhase::Development {
                break;
            }

            // If we're still in Development but iteration advanced, that's also acceptable
            if state.iteration > 0 {
                break;
            }

            // BUG: Still in Development phase, iteration 0, with reset continuation counter
            assert!(
                !(state.continuation.continuation_attempt == 0 && state.iteration == 0),
                "INFINITE LOOP BUG: After continuation budget exhaustion at cycle {cycle_count}, \
                 continuation_attempt reset to 0 but still in Development phase iteration 0. \
                 Expected iteration to advance or phase to change. \
                 This reproduces the log pattern: 1->2->cleanup->1->2->cleanup..."
            );
        }

        // Verify we either advanced iteration or changed phase (not stuck in iteration 0)
        assert!(
            state.iteration > 0
                || state.phase != ralph_workflow::reducer::event::PipelinePhase::Development,
            "After continuation budget exhaustion, should either advance iteration or change phase, \
             not restart continuation cycle"
        );
    });
}
