//! End-to-end style reducer/orchestration tests for the recovery loop.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::determine_next_effect;
use ralph_workflow::reducer::effect::{Effect, RecoveryResetType};
use ralph_workflow::reducer::event::{AwaitingDevFixEvent, PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::state_reduction::reduce;

/// End-to-end test: failure -> dev-fix -> recovery attempted -> recovery succeeds.
#[test]
fn end_to_end_recovery_success() {
    use ralph_workflow::reducer::event::{ErrorEvent, PromptInputEvent};

    with_default_timeout(|| {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Development;

        let error_event = PipelineEvent::PromptInput(PromptInputEvent::HandlerError {
            phase: PipelinePhase::Development,
            error: ErrorEvent::AgentChainExhausted {
                role: AgentRole::Developer,
                phase: PipelinePhase::Development,
                cycle: 3,
            },
        });
        let mut state = reduce(state, error_event);
        assert_eq!(state.phase, PipelinePhase::AwaitingDevFix);

        state = with_locked_prompt_permissions(state);
        let effect = determine_next_effect(&state);
        assert!(matches!(effect, Effect::TriggerDevFixFlow { .. }));

        let dev_fix_event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: true,
            summary: Some("Fixed the issue".to_string()),
        });
        state = reduce(state, dev_fix_event);
        state.dev_fix_triggered = true;

        assert_eq!(state.phase, PipelinePhase::AwaitingDevFix);
        assert_eq!(state.recovery_escalation_level, 1);

        let effect = determine_next_effect(&state);
        assert!(matches!(effect, Effect::AttemptRecovery { level: 1, .. }));

        let recovery_event =
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 1,
                attempt_count: 1,
                target_phase: PipelinePhase::Development,
            });
        state = reduce(state, recovery_event);
        assert_eq!(state.phase, PipelinePhase::Development);

        let success_event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoverySucceeded {
            level: 1,
            total_attempts: 1,
        });
        state = reduce(state, success_event);

        assert_eq!(state.dev_fix_attempt_count, 0);
        assert_eq!(state.recovery_escalation_level, 0);
        assert_eq!(state.failed_phase_for_recovery, None);
        assert_eq!(state.phase, PipelinePhase::Development);
    });
}

/// End-to-end test: multiple attempts escalate and remain non-terminating until exhaustion.
#[test]
fn end_to_end_escalation_progresses_levels() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_triggered = false;

        for attempt in 1..=12 {
            state.dev_fix_triggered = false;
            let effect = determine_next_effect(&state);
            assert!(
                matches!(effect, Effect::TriggerDevFixFlow { .. })
                    || matches!(effect, Effect::AttemptRecovery { .. })
                    || matches!(effect, Effect::EmitRecoveryReset { .. }),
                "Attempt {attempt}: unexpected effect {effect:?}"
            );

            state.dev_fix_triggered = true;
            let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                success: false,
                summary: None,
            });
            state = reduce(state, event);

            let expected_level = match attempt {
                1..=3 => 1,
                4..=6 => 2,
                7..=9 => 3,
                _ => 4,
            };
            assert_eq!(state.recovery_escalation_level, expected_level);
        }

        assert_eq!(state.phase, PipelinePhase::AwaitingDevFix);
        assert_eq!(state.dev_fix_attempt_count, 12);
        assert_eq!(state.recovery_escalation_level, 4);

        let effect = determine_next_effect(&state);
        assert!(matches!(
            effect,
            Effect::EmitRecoveryReset {
                reset_type: RecoveryResetType::CompleteReset,
                ..
            }
        ));
    });
}

#[test]
fn end_to_end_recovery_loop_with_multiple_attempts() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(3, 0);
        state.phase = PipelinePhase::Development;
        state.iteration = 1;
        state.development_agent_invoked_iteration = Some(1);
        state.development_xml_extracted_iteration = Some(1);

        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_attempt_count = 0;
        state.recovery_escalation_level = 0;

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: true,
            summary: Some("Fixed import path".to_string()),
        });
        state = reduce(state, event);
        assert_eq!(state.dev_fix_attempt_count, 1);
        assert_eq!(state.recovery_escalation_level, 1);

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
            level: 1,
            attempt_count: 1,
            target_phase: PipelinePhase::Development,
        });
        state = reduce(state, event);
        assert_eq!(state.phase, PipelinePhase::Development);
        assert_eq!(state.development_agent_invoked_iteration, Some(1));

        state.phase = PipelinePhase::AwaitingDevFix;

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: true,
            summary: Some("Fixed permission issue".to_string()),
        });
        state = reduce(state, event);
        assert_eq!(state.dev_fix_attempt_count, 2);

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
            level: 1,
            attempt_count: 2,
            target_phase: PipelinePhase::Development,
        });
        state = reduce(state, event);
        assert_eq!(state.phase, PipelinePhase::Development);

        state.phase = PipelinePhase::AwaitingDevFix;

        for i in 3..=4 {
            let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                success: true,
                summary: Some(format!("Fix attempt {i}")),
            });
            state = reduce(state, event);
        }
        assert_eq!(state.dev_fix_attempt_count, 4);
        assert_eq!(state.recovery_escalation_level, 2);

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
            level: 2,
            attempt_count: 4,
            target_phase: PipelinePhase::Development,
        });
        state = reduce(state, event);
        assert_eq!(state.phase, PipelinePhase::Development);
        assert_eq!(state.development_agent_invoked_iteration, None);
        assert_eq!(state.development_xml_extracted_iteration, None);
        assert_eq!(state.iteration, 1);
        assert_eq!(state.dev_fix_attempt_count, 4);
        assert_eq!(state.recovery_escalation_level, 2);
        assert_eq!(
            state.failed_phase_for_recovery,
            Some(PipelinePhase::Development)
        );
    });
}

#[test]
fn sequential_failures_escalate_deterministically_across_recovery_boundaries() {
    use ralph_workflow::reducer::event::{ErrorEvent, PromptInputEvent};

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(5, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Planning);
        state.iteration = 3;

        for attempt in 1..=3 {
            state = reduce(
                state,
                PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixTriggered {
                    failed_phase: PipelinePhase::Planning,
                    failed_role: AgentRole::Developer,
                }),
            );
            state = reduce(
                state,
                PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                    success: false,
                    summary: Some(format!("attempt {attempt}")),
                }),
            );

            assert_eq!(state.dev_fix_attempt_count, attempt);
            assert_eq!(state.recovery_escalation_level, 1);
            assert_eq!(state.recovery_epoch, 0);
        }

        state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 1,
                attempt_count: 3,
                target_phase: PipelinePhase::Planning,
            }),
        );
        assert_eq!(state.phase, PipelinePhase::Planning);
        assert_eq!(state.recovery_epoch, 0);

        state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::HandlerError {
                phase: PipelinePhase::Planning,
                error: ErrorEvent::AgentChainExhausted {
                    role: AgentRole::Developer,
                    phase: PipelinePhase::Planning,
                    cycle: 1,
                },
            }),
        );
        assert_eq!(state.phase, PipelinePhase::AwaitingDevFix);
        assert_eq!(state.dev_fix_attempt_count, 3);
        assert_eq!(state.recovery_escalation_level, 1);
        assert_eq!(state.recovery_epoch, 0);

        state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixTriggered {
                failed_phase: PipelinePhase::Planning,
                failed_role: AgentRole::Developer,
            }),
        );
        state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                success: false,
                summary: Some("attempt 4".to_string()),
            }),
        );
        assert_eq!(state.dev_fix_attempt_count, 4);
        assert_eq!(state.recovery_escalation_level, 2);
        assert_eq!(state.recovery_epoch, 0);
    });
}

#[test]
fn sequential_level_3_and_4_recovery_increments_epoch_without_counter_corruption() {
    use ralph_workflow::reducer::event::{ErrorEvent, PromptInputEvent};

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(5, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Planning);
        state.iteration = 3;
        state.dev_fix_attempt_count = 6;
        state.recovery_escalation_level = 2;
        state.recovery_epoch = 10;

        state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixTriggered {
                failed_phase: PipelinePhase::Planning,
                failed_role: AgentRole::Developer,
            }),
        );
        state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                success: false,
                summary: Some("attempt 7".to_string()),
            }),
        );
        assert_eq!(state.dev_fix_attempt_count, 7);
        assert_eq!(state.recovery_escalation_level, 3);
        assert_eq!(state.recovery_epoch, 10);

        state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 3,
                attempt_count: 7,
                target_phase: PipelinePhase::Planning,
            }),
        );
        assert_eq!(state.phase, PipelinePhase::Planning);
        assert_eq!(state.iteration, 2);
        assert_eq!(state.dev_fix_attempt_count, 7);
        assert_eq!(state.recovery_escalation_level, 3);
        assert_eq!(state.recovery_epoch, 11);

        state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::HandlerError {
                phase: PipelinePhase::Planning,
                error: ErrorEvent::AgentChainExhausted {
                    role: AgentRole::Developer,
                    phase: PipelinePhase::Planning,
                    cycle: 2,
                },
            }),
        );
        assert_eq!(state.phase, PipelinePhase::AwaitingDevFix);
        assert_eq!(state.dev_fix_attempt_count, 7);
        assert_eq!(state.recovery_escalation_level, 3);
        assert_eq!(state.recovery_epoch, 11);

        for expected_attempt in 8..=10 {
            state = reduce(
                state,
                PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixTriggered {
                    failed_phase: PipelinePhase::Planning,
                    failed_role: AgentRole::Developer,
                }),
            );
            state = reduce(
                state,
                PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                    success: false,
                    summary: Some(format!("attempt {expected_attempt}")),
                }),
            );
            assert_eq!(state.dev_fix_attempt_count, expected_attempt);
            let expected_level = if expected_attempt <= 9 { 3 } else { 4 };
            assert_eq!(state.recovery_escalation_level, expected_level);
            assert_eq!(state.recovery_epoch, 11);
        }

        state = reduce(
            state,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 4,
                attempt_count: 10,
                target_phase: PipelinePhase::Planning,
            }),
        );

        assert_eq!(state.phase, PipelinePhase::Planning);
        assert_eq!(state.iteration, 0);
        assert_eq!(state.dev_fix_attempt_count, 10);
        assert_eq!(state.recovery_escalation_level, 4);
        assert_eq!(state.recovery_epoch, 12);
    });
}
