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
        AwaitingDevFixEvent::CompletionMarkerEmitted { is_failure } => {
            // Completion marker emitted, transition to Interrupted
            PipelineState {
                phase: PipelinePhase::Interrupted,
                previous_phase: Some(state.phase),
                completion_marker_pending: false,
                completion_marker_is_failure: is_failure,
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
            let level_from_attempt_count = match state.dev_fix_attempt_count {
                0 => level,
                1..=3 => 1,
                4..=6 => 2,
                7..=9 => 3,
                _ => 4,
            };
            let effective_level = level
                .max(state.recovery_escalation_level)
                .max(level_from_attempt_count);

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
            let new_state = PipelineState {
                phase: target_phase,
                previous_phase: Some(PipelinePhase::AwaitingDevFix),
                recovery_escalation_level: effective_level,
                // Keep recovery tracking fields so we can escalate if this fails
                ..state.clone()
            };

            // Apply state reset based on escalation level (functional style)
            let new_state = match effective_level {
                1 => {
                    // Level 1: Simple retry - just transition back, no state reset
                    new_state
                }
                2 => {
                    // Level 2: Reset to phase start - clear phase-specific progress flags
                    let reset = new_state.clear_phase_flags(target_phase);

                    // IMPORTANT: Level 2 is a true "phase start" restart.
                    // Clear continuation/retry flags that have higher orchestration
                    // priority than normal phase sequencing (same-agent retry, XSD retry,
                    // continuation pending, context write/cleanup pending).
                    let reset = PipelineState {
                        continuation: reset.continuation.clone().reset(),
                        ..reset
                    };

                    // Clear phase-scoped materialized prompt inputs so prompt preparation
                    // reruns from scratch for the restarted phase.
                    let reset = PipelineState {
                        prompt_inputs: match target_phase {
                            PipelinePhase::Planning => {
                                reset.prompt_inputs.clone().with_planning_cleared()
                            }
                            PipelinePhase::Development => {
                                reset.prompt_inputs.clone().with_development_cleared()
                            }
                            PipelinePhase::Review => {
                                reset.prompt_inputs.clone().with_review_cleared()
                            }
                            PipelinePhase::CommitMessage => {
                                reset.prompt_inputs.clone().with_commit_cleared()
                            }
                            _ => reset.prompt_inputs.clone(),
                        },
                        ..reset
                    };

                    // Planning phase has global prerequisites at the true phase start.
                    // If we are resetting to Planning phase start, we must re-run these
                    // prerequisite effects; otherwise orchestration will skip them and the
                    // "phase start" reset won't actually restart from the beginning.
                    if matches!(target_phase, PipelinePhase::Planning) {
                        PipelineState {
                            context_cleaned: false,
                            gitignore_entries_ensured: false,
                            ..reset
                        }
                    } else {
                        reset
                    }
                }
                3 => {
                    // Level 3: Reset iteration counter - decrement iteration and restart from Planning.
                    // Advance recovery_epoch so PromptScopeKey replay identity changes with scope.
                    // Clear prompt_history atomically so stale prompts are not replayed after scope rotation.
                    let s = new_state.reset_iteration();
                    PipelineState {
                        recovery_epoch: s.recovery_epoch + 1,
                        prompt_history: std::collections::HashMap::new(),
                        ..s
                    }
                }
                _ => {
                    // Level 4+: Complete reset - reset to iteration 0, restart from Planning.
                    // Advance recovery_epoch so PromptScopeKey replay identity changes with scope.
                    // Clear prompt_history atomically so stale prompts are not replayed after scope rotation.
                    let s = new_state.reset_to_iteration_zero();
                    PipelineState {
                        recovery_epoch: s.recovery_epoch + 1,
                        prompt_history: std::collections::HashMap::new(),
                        ..s
                    }
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
            if effective_level >= 2 || new_state.agent_chain.is_exhausted() {
                let drain = new_state.agent_chain.current_drain;
                PipelineState {
                    agent_chain: new_state.agent_chain.reset_for_drain(drain),
                    ..new_state
                }
            } else {
                new_state
            }
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
#[path = "awaiting_dev_fix/tests.rs"]
mod tests;
