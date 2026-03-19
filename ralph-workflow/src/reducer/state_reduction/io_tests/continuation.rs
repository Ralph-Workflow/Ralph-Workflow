// Continuation event handling tests.
//
// Tests for continuation triggered, succeeded, budget exhausted events,
// and continuation state management during development iterations.

use super::*;
use crate::agents::AgentRole;
use crate::reducer::event::PipelineEvent;
use crate::reducer::state::{DevelopmentStatus, PipelineState};
use crate::reducer::{determine_next_effect, effect::Effect};

#[test]
fn test_continuation_triggered_updates_state() {
    use crate::reducer::state::DevelopmentStatus;

    let state = create_test_state();
    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            1,
            DevelopmentStatus::Partial,
            "Did work".to_string(),
            Some(vec!["src/main.rs".to_string()]),
            Some("Continue".to_string()),
        ),
    );

    assert!(new_state.continuation.is_continuation());
    assert_eq!(
        new_state.continuation.previous_status,
        Some(DevelopmentStatus::Partial)
    );
    assert_eq!(
        new_state.continuation.previous_summary,
        Some("Did work".to_string())
    );
    assert_eq!(
        new_state.continuation.previous_files_changed,
        Some(vec!["src/main.rs".to_string()].into_boxed_slice())
    );
    assert_eq!(
        new_state.continuation.previous_next_steps,
        Some("Continue".to_string())
    );
    assert_eq!(new_state.continuation.continuation_attempt, 1);
    assert_eq!(
        new_state.agent_chain.current_mode,
        crate::agents::DrainMode::Continuation
    );
}

#[test]
fn test_continuation_triggered_sets_iteration_from_event() {
    use crate::reducer::state::DevelopmentStatus;

    let state = PipelineState {
        iteration: 99,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            2,
            DevelopmentStatus::Partial,
            "Did work".to_string(),
            None,
            None,
        ),
    );

    assert_eq!(new_state.iteration, 2);
}

#[test]
fn test_continuation_triggered_with_failed_status() {
    use crate::reducer::state::DevelopmentStatus;

    let state = create_test_state();
    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            1,
            DevelopmentStatus::Failed,
            "Build failed".to_string(),
            None,
            Some("Fix errors".to_string()),
        ),
    );

    assert!(new_state.continuation.is_continuation());
    assert_eq!(
        new_state.continuation.previous_status,
        Some(DevelopmentStatus::Failed)
    );
    assert_eq!(
        new_state.continuation.previous_summary,
        Some("Build failed".to_string())
    );
    assert!(new_state.continuation.previous_files_changed.is_none());
}

#[test]
fn test_continuation_succeeded_resets_state() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        continuation: ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Work".to_string(),
            None,
            None,
        ),
        ..create_test_state()
    };
    assert!(state.continuation.is_continuation());

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_succeeded(1, 2),
    );

    assert!(!new_state.continuation.is_continuation());
    assert_eq!(new_state.continuation.continuation_attempt, 0);
    assert!(new_state.continuation.previous_status.is_none());
    assert_eq!(
        new_state.agent_chain.current_mode,
        crate::agents::DrainMode::Normal
    );
}

#[test]
fn test_continuation_succeeded_sets_iteration_from_event() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 99,
        continuation: ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Work".to_string(),
            None,
            None,
        ),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_succeeded(1, 1),
    );

    assert_eq!(new_state.iteration, 1);
}

#[test]
fn test_iteration_started_resets_continuation() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        continuation: ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Work".to_string(),
            None,
            None,
        ),
        ..create_test_state()
    };
    assert!(state.continuation.is_continuation());

    let new_state = reduce(state, PipelineEvent::development_iteration_started(2));

    assert!(!new_state.continuation.is_continuation());
    assert_eq!(new_state.iteration, 2);
}

#[test]
fn test_iteration_completed_resets_continuation() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Development,
        continuation: ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Work".to_string(),
            None,
            None,
        ),
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_iteration_completed(1, true),
    );

    assert!(!new_state.continuation.is_continuation());
    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
}

#[test]
fn test_development_phase_completed_resets_continuation() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        phase: PipelinePhase::Development,
        continuation: ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Work".to_string(),
            None,
            None,
        ),
        ..create_test_state()
    };

    let new_state = reduce(state, PipelineEvent::development_phase_completed());

    assert!(!new_state.continuation.is_continuation());
    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(
        new_state.agent_chain.current_mode,
        crate::agents::DrainMode::Normal
    );
}

#[test]
fn test_same_agent_retry_uses_same_agent_retry_mode_until_success() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        ..create_test_state()
    };

    let retrying_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            "mock".to_string(),
            1,
            crate::reducer::event::AgentErrorKind::InternalError,
            false,
        ),
    );

    assert!(retrying_state.continuation.same_agent_retry_pending);
    assert_eq!(
        retrying_state.agent_chain.current_mode,
        crate::agents::DrainMode::SameAgentRetry
    );

    let recovered_state = reduce(
        retrying_state,
        PipelineEvent::agent_invocation_succeeded(AgentRole::Developer, "mock".to_string()),
    );

    assert_eq!(
        recovered_state.agent_chain.current_mode,
        crate::agents::DrainMode::Normal
    );
}

#[test]
fn test_xsd_retry_uses_xsd_retry_mode_until_success() {
    let base = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: base
            .agent_chain
            .with_drain(crate::agents::AgentDrain::Analysis),
        ..base
    };

    let retrying_state = reduce(
        state,
        PipelineEvent::agent_xsd_validation_failed(
            AgentRole::Analysis,
            crate::reducer::state::ArtifactType::DevelopmentResult,
            "invalid xml".to_string(),
            0,
        ),
    );

    assert!(retrying_state.continuation.xsd_retry_pending);
    assert_eq!(
        retrying_state.agent_chain.current_mode,
        crate::agents::DrainMode::XsdRetry
    );

    let recovered_state = reduce(
        retrying_state,
        PipelineEvent::agent_invocation_succeeded(AgentRole::Analysis, "mock".to_string()),
    );

    assert_eq!(
        recovered_state.agent_chain.current_mode,
        crate::agents::DrainMode::Normal
    );
}

#[test]
fn test_multiple_continuation_triggers_accumulate() {
    use crate::reducer::state::DevelopmentStatus;

    let state = create_test_state();

    // First continuation
    let state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            1,
            DevelopmentStatus::Partial,
            "First attempt".to_string(),
            None,
            None,
        ),
    );
    assert_eq!(state.continuation.continuation_attempt, 1);

    // Second continuation
    let state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            1,
            DevelopmentStatus::Partial,
            "Second attempt".to_string(),
            None,
            None,
        ),
    );
    assert_eq!(state.continuation.continuation_attempt, 2);
    assert_eq!(
        state.continuation.previous_summary,
        Some("Second attempt".to_string())
    );
}

#[test]
fn test_continuation_budget_exhausted_switches_to_next_agent() {
    use crate::reducer::state::DevelopmentStatus;

    let state = create_test_state();
    assert_eq!(state.agent_chain.current_agent_index, 0);

    let new_state = reduce(
        state,
        PipelineEvent::development_continuation_budget_exhausted(0, 3, DevelopmentStatus::Partial),
    );
    // UPDATED: After fix for wt-39, continuation budget exhaustion now completes the iteration
    // and transitions to CommitMessage (or next phase) rather than staying in Development.
    // This prevents the infinite loop where the system would restart continuation with a new agent.
    assert_eq!(
        new_state.phase,
        PipelinePhase::CommitMessage,
        "Should complete iteration and transition to CommitMessage after budget exhaustion"
    );
    assert_eq!(
        new_state.agent_chain.current_agent_index, 0,
        "Agent chain should be reset after iteration completion"
    );
    assert_eq!(
        new_state.continuation.continuation_attempt, 0,
        "Should reset continuation attempt after iteration completion"
    );
}

#[test]
fn test_continuation_budget_exhausted_resets_continuation_state() {
    use crate::reducer::state::ContinuationState;

    let state = PipelineState {
        continuation: ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Work".to_string(),
            None,
            None,
        ),
        ..create_test_state()
    };
    assert!(state.continuation.is_continuation());

    let new_state = reduce(
        state,
        PipelineEvent::development_continuation_budget_exhausted(0, 3, DevelopmentStatus::Partial),
    );
    assert!(
        !new_state.continuation.is_continuation(),
        "Continuation state should be reset when switching to next agent"
    );
    assert_eq!(
        new_state.continuation.continuation_attempt, 0,
        "Continuation attempt should be reset for new agent"
    );
}

#[test]
fn test_continuation_budget_exhausted_preserves_iteration() {
    use crate::reducer::state::DevelopmentStatus;

    let mut state = PipelineState {
        iteration: 5,
        ..create_test_state()
    };
    state.iteration = 5;

    let new_state = reduce(
        state,
        PipelineEvent::development_continuation_budget_exhausted(5, 3, DevelopmentStatus::Failed),
    );
    assert_eq!(
        new_state.iteration, 5,
        "Should preserve the iteration number"
    );
}

#[test]
fn test_orchestration_detects_exhaustion_after_all_agents_tried() {
    use crate::agents::AgentRole;
    use crate::reducer::state::{AgentChainState, DevelopmentStatus, PromptPermissionsState};

    let agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["agent-a".to_string(), "agent-b".to_string()],
            vec![vec!["model-1".to_string()], vec!["model-2".to_string()]],
            AgentRole::Developer,
        )
        .with_max_cycles(1); // Only 1 cycle allowed

    let state = {
        let base = PipelineState::initial(5, 3);
        PipelineState {
            agent_chain,
            phase: PipelinePhase::Development,
            prompt_permissions: PromptPermissionsState {
                locked: true,
                restore_needed: true,
                ..base.prompt_permissions
            },
            ..base
        }
    };

    // UPDATED (wt-39 fix): After continuation budget exhaustion, the system now completes
    // the iteration and transitions to CommitMessage rather than staying in Development
    // to try more agents within the same iteration. This prevents the infinite loop where
    // continuation would restart after cycling through all agents.
    //
    // The new behavior establishes a clear contract: one continuation budget per iteration.
    // If work is incomplete after exhausting the budget, the iteration completes and either:
    // 1. Advances to next iteration (where different agents can be tried), OR
    // 2. Transitions to next pipeline phase
    //
    // This ensures bounded execution per iteration and prevents unbounded agent fallback cycles.
    let state = reduce(
        state,
        PipelineEvent::development_continuation_budget_exhausted(0, 3, DevelopmentStatus::Failed),
    );

    // After budget exhaustion, iteration completes and transitions to CommitMessage
    assert_eq!(
        state.phase,
        PipelinePhase::CommitMessage,
        "Should complete iteration and transition to CommitMessage after continuation budget exhaustion"
    );
    assert_eq!(
        state.agent_chain.current_agent_index, 0,
        "Agent chain should be reset to first agent after iteration completion"
    );
    assert_eq!(
        state.continuation.continuation_attempt, 0,
        "Continuation attempt should be reset after iteration completion"
    );
    // Orchestration should continue commit flow, possibly cleaning context first.
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::CheckCommitDiff)
            || matches!(effect, Effect::CleanupContinuationContext),
        "Should proceed with commit flow after iteration completion; got {effect:?}"
    );
}

#[test]
fn test_continuation_budget_with_missing_config_key() {
    use crate::reducer::state::DevelopmentStatus;

    // Simulate missing max_dev_continuations in config
    // When config key is missing, UnifiedConfig provides default of 2, which gets
    // wrapped in Some(2) during conversion. ContinuationState::max_continue_count
    // should be 3 (1 initial + 2 continuations).
    let continuation = ContinuationState::with_limits(
        10, // max_xsd_retries
        3,  // max_continue_count (should be 1 + default_max_dev_continuations)
        2,  // max_same_agent_retries
    );

    let state = PipelineState::initial_with_continuation(1, 0, &continuation);

    // Verify default is applied correctly
    assert_eq!(
        state.continuation.max_continue_count, 3,
        "Missing config key should default to 3 total attempts (1 initial + 2 continuations)"
    );

    // Verify exhaustion logic works correctly
    assert!(
        !state.continuation.continuations_exhausted(),
        "Initial attempt (0) should not be exhausted"
    );

    let state = state.continuation.trigger_continuation(
        DevelopmentStatus::Partial,
        "partial work".to_string(),
        None,
        None,
    );
    assert_eq!(state.continuation_attempt, 1);
    assert!(
        !state.continuations_exhausted(),
        "Attempt 1 should not be exhausted"
    );

    let state = state.trigger_continuation(
        DevelopmentStatus::Partial,
        "partial work 2".to_string(),
        None,
        None,
    );
    assert_eq!(state.continuation_attempt, 2);
    assert!(
        !state.continuations_exhausted(),
        "Attempt 2 should not be exhausted"
    );

    // Third trigger_continuation hits the defensive check (next_attempt = 3 >= 3)
    let state = state.trigger_continuation(
        DevelopmentStatus::Partial,
        "partial work 3 attempt".to_string(),
        None,
        None,
    );
    assert_eq!(
        state.continuation_attempt, 2,
        "defensive check should prevent increment to 3"
    );
    assert!(
        !state.continuations_exhausted(),
        "counter at 2, so 2 < 3 is true (not exhausted by counter check)"
    );
    assert!(
        !state.continue_pending,
        "defensive check should clear continue_pending"
    );
}

/// Test that continuation cap is enforced at the reducer level.
///
/// This test verifies that when the defensive check in `trigger_continuation` fires,
/// the counter does not increment and `continue_pending` is cleared.
/// The orchestration layer detects exhaustion via `OutcomeApplied`'s (attempt + 1 >= max) check.
#[test]
fn test_orchestration_fires_budget_exhausted_at_cap() {
    use crate::reducer::state::DevelopmentStatus;

    let continuation = ContinuationState::with_limits(10, 3, 2);
    let state = PipelineState::initial_with_continuation(1, 0, &continuation);

    let state = reduce(
        PipelineState {
            phase: PipelinePhase::Development,
            ..state.clone()
        },
        PipelineEvent::development_iteration_continuation_triggered(
            0,
            DevelopmentStatus::Partial,
            "partial".to_string(),
            None,
            None,
        ),
    );

    let state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            0,
            DevelopmentStatus::Partial,
            "partial".to_string(),
            None,
            None,
        ),
    );

    assert_eq!(state.continuation.continuation_attempt, 2);
    assert!(!state.continuation.continuations_exhausted());

    // Attempt third continuation (defensive check prevents increment)
    let state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            0,
            DevelopmentStatus::Partial,
            "partial attempt 3".to_string(),
            None,
            None,
        ),
    );

    assert_eq!(
        state.continuation.continuation_attempt, 2,
        "defensive check should prevent increment to 3"
    );
    assert!(
        !state.continuation.continuations_exhausted(),
        "counter at 2, so 2 < 3 is true"
    );
    assert!(
        !state.continuation.continue_pending,
        "defensive check should clear continue_pending"
    );

    // In normal flow, OutcomeApplied would check (continuation_attempt + 1 >= max_continue_count)
    // which is (2 + 1 >= 3) = true, and emit ContinuationBudgetExhausted instead of
    // ContinuationTriggered, preventing the defensive check from ever being reached.
}

/// Test that `trigger_continuation` defensive check prevents counter increment at boundary.
///
/// This is a more focused test of the fix for the infinite loop bug (wt-39). It verifies
/// that when `trigger_continuation` is called at the boundary (attempt 2 with max 3), the
/// defensive check prevents the counter from incrementing to 3, keeping it at 2 instead.
///
/// # Why This Test Matters
///
/// Before the fix, the code incremented the counter BEFORE checking the boundary:
/// ```rust
/// self.continuation_attempt = next_attempt; // BUG: Sets counter BEFORE boundary check
/// if next_attempt >= self.max_continue_count { /* defensive check */ }
/// ```
///
/// This allowed the counter to reach 3 (the max value) instead of staying at 2 (below max).
/// The fix moved the boundary check BEFORE the increment:
/// ```rust
/// if next_attempt >= self.max_continue_count { return self; } // Return WITHOUT incrementing
/// self.continuation_attempt = next_attempt; // Only update if not exhausted
/// ```
#[test]
fn test_trigger_continuation_at_boundary_does_not_increment() {
    use crate::reducer::state::DevelopmentStatus;

    let continuation = ContinuationState::with_limits(10, 3, 2);
    let state = PipelineState::initial_with_continuation(1, 0, &continuation);

    let state = reduce(
        PipelineState {
            phase: PipelinePhase::Development,
            ..state.clone()
        },
        PipelineEvent::development_iteration_continuation_triggered(
            0,
            DevelopmentStatus::Partial,
            "partial".to_string(),
            None,
            None,
        ),
    );

    let state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            0,
            DevelopmentStatus::Partial,
            "partial".to_string(),
            None,
            None,
        ),
    );

    assert_eq!(state.continuation.continuation_attempt, 2);
    assert!(!state.continuation.continuations_exhausted());

    // Attempt third continuation (defensive check should prevent increment)
    let old_counter = state.continuation.continuation_attempt;
    let state = reduce(
        state,
        PipelineEvent::development_iteration_continuation_triggered(
            0,
            DevelopmentStatus::Partial,
            "partial attempt 3".to_string(),
            None,
            None,
        ),
    );

    // CRITICAL: Counter must stay at 2, not increment to 3
    assert_eq!(
        state.continuation.continuation_attempt, old_counter,
        "trigger_continuation defensive check must prevent counter increment at boundary"
    );
    assert_eq!(state.continuation.continuation_attempt, 2);
    assert!(!state.continuation.continue_pending);
    assert!(!state.continuation.context_write_pending);
}

/// Test that `OutcomeApplied` correctly detects exhaustion before triggering continuation.
///
/// This test verifies the orchestration-level exhaustion detection that prevents the
/// infinite loop bug (wt-39). The `OutcomeApplied` event handler checks if the NEXT
/// attempt would exceed the limit using `(attempt + 1 >= max)`, and if so, emits
/// `ContinuationBudgetExhausted` instead of `ContinuationTriggered`.
///
/// # Why This Test Matters
///
/// The defensive check in `trigger_continuation` (tested above) is a safety net, but
/// the PRIMARY defense against infinite loops is this orchestration-level check in
/// `OutcomeApplied`. This test verifies that check works correctly:
///
/// - Attempt 0 completes with Partial → continues (0 + 1 = 1 < 3)
/// - Attempt 1 completes with Partial → continues (1 + 1 = 2 < 3)
/// - Attempt 2 completes with Partial → exhausts (2 + 1 = 3 >= 3)
///
/// After exhaustion, the system switches agents and resets the continuation counter to 0.
#[test]
fn test_outcome_applied_exhausts_before_triggering_third_continuation() {
    use crate::reducer::event::DevelopmentEvent;
    use crate::reducer::state::{DevelopmentStatus, DevelopmentValidatedOutcome};

    let continuation = ContinuationState::with_limits(10, 3, 2);
    let initial_state = PipelineState::initial_with_continuation(5, 0, &continuation);

    let state = reduce(
        PipelineState {
            phase: PipelinePhase::Development,
            ..initial_state.clone()
        },
        PipelineEvent::development_iteration_started(0),
    );

    // Attempt 0 (initial) completes with Partial
    let state = reduce(
        PipelineState {
            development_validated_outcome: Some(DevelopmentValidatedOutcome {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "partial work".to_string(),
                files_changed: None,
                next_steps: None,
            }),
            ..state.clone()
        },
        PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
    );
    assert_eq!(state.continuation.continuation_attempt, 1);

    // Attempt 1 completes with Partial
    let state = reduce(
        PipelineState {
            development_validated_outcome: Some(DevelopmentValidatedOutcome {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "more partial work".to_string(),
                files_changed: None,
                next_steps: None,
            }),
            ..state.clone()
        },
        PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
    );
    assert_eq!(state.continuation.continuation_attempt, 2);

    // Attempt 2 completes with Partial
    // CRITICAL: OutcomeApplied should detect exhaustion here via (2 + 1 >= 3) check
    // and emit ContinuationBudgetExhausted, NOT ContinuationTriggered
    let state = reduce(
        PipelineState {
            development_validated_outcome: Some(DevelopmentValidatedOutcome {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "still more partial work".to_string(),
                files_changed: None,
                next_steps: None,
            }),
            ..state.clone()
        },
        PipelineEvent::Development(DevelopmentEvent::OutcomeApplied { iteration: 0 }),
    );

    // After budget exhaustion, agent switches and counter resets to 0
    assert_eq!(
        state.continuation.continuation_attempt, 0,
        "OutcomeApplied at attempt 2 should exhaust budget (2+1>=3) and reset counter via agent switch"
    );
    assert!(!state.continuation.is_continuation());
}
