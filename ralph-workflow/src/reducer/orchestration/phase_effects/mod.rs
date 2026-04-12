//! Phase-specific effect orchestration.
//!
//! This module contains pure orchestration logic for determining the next effect
//! based on the current pipeline state. All functions are deterministic and perform
//! no I/O operations.
//!
//! # Architecture
//!
//! Each phase module implements a `determine_*_effect()` function that:
//! - Takes `&PipelineState` as input
//! - Returns an `Effect` to execute next
//! - Performs NO I/O or side effects
//! - Is purely deterministic
//!
//! # Priority Order
//!
//! The main `determine_next_effect_for_phase()` function is called by the
//! higher-level orchestration layer in `xsd_retry.rs`, which handles:
//!
//! 1. **Continuation cleanup** - Write pending continuation context
//! 2. **Retry logic** - Same-agent retry after timeout/failure
//! 3. **XSD retry** - Re-invoke agent after XSD validation failure
//! 4. **Continuation** - Re-invoke agent with continuation prompt
//! 5. **Normal progression** - Call phase-specific orchestration (this module)
//!
//! # Phase Modules
//!
//! - `planning` - Planning phase orchestration
//! - `development` - Development phase orchestration (including Analysis agent)
//! - `review` - Review phase orchestration (including Fix agent)
//! - `commit` - Commit phase orchestration
//!
//! # Special Cases
//!
//! - `FinalValidation` phase → `CheckUncommittedChangesBeforeTermination` (safety check), then `ValidateFinalState`
//! - Finalizing phase → `RestorePromptPermissions` effect
//! - `AwaitingDevFix` phase → `TriggerDevFixFlow` effect
//! - Complete/Interrupted phase → `CheckUncommittedChangesBeforeTermination` (safety check), then `SaveCheckpoint`
//!
//! ## Pre-Termination Safety Check
//!
//! Before any pipeline termination (Complete, Interrupted, or after `FinalValidation`),
//! the orchestration derives a `CheckUncommittedChangesBeforeTermination` effect to
//! ensure no work is lost:
//!
//! - If uncommitted changes exist → route to `CommitMessage` phase
//! - If working directory is clean → emit `PreTerminationSafetyCheckPassed` and proceed
//! - If git snapshot fails → route to `AwaitingDevFix` for recovery
//!
//! **THE ONLY EXCEPTION:** User-initiated Ctrl+C (`interrupted_by_user=true`) skips
//! this check because the user explicitly chose to interrupt. All other termination
//! paths (`AwaitingDevFix` exhaustion, completion marker emission, etc.) MUST commit
//! uncommitted work before terminating.

mod commit;
mod development;
mod planning;
mod review;

use crate::reducer::effect::Effect;
use crate::reducer::event::PipelinePhase;
use crate::reducer::state::PipelineState;

pub(super) fn determine_next_effect_for_phase(state: &PipelineState) -> Effect {
    match state.phase {
        PipelinePhase::Planning => planning::determine_planning_effect(state),
        PipelinePhase::Development => development::determine_development_effect(state),
        PipelinePhase::Review => review::determine_review_effect(state),
        PipelinePhase::CommitMessage => commit::determine_commit_effect(state),
        PipelinePhase::FinalValidation => {
            // SAFETY CHECK: Ensure no uncommitted work before finalization
            // This check happens before FinalStateValidationCompleted, ensuring all work is committed
            // before the pipeline enters its terminal sequence (Finalizing -> Complete)
            if !state.pre_termination_commit_checked {
                return Effect::CheckUncommittedChangesBeforeTermination;
            }

            Effect::ValidateFinalState
        }
        PipelinePhase::Finalizing => Effect::RestorePromptPermissions,
        PipelinePhase::AwaitingDevFix => {
            // Completion marker emission must preempt recovery-loop effects.
            // If the marker write failed, we must keep retrying deterministically until
            // the marker is successfully written (CompletionMarkerEmitted).
            if state.completion_marker_pending {
                // Completion marker emission is NOT an exception to the pre-termination
                // commit safety check. The ONLY exception is user-initiated Ctrl+C.
                if !state.interrupted_by_user && !state.pre_termination_commit_checked {
                    return Effect::CheckUncommittedChangesBeforeTermination;
                }

                return Effect::EmitCompletionMarkerAndTerminate {
                    is_failure: state.completion_marker_is_failure,
                    reason: state.completion_marker_reason.clone(),
                };
            }

            // If dev-fix already triggered and recovery state is set, attempt recovery
            if state.dev_fix_triggered && state.recovery_escalation_level > 0 {
                // Derive the appropriate recovery effect based on escalation level
                if state.recovery_escalation_level == 1 {
                    // Level 1: Simple retry - emit RecoveryAttempted to transition back
                    Effect::AttemptRecovery {
                        level: state.recovery_escalation_level,
                        attempt_count: state.dev_fix_attempt_count,
                    }
                } else {
                    // Level 2+: Requires state reset - use EmitRecoveryReset
                    use crate::reducer::effect::RecoveryResetType;
                    let (reset_type, target_phase) = match state.recovery_escalation_level {
                        2 => (
                            RecoveryResetType::PhaseStart,
                            state
                                .failed_phase_for_recovery
                                .unwrap_or(PipelinePhase::Development),
                        ),
                        3 => (RecoveryResetType::IterationReset, PipelinePhase::Planning),
                        _ => (RecoveryResetType::CompleteReset, PipelinePhase::Planning),
                    };
                    Effect::EmitRecoveryReset {
                        reset_type,
                        target_phase,
                    }
                }
            } else {
                // First time in AwaitingDevFix or dev-fix not yet triggered
                let failed_phase = state
                    .failed_phase_for_recovery
                    .or(state.previous_phase)
                    .unwrap_or(PipelinePhase::Development);
                let failed_phase = if failed_phase == PipelinePhase::AwaitingDevFix {
                    PipelinePhase::Development
                } else {
                    failed_phase
                };
                Effect::TriggerDevFixFlow {
                    failed_phase,
                    failed_role: state.agent_chain.current_drain.role(),
                    retry_cycle: state.agent_chain.retry_cycle,
                }
            }
        }
        PipelinePhase::Complete | PipelinePhase::Interrupted => {
            use crate::reducer::event::CheckpointTrigger;

            // EXCEPTION: User-initiated Ctrl+C (interrupted_by_user=true) skips safety check.
            //
            // IMPORTANT: This exception applies ONLY to `phase == Interrupted`.
            // If a checkpoint is resumed with `phase == Complete` but `interrupted_by_user == true`
            // (e.g., a mis-set checkpoint), we must still run the pre-termination safety check.
            if state.phase == PipelinePhase::Interrupted && state.interrupted_by_user {
                // On Interrupted, ALWAYS attempt PROMPT.md restoration before checkpoint.
                // We do NOT gate on restore_needed because:
                // 1. A prior crashed run (SIGKILL) may have left PROMPT.md read-only
                // 2. This run was interrupted early before LockPromptPermissions executed
                // 3. restore_needed is false, but PROMPT.md may still need restoration
                // The restoration handler is idempotent - calling it on already-writable
                // PROMPT.md is a no-op.
                if !state.prompt_permissions.restored {
                    return Effect::RestorePromptPermissions;
                }

                return Effect::SaveCheckpoint {
                    trigger: CheckpointTrigger::Interrupt,
                };
            }

            // SAFETY CHECK: Ensure no uncommitted work before termination
            // This applies to ALL other termination paths:
            // - AwaitingDevFix exhaustion → Interrupted
            // - Completion marker emission → Interrupted
            // - Normal completion → Complete
            if !state.pre_termination_commit_checked {
                return Effect::CheckUncommittedChangesBeforeTermination;
            }

            // Safety check passed - proceed with normal termination
            // On Interrupted phase, always attempt PROMPT.md restoration (same reasoning
            // as user-initiated path: prior crashed runs may have left it read-only).
            if state.phase == PipelinePhase::Interrupted && !state.prompt_permissions.restored {
                return Effect::RestorePromptPermissions;
            }

            // Programmatic termination (Complete or non-user Interrupted) should NOT be
            // classified as an interrupt. Reserve CheckpointTrigger::Interrupt for true
            // Ctrl+C interruptions (interrupted_by_user=true path above).
            Effect::SaveCheckpoint {
                trigger: CheckpointTrigger::PhaseTransition,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::AgentRole;
    use crate::reducer::state::{AgentChainState, PromptPermissionsState};

    #[test]
    fn trigger_dev_fix_flow_prefers_failed_phase_for_recovery_over_previous_phase() {
        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::AwaitingDevFix,
            previous_phase: Some(PipelinePhase::AwaitingDevFix),
            failed_phase_for_recovery: Some(PipelinePhase::CommitMessage),
            dev_fix_triggered: false,
            recovery_escalation_level: 0,
            agent_chain: AgentChainState {
                current_role: AgentRole::Developer,
                retry_cycle: 7,
                ..AgentChainState::default()
            },
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        match effect {
            Effect::TriggerDevFixFlow {
                failed_phase,
                failed_role,
                retry_cycle,
            } => {
                assert_eq!(failed_phase, PipelinePhase::CommitMessage);
                assert_eq!(failed_role, AgentRole::Developer);
                assert_eq!(retry_cycle, 7);
            }
            other => panic!("expected TriggerDevFixFlow, got: {other:?}"),
        }
    }

    #[test]
    fn trigger_dev_fix_flow_never_reports_awaiting_dev_fix_as_failed_phase() {
        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::AwaitingDevFix,
            previous_phase: Some(PipelinePhase::AwaitingDevFix),
            failed_phase_for_recovery: None,
            dev_fix_triggered: false,
            recovery_escalation_level: 0,
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        match effect {
            Effect::TriggerDevFixFlow { failed_phase, .. } => {
                assert_ne!(failed_phase, PipelinePhase::AwaitingDevFix);
            }
            other => panic!("expected TriggerDevFixFlow, got: {other:?}"),
        }
    }

    #[test]
    fn awaiting_dev_fix_completion_marker_pending_requires_safety_check_first() {
        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::AwaitingDevFix,
            completion_marker_pending: true,
            completion_marker_is_failure: true,
            completion_marker_reason: Some("safety_valve".to_string()),
            interrupted_by_user: false,
            pre_termination_commit_checked: false,
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        assert!(
            matches!(effect, Effect::CheckUncommittedChangesBeforeTermination),
            "expected safety check to preempt completion marker emission, got: {effect:?}"
        );
    }

    #[test]
    fn awaiting_dev_fix_completion_marker_pending_emits_after_safety_check() {
        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::AwaitingDevFix,
            completion_marker_pending: true,
            completion_marker_is_failure: true,
            completion_marker_reason: Some("safety_valve".to_string()),
            interrupted_by_user: false,
            pre_termination_commit_checked: true,
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        assert!(
            matches!(
                effect,
                Effect::EmitCompletionMarkerAndTerminate {
                    is_failure: true,
                    ref reason
                } if reason.as_deref() == Some("safety_valve")
            ),
            "expected EmitCompletionMarkerAndTerminate after safety check, got: {effect:?}"
        );
    }

    // Dev-fix agent selection is enforced by the TriggerDevFixFlow handler.
    // Orchestration intentionally preserves the original failed role in the effect params.

    #[test]
    fn user_interrupt_skips_pre_termination_safety_check() {
        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::Interrupted,
            interrupted_by_user: true,
            pre_termination_commit_checked: false,
            prompt_permissions: PromptPermissionsState {
                restored: true,
                ..Default::default()
            },
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        // Should skip safety check and go directly to SaveCheckpoint
        match effect {
            Effect::SaveCheckpoint { trigger } => {
                assert_eq!(trigger, crate::reducer::event::CheckpointTrigger::Interrupt);
            }
            other => panic!(
                "Expected SaveCheckpoint effect when interrupted_by_user=true, got {other:?}. \
                 User interrupt should skip pre-termination safety check."
            ),
        }
    }

    #[test]
    fn programmatic_interrupt_requires_pre_termination_safety_check() {
        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::Interrupted,
            interrupted_by_user: false,
            pre_termination_commit_checked: false,
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        // Should derive safety check effect BEFORE proceeding to termination
        match effect {
            Effect::CheckUncommittedChangesBeforeTermination => {
                // Expected - safety check required
            }
            other => panic!(
                "Expected CheckUncommittedChangesBeforeTermination when interrupted_by_user=false, got {other:?}. \
                 Programmatic interrupts must commit uncommitted work before terminating."
            ),
        }
    }

    #[test]
    fn complete_phase_requires_pre_termination_safety_check() {
        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::Complete,
            interrupted_by_user: false,
            pre_termination_commit_checked: false,
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        // Should derive safety check before completion
        match effect {
            Effect::CheckUncommittedChangesBeforeTermination => {
                // Expected - safety check required
            }
            other => panic!(
                "Expected CheckUncommittedChangesBeforeTermination before Complete, got {other:?}"
            ),
        }
    }

    #[test]
    fn final_validation_requires_pre_termination_safety_check() {
        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::FinalValidation,
            pre_termination_commit_checked: false,
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        // Should derive safety check before final validation
        match effect {
            Effect::CheckUncommittedChangesBeforeTermination => {
                // Expected - safety check required
            }
            other => panic!(
                "Expected CheckUncommittedChangesBeforeTermination before FinalValidation, got {other:?}"
            ),
        }
    }

    #[test]
    fn safety_check_allows_proceed_after_checked() {
        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::Interrupted,
            interrupted_by_user: false,
            pre_termination_commit_checked: true,
            prompt_permissions: PromptPermissionsState {
                restored: true,
                ..Default::default()
            },
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        // Should proceed to checkpoint save
        match effect {
            Effect::SaveCheckpoint { .. } => {
                // Expected - proceed after safety check and restoration
            }
            other => panic!(
                "Expected SaveCheckpoint after safety check and restoration complete, got {other:?}"
            ),
        }
    }

    #[test]
    fn complete_saves_checkpoint_with_phase_transition_trigger_after_safety_check() {
        use crate::reducer::event::CheckpointTrigger;

        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::Complete,
            pre_termination_commit_checked: true,
            interrupted_by_user: false,
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        match effect {
            Effect::SaveCheckpoint { trigger } => {
                assert_eq!(trigger, CheckpointTrigger::PhaseTransition);
            }
            other => panic!("expected SaveCheckpoint, got: {other:?}"),
        }
    }

    #[test]
    fn programmatic_interrupt_saves_checkpoint_with_phase_transition_trigger_after_safety_check() {
        use crate::reducer::event::CheckpointTrigger;

        let state = PipelineState::initial(1, 0);
        let state = PipelineState {
            phase: PipelinePhase::Interrupted,
            pre_termination_commit_checked: true,
            interrupted_by_user: false,
            prompt_permissions: PromptPermissionsState {
                restored: true,
                ..Default::default()
            },
            ..state
        };

        let effect = determine_next_effect_for_phase(&state);

        match effect {
            Effect::SaveCheckpoint { trigger } => {
                assert_eq!(trigger, CheckpointTrigger::PhaseTransition);
            }
            other => panic!("expected SaveCheckpoint, got: {other:?}"),
        }
    }
}
