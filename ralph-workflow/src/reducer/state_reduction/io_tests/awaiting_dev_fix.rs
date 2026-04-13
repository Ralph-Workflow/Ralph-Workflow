//! Unit tests for `AwaitingDevFix` recovery escalation logic.

use crate::agents::{AgentDrain, AgentRole};
use crate::reducer::event::{AwaitingDevFixEvent, PipelineEvent, PipelinePhase};
use crate::reducer::state::PipelineState;
use crate::reducer::state_reduction::reduce;

#[test]
fn test_dev_fix_completed_increments_attempt_count() {
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::AwaitingDevFix;
    state.failed_phase_for_recovery = Some(PipelinePhase::Development);

    let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
        success: true,
        summary: Some("Fixed".to_string()),
    });

    let new_state = reduce(state, event);
    assert_eq!(new_state.dev_fix_attempt_count, 1);
}

#[test]
fn test_recovery_escalation_level_1_for_attempts_1_to_3() {
    for attempt in 1..=3 {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_attempt_count = attempt - 1;

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: false,
            summary: None,
        });

        let new_state = reduce(state, event);
        assert_eq!(
            new_state.recovery_escalation_level, 1,
            "Attempt {attempt} should be at level 1"
        );
    }
}

#[test]
fn test_recovery_escalation_level_2_for_attempts_4_to_6() {
    for attempt in 4..=6 {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_attempt_count = attempt - 1;

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: false,
            summary: None,
        });

        let new_state = reduce(state, event);
        assert_eq!(
            new_state.recovery_escalation_level, 2,
            "Attempt {attempt} should be at level 2"
        );
    }
}

#[test]
fn test_recovery_escalation_level_3_for_attempts_7_to_9() {
    for attempt in 7..=9 {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_attempt_count = attempt - 1;

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: false,
            summary: None,
        });

        let new_state = reduce(state, event);
        assert_eq!(
            new_state.recovery_escalation_level, 3,
            "Attempt {attempt} should be at level 3"
        );
    }
}

#[test]
fn test_recovery_escalation_level_4_for_attempts_10_plus() {
    for attempt in 10..=12 {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);
        state.dev_fix_attempt_count = attempt - 1;

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
            success: false,
            summary: None,
        });

        let new_state = reduce(state, event);
        assert_eq!(
            new_state.recovery_escalation_level, 4,
            "Attempt {attempt} should be at level 4"
        );
    }
}

#[test]
fn test_recovery_exhaustion_does_not_directly_interrupt() {
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::AwaitingDevFix;
    state.failed_phase_for_recovery = Some(PipelinePhase::Development);
    state.dev_fix_attempt_count = 12;

    let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted {
        success: false,
        summary: None,
    });

    let new_state = reduce(state, event);
    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    assert_eq!(new_state.dev_fix_attempt_count, 13);
}

#[test]
fn test_recovery_attempted_transitions_to_failed_phase() {
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::AwaitingDevFix;
    state.failed_phase_for_recovery = Some(PipelinePhase::Review);
    state.recovery_escalation_level = 2;
    state.agent_chain = state
        .agent_chain
        .with_agents(
            vec!["reviewer-a".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        )
        .with_drain(AgentDrain::Fix);
    state.agent_chain.current_agent_index = 1;
    state.agent_chain.retry_cycle = 2;

    let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
        level: 2,
        attempt_count: 4,
        target_phase: PipelinePhase::Review,
    });

    let new_state = reduce(state, event);
    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(
        new_state.previous_phase,
        Some(PipelinePhase::AwaitingDevFix)
    );
    assert_eq!(new_state.agent_chain.current_drain, AgentDrain::Fix);
    assert_eq!(new_state.agent_chain.current_role, AgentRole::Fix);
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.retry_cycle, 0);
}

#[test]
fn test_recovery_succeeded_clears_recovery_state() {
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::Development;
    state.dev_fix_attempt_count = 5;
    state.recovery_escalation_level = 2;
    state.failed_phase_for_recovery = Some(PipelinePhase::Development);

    let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoverySucceeded {
        level: 2,
        total_attempts: 5,
    });

    let new_state = reduce(state, event);
    assert_eq!(new_state.dev_fix_attempt_count, 0);
    assert_eq!(new_state.recovery_escalation_level, 0);
    assert_eq!(new_state.failed_phase_for_recovery, None);
}
