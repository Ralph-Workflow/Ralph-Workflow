//! Orchestration-focused tests for recovery levels.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::determine_next_effect;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{AwaitingDevFixEvent, PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::state_reduction::reduce;

/// Test that recovery Level 1 (retry same operation) works correctly.
#[test]
fn recovery_level_1_retry_same_operation() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_attempt_count = 0;

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: true,
            summary: Some("Fixed".to_string()),
        });
        let state = reduce(state, event);

        assert_eq!(state.recovery_escalation_level, 1);
        assert_eq!(state.dev_fix_attempt_count, 1);
        assert_eq!(state.phase, PipelinePhase::AwaitingDevFix);

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
            level: 1,
            attempt_count: 1,
            target_phase: PipelinePhase::Development,
        });
        let state = reduce(state, event);
        assert_eq!(state.phase, PipelinePhase::Development);

        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Development,
                    ..
                } | Effect::PrepareDevelopmentContext { .. }
            ),
            "Level 1 recovery should retry same operation, got {effect:?}"
        );
    });
}

/// Test that recovery escalates to Level 2 after 3 failed Level 1 attempts.
#[test]
fn recovery_escalation_to_level_2() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        for i in 1..=3 {
            let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                success: false,
                summary: None,
            });
            state = reduce(state, event);
            assert_eq!(state.recovery_escalation_level, 1);
            assert_eq!(state.dev_fix_attempt_count, i);
        }

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: false,
            summary: None,
        });
        state = reduce(state, event);

        assert_eq!(state.recovery_escalation_level, 2);
        assert_eq!(state.dev_fix_attempt_count, 4);
    });
}

/// Recovery must NOT derive termination due to internal attempt counts.
#[test]
fn recovery_does_not_derive_termination_effect_after_many_attempts() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_triggered = true;

        for i in 1..=20 {
            let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                success: false,
                summary: None,
            });
            state = reduce(state, event);
            assert_eq!(state.phase, PipelinePhase::AwaitingDevFix, "attempt {i}");

            let effect = determine_next_effect(&state);
            assert!(
                !matches!(effect, Effect::EmitCompletionMarkerAndTerminate { .. }),
                "attempt {i} should not derive termination effect, got {effect:?}"
            );
        }
    });
}

/// Regression: `DevFixCompleted` must not terminate immediately.
#[test]
fn dev_fix_completed_does_not_terminate() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_attempt_count = 0;

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: true,
            summary: Some("Fixed the issue".to_string()),
        });
        let state = reduce(state, event);

        assert_eq!(
            state.phase,
            PipelinePhase::AwaitingDevFix,
            "DevFixCompleted should not terminate"
        );
        assert_eq!(state.recovery_escalation_level, 1);
        assert_eq!(state.dev_fix_attempt_count, 1);
    });
}

/// Regression: recovery attempts never derive termination.
#[test]
fn recovery_never_derives_termination_effect() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_triggered = true;

        for i in 1..=30 {
            let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
                success: false,
                summary: None,
            });
            state = reduce(state, event);

            assert_eq!(state.phase, PipelinePhase::AwaitingDevFix, "Attempt {i}");
            let effect = determine_next_effect(&state);
            assert!(
                !matches!(effect, Effect::EmitCompletionMarkerAndTerminate { .. }),
                "Attempt {i} should not derive termination effect, got {effect:?}"
            );
        }
    });
}

/// Regression: preserve attempt count when failing again after recovery attempt.
#[test]
fn preserves_attempt_count_on_repeated_failure() {
    use ralph_workflow::reducer::event::{ErrorEvent, PromptInputEvent};

    with_default_timeout(|| {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Development;
        state.previous_phase = Some(PipelinePhase::AwaitingDevFix);
        state.dev_fix_attempt_count = 1;
        state.recovery_escalation_level = 1;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        let error_event = PipelineEvent::PromptInput(PromptInputEvent::HandlerError {
            phase: PipelinePhase::Development,
            error: ErrorEvent::AgentChainExhausted {
                role: AgentRole::Developer,
                phase: PipelinePhase::Development,
                cycle: 3,
            },
        });
        state = reduce(state, error_event);

        assert_eq!(state.phase, PipelinePhase::AwaitingDevFix);
        assert_eq!(state.dev_fix_attempt_count, 1);
        assert_eq!(state.recovery_escalation_level, 1);
    });
}

/// Orchestration does not emit completion marker based on attempts.
#[test]
fn orchestration_does_not_emit_completion_based_on_attempts() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.dev_fix_triggered = true;
        state.dev_fix_attempt_count = 13;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        let effect = determine_next_effect(&state);
        assert!(
            !matches!(effect, Effect::EmitCompletionMarkerAndTerminate { .. }),
            "should not emit completion marker based on attempts, got {effect:?}"
        );
    });
}
