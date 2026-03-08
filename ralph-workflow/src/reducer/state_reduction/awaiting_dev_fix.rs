//! `AwaitingDevFix` event reduction.
//!
//! Handles events during the failure remediation phase.

use crate::reducer::event::{AwaitingDevFixEvent, PipelinePhase};
use crate::reducer::state::PipelineState;

/// Reduce `AwaitingDevFix` events.
///
/// This phase handles pipeline failure remediation by tracking the dev-fix
/// flow state and transitioning to Interrupted after completion marker emission.
pub(super) fn reduce_awaiting_dev_fix_event(
    state: PipelineState,
    event: AwaitingDevFixEvent,
) -> PipelineState {
    match event {
        AwaitingDevFixEvent::DevFixTriggered { .. } => {
            // Record that dev-fix was triggered, stay in AwaitingDevFix phase
            PipelineState {
                dev_fix_triggered: true,
                ..state
            }
        }
        AwaitingDevFixEvent::DevFixSkipped { .. } => {
            // Dev-fix was skipped (disabled/unavailable feature).
            // Treat this as a completed recovery attempt so unattended orchestration
            // can advance into the recovery loop instead of re-triggering dev-fix
            // indefinitely.

            let new_attempt_count = state.dev_fix_attempt_count + 1;
            let new_level = match new_attempt_count {
                1..=3 => 1,
                4..=6 => 2,
                7..=9 => 3,
                _ => 4,
            };

            PipelineState {
                dev_fix_triggered: true,
                dev_fix_attempt_count: new_attempt_count,
                recovery_escalation_level: new_level,
                ..state
            }
        }
        AwaitingDevFixEvent::DevFixCompleted {
            success: _,
            summary: _,
        } => {
            // Dev-fix attempt completed. Decide whether to:
            // 1. Attempt recovery at current level
            // 2. Escalate to next recovery level

            let new_attempt_count = state.dev_fix_attempt_count + 1;

            // Determine recovery escalation level based on attempt count
            // Level 1 (attempts 1-3): Retry same operation
            // Level 2 (attempts 4-6): Reset to phase start
            // Level 3 (attempts 7-9): Reset iteration counter
            // Level 4 (attempts 10+): Reset to iteration 0
            let new_level = match new_attempt_count {
                1..=3 => 1,
                4..=6 => 2,
                7..=9 => 3,
                _ => 4,
            };

            // Prepare for recovery attempt at the determined level.
            //
            // IMPORTANT: Do not transition to Interrupted directly here.
            // Internal failures are handled via recovery attempts; termination is reserved
            // for explicit external/catastrophic conditions and must go through the single
            // completion-marker path: Effect::EmitCompletionMarkerAndTerminate ->
            // CompletionMarkerEmitted.
            PipelineState {
                dev_fix_attempt_count: new_attempt_count,
                recovery_escalation_level: new_level,
                // Stay in AwaitingDevFix until recovery is attempted
                ..state
            }
        }
        AwaitingDevFixEvent::DevFixAgentUnavailable { .. } => {
            // Dev-fix agent unavailable (quota/usage limit). Stay in AwaitingDevFix so
            // orchestration can keep the unattended recovery loop running.
            state
        }
        AwaitingDevFixEvent::CompletionMarkerEmitted { .. } => {
            // Completion marker emitted, transition to Interrupted
            PipelineState {
                phase: PipelinePhase::Interrupted,
                previous_phase: Some(state.phase),
                completion_marker_pending: false,
                completion_marker_reason: None,
                ..state
            }
        }
        AwaitingDevFixEvent::CompletionMarkerWriteFailed { is_failure, error } => {
            // Marker write failed; stay in AwaitingDevFix but set an explicit retry flag so
            // orchestration deterministically re-derives EmitCompletionMarkerAndTerminate.
            PipelineState {
                completion_marker_pending: true,
                completion_marker_is_failure: is_failure,
                completion_marker_reason: Some(error),
                ..state
            }
        }
        AwaitingDevFixEvent::RecoveryAttempted {
            level,
            attempt_count: _,
            target_phase,
        } => {
            // Recovery state transitions documented for clarity:
            //
            // Level 1: Retry same operation (attempts 1-3)
            //   - No state reset, just transition back to failed phase
            //   - Orchestration will derive the same effect that failed
            //   - Example: If InvokeAgent failed, retry InvokeAgent
            //
            // Level 2: Reset to phase start (attempts 4-6)
            //   - Clear all phase-specific progress flags
            //   - Orchestration starts the phase from scratch
            //   - Preserves: iteration counter, reviewer_pass, other phases
            //   - Example: Clear development_agent_invoked_iteration, restart from PrepareDevelopmentContext
            //
            // Level 3: Reset iteration (attempts 7-9)
            //   - Decrement iteration counter (floor at 0)
            //   - Clear Planning/Development/Commit flags
            //   - Transition to Planning phase to redo iteration
            //   - Preserves: reviewer_pass, total_iterations
            //
            // Level 4: Complete reset (attempts 10+)
            //   - Reset iteration to 0
            //   - Clear Planning/Development/Commit flags
            //   - Transition to Planning phase for full restart
            //   - Preserves: reviewer_pass, total_iterations

            // Base state with phase transition
            let mut new_state = PipelineState {
                phase: target_phase,
                previous_phase: Some(PipelinePhase::AwaitingDevFix),
                // Keep recovery tracking fields so we can escalate if this fails
                ..state
            };

            // Apply state reset based on escalation level
            new_state = match level {
                1 => {
                    // Level 1: Simple retry - just transition back, no state reset
                    new_state
                }
                2 => {
                    // Level 2: Reset to phase start - clear phase-specific progress flags
                    let mut reset = new_state.clear_phase_flags(target_phase);

                    // IMPORTANT: Level 2 is a true "phase start" restart.
                    // Clear continuation/retry flags that have higher orchestration
                    // priority than normal phase sequencing (same-agent retry, XSD retry,
                    // continuation pending, context write/cleanup pending).
                    reset.continuation = reset.continuation.clone().reset();

                    // Clear phase-scoped materialized prompt inputs so prompt preparation
                    // reruns from scratch for the restarted phase.
                    reset.prompt_inputs = match target_phase {
                        PipelinePhase::Planning => reset
                            .prompt_inputs
                            .clone()
                            .with_planning_cleared()
                            .with_xsd_retry_cleared(),
                        PipelinePhase::Development => reset
                            .prompt_inputs
                            .clone()
                            .with_development_cleared()
                            .with_xsd_retry_cleared(),
                        PipelinePhase::Review => reset
                            .prompt_inputs
                            .clone()
                            .with_review_cleared()
                            .with_xsd_retry_cleared(),
                        PipelinePhase::CommitMessage => reset
                            .prompt_inputs
                            .clone()
                            .with_commit_cleared()
                            .with_xsd_retry_cleared(),
                        _ => reset.prompt_inputs.clone().with_xsd_retry_cleared(),
                    };

                    // Planning phase has global prerequisites at the true phase start.
                    // If we are resetting to Planning phase start, we must re-run these
                    // prerequisite effects; otherwise orchestration will skip them and the
                    // "phase start" reset won't actually restart from the beginning.
                    if matches!(target_phase, PipelinePhase::Planning) {
                        reset.context_cleaned = false;
                        reset.gitignore_entries_ensured = false;
                    }

                    reset
                }
                3 => {
                    // Level 3: Reset iteration counter - decrement iteration and restart from Planning.
                    // Advance recovery_epoch so PromptScopeKey replay identity changes with scope.
                    let mut s = new_state.reset_iteration();
                    s.recovery_epoch += 1;
                    s
                }
                _ => {
                    // Level 4+: Complete reset - reset to iteration 0, restart from Planning.
                    // Advance recovery_epoch so PromptScopeKey replay identity changes with scope.
                    let mut s = new_state.reset_to_iteration_zero();
                    s.recovery_epoch += 1;
                    s
                }
            };

            // Recovery must also reset agent-chain state.
            //
            // If the original failure was agent-chain exhaustion, leaving the chain exhausted
            // would cause immediate re-failure on the next orchestration cycle.
            //
            // Semantics:
            // - Always reset for Level 2+ recovery (phase/iteration resets imply fresh work).
            // - Also reset for Level 1 if the chain is already exhausted.
            if level >= 2 || new_state.agent_chain.is_exhausted() {
                let role = new_state.agent_chain.current_role;
                new_state.agent_chain = new_state.agent_chain.reset_for_role(role);
            }

            new_state
        }
        AwaitingDevFixEvent::RecoveryEscalated {
            from_level: _,
            to_level,
            reason: _,
        } => {
            // Recovery escalated - update level, stay in AwaitingDevFix
            PipelineState {
                recovery_escalation_level: to_level,
                ..state
            }
        }
        AwaitingDevFixEvent::RecoverySucceeded {
            level: _,
            total_attempts: _,
        } => {
            // Recovery succeeded - clear recovery state and resume normal operation
            PipelineState {
                dev_fix_attempt_count: 0,
                recovery_escalation_level: 0,
                failed_phase_for_recovery: None,
                // Stay in current phase (which should be the recovered phase)
                ..state
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::AgentRole;
    use crate::reducer::event::{AwaitingDevFixEvent, PipelineEvent, PipelinePhase};
    use crate::reducer::reduce;
    use crate::reducer::state::AgentChainState;

    #[test]
    fn dev_fix_completed_does_not_directly_interrupt_when_attempts_exhausted() {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.dev_fix_attempt_count = 12;
        state.recovery_escalation_level = 4;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                success: false,
                summary: Some("attempt 13".to_string()),
            }),
        );

        assert_eq!(
            new_state.phase,
            PipelinePhase::AwaitingDevFix,
            "expected to remain in AwaitingDevFix so orchestration can emit completion marker"
        );
        assert_eq!(new_state.dev_fix_attempt_count, 13);
    }

    #[test]
    fn recovery_attempted_uses_event_target_phase_not_state_snapshot() {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.recovery_escalation_level = 2;
        state.dev_fix_attempt_count = 4;

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 2,
                attempt_count: 4,
                target_phase: PipelinePhase::Planning,
            }),
        );

        assert_eq!(new_state.phase, PipelinePhase::Planning);
    }

    #[test]
    fn recovery_attempted_resets_agent_chain_when_exhausted() {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;

        let mut chain = AgentChainState::initial()
            .with_agents(
                vec!["dev".to_string()],
                vec![vec!["model".to_string()]],
                AgentRole::Developer,
            )
            .with_max_cycles(1);
        chain.retry_cycle = 1;
        chain.current_agent_index = 0;
        chain.current_model_index = 0;
        assert!(chain.is_exhausted());
        state.agent_chain = chain;

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 1,
                attempt_count: 1,
                target_phase: PipelinePhase::Development,
            }),
        );

        assert!(!new_state.agent_chain.is_exhausted());
        assert_eq!(new_state.agent_chain.retry_cycle, 0);
        assert_eq!(new_state.phase, PipelinePhase::Development);
    }

    #[test]
    fn dev_fix_skipped_advances_recovery_state_to_avoid_infinite_trigger_loop() {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.dev_fix_triggered = false;
        state.dev_fix_attempt_count = 0;
        state.recovery_escalation_level = 0;
        state.failed_phase_for_recovery = Some(PipelinePhase::CommitMessage);

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixSkipped {
                reason: "disabled".to_string(),
            }),
        );

        assert!(
            new_state.dev_fix_triggered,
            "DevFixSkipped should mark dev-fix as handled so orchestration can progress"
        );
        assert_eq!(new_state.dev_fix_attempt_count, 1);
        assert_eq!(new_state.recovery_escalation_level, 1);
        assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
        assert_eq!(
            new_state.failed_phase_for_recovery,
            Some(PipelinePhase::CommitMessage)
        );
    }

    #[test]
    fn level_2_phase_start_recovery_clears_retry_and_continuation_flags() {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;

        // Simulate being stuck in a retry/continuation path before recovery.
        state.continuation.xsd_retry_pending = true;
        state.continuation.xsd_retry_session_reuse_pending = true;
        state.continuation.same_agent_retry_pending = true;
        state.continuation.same_agent_retry_reason =
            Some(crate::reducer::state::SameAgentRetryReason::Timeout);
        state.continuation.continue_pending = true;
        state.continuation.fix_continue_pending = true;
        state.continuation.context_write_pending = true;
        state.continuation.context_cleanup_pending = true;

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 2,
                attempt_count: 4,
                target_phase: PipelinePhase::CommitMessage,
            }),
        );

        assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
        assert!(!new_state.continuation.xsd_retry_pending);
        assert!(!new_state.continuation.xsd_retry_session_reuse_pending);
        assert!(!new_state.continuation.same_agent_retry_pending);
        assert!(new_state.continuation.same_agent_retry_reason.is_none());
        assert!(!new_state.continuation.continue_pending);
        assert!(!new_state.continuation.fix_continue_pending);
        assert!(!new_state.continuation.context_write_pending);
        assert!(!new_state.continuation.context_cleanup_pending);
    }

    #[test]
    fn completion_marker_write_failed_sets_pending_flag_for_deterministic_retry() {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.completion_marker_pending = false;

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::CompletionMarkerWriteFailed {
                is_failure: true,
                error: "disk full".to_string(),
            }),
        );

        assert!(new_state.completion_marker_pending);
        assert!(new_state.completion_marker_is_failure);
        assert_eq!(
            new_state.completion_marker_reason.as_deref(),
            Some("disk full")
        );
        assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    }

    // =========================================================================
    // recovery_epoch tests (RFC-007 corrective action #2)
    // =========================================================================

    #[test]
    fn level_1_recovery_does_not_change_recovery_epoch() {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.recovery_epoch = 0;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 1,
                attempt_count: 1,
                target_phase: PipelinePhase::Development,
            }),
        );

        assert_eq!(
            new_state.recovery_epoch, 0,
            "Level 1 recovery must not change recovery_epoch"
        );
    }

    #[test]
    fn level_2_recovery_does_not_change_recovery_epoch() {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.recovery_epoch = 0;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 2,
                attempt_count: 4,
                target_phase: PipelinePhase::Development,
            }),
        );

        assert_eq!(
            new_state.recovery_epoch, 0,
            "Level 2 recovery must not change recovery_epoch"
        );
    }

    #[test]
    fn level_3_recovery_increments_recovery_epoch_by_1() {
        let mut state = PipelineState::initial(2, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.recovery_epoch = 0;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 3,
                attempt_count: 7,
                target_phase: PipelinePhase::Planning,
            }),
        );

        assert_eq!(
            new_state.recovery_epoch, 1,
            "Level 3 recovery must increment recovery_epoch by 1"
        );
    }

    #[test]
    fn level_4_recovery_increments_recovery_epoch_by_1() {
        let mut state = PipelineState::initial(3, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.recovery_epoch = 0;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 4,
                attempt_count: 10,
                target_phase: PipelinePhase::Planning,
            }),
        );

        assert_eq!(
            new_state.recovery_epoch, 1,
            "Level 4 recovery must increment recovery_epoch by 1"
        );
    }

    #[test]
    fn sequential_level_3_recoveries_each_increment_epoch() {
        let mut state = PipelineState::initial(3, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.recovery_epoch = 0;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        // First level-3 recovery: epoch 0 → 1
        let state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 3,
                attempt_count: 7,
                target_phase: PipelinePhase::Planning,
            }),
        );
        assert_eq!(state.recovery_epoch, 1);

        // Simulate returning to AwaitingDevFix for a second recovery
        let mut state2 = state;
        state2.phase = PipelinePhase::AwaitingDevFix;
        state2.failed_phase_for_recovery = Some(PipelinePhase::Development);

        // Second level-3 recovery: epoch 1 → 2
        let state2 = reduce(
            state2,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 3,
                attempt_count: 7,
                target_phase: PipelinePhase::Planning,
            }),
        );
        assert_eq!(
            state2.recovery_epoch, 2,
            "Sequential level-3 recoveries must each increment epoch"
        );
    }

    #[test]
    fn level_2_planning_phase_start_recovery_resets_context_and_gitignore_prereqs() {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;

        // Simulate having already satisfied global Planning prerequisites.
        state.context_cleaned = true;
        state.gitignore_entries_ensured = true;

        let new_state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 2,
                attempt_count: 4,
                target_phase: PipelinePhase::Planning,
            }),
        );

        assert_eq!(new_state.phase, PipelinePhase::Planning);
        assert!(
            !new_state.context_cleaned,
            "Level 2 Planning recovery should re-run CleanupContext"
        );
        assert!(
            !new_state.gitignore_entries_ensured,
            "Level 2 Planning recovery should re-run EnsureGitignoreEntries"
        );
    }
}
