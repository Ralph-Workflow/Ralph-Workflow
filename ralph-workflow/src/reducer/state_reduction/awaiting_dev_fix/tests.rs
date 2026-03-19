use super::*;
use crate::agents::AgentRole;
use crate::reducer::event::{AwaitingDevFixEvent, PipelineEvent, PipelinePhase};
use crate::reducer::reduce;
use crate::reducer::state::{AgentChainState, ContinuationState};

#[test]
fn dev_fix_completed_does_not_directly_interrupt_when_attempts_exhausted() {
    let state = PipelineState::initial(1, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        dev_fix_attempt_count: 12,
        recovery_escalation_level: 4,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state
    };

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
    let state = PipelineState::initial(1, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        recovery_escalation_level: 2,
        dev_fix_attempt_count: 4,
        ..state
    };

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
    let state = PipelineState::initial(1, 0);
    let chain = AgentChainState::initial()
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

    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        agent_chain: chain,
        ..state
    };

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
    let state = PipelineState::initial(1, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        dev_fix_triggered: false,
        dev_fix_attempt_count: 0,
        recovery_escalation_level: 0,
        failed_phase_for_recovery: Some(PipelinePhase::CommitMessage),
        ..state
    };

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
    let state = PipelineState::initial(1, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        continuation: ContinuationState {
            xsd_retry_pending: true,
            xsd_retry_session_reuse_pending: true,
            same_agent_retry_pending: true,
            same_agent_retry_reason: Some(crate::reducer::state::SameAgentRetryReason::Timeout),
            continue_pending: true,
            fix_continue_pending: true,
            context_write_pending: true,
            context_cleanup_pending: true,
            ..Default::default()
        },
        ..state
    };

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
    let state = PipelineState::initial(1, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        completion_marker_pending: false,
        ..state
    };

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

#[test]
fn completion_marker_emitted_records_is_failure_and_clears_pending_state() {
    let state = PipelineState::initial(1, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        completion_marker_pending: true,
        completion_marker_is_failure: true,
        completion_marker_reason: Some("previous error".to_string()),
        ..state
    };

    let new_state = reduce(
        state,
        PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::CompletionMarkerEmitted {
            is_failure: false,
        }),
    );

    assert_eq!(new_state.phase, PipelinePhase::Interrupted);
    assert!(!new_state.completion_marker_pending);
    assert!(!new_state.completion_marker_is_failure);
    assert!(new_state.completion_marker_reason.is_none());
}

// =========================================================================
// recovery_epoch tests (RFC-007 corrective action #2)
// =========================================================================

#[test]
fn level_1_recovery_does_not_change_recovery_epoch() {
    let state = PipelineState::initial(1, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        recovery_epoch: 0,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state
    };

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
    let state = PipelineState::initial(1, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        recovery_epoch: 0,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state
    };

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
    let state = PipelineState::initial(2, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        recovery_epoch: 0,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state
    };

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
    let state = PipelineState::initial(3, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        recovery_epoch: 0,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state
    };

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
    let state = PipelineState::initial(3, 0);
    let state1 = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        recovery_epoch: 0,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state
    };

    let state1 = reduce(
        state1,
        PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
            level: 3,
            attempt_count: 7,
            target_phase: PipelinePhase::Planning,
        }),
    );
    assert_eq!(state1.recovery_epoch, 1);

    let state2 = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state1
    };

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
fn level_3_recovery_clears_prompt_history() {
    let state = PipelineState::initial(2, 0);
    let s = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        recovery_epoch: 0,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state
    };
    s.prompt_history.insert(
        "planning_iter1_normal".to_string(),
        crate::prompts::PromptHistoryEntry::from_string("old prompt".to_string()),
    );

    let new_state = reduce(
        s,
        PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
            level: 3,
            attempt_count: 7,
            target_phase: PipelinePhase::Planning,
        }),
    );

    assert!(
        new_state.prompt_history.is_empty(),
        "Level 3 recovery must clear prompt_history to prevent stale replay after epoch rotation"
    );
    assert_eq!(new_state.recovery_epoch, 1);
}

#[test]
fn level_4_recovery_clears_prompt_history() {
    let state = PipelineState::initial(3, 0);
    let s = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        recovery_epoch: 0,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state
    };
    s.prompt_history.insert(
        "planning_iter1_normal".to_string(),
        crate::prompts::PromptHistoryEntry::from_string("old prompt".to_string()),
    );
    s.prompt_history.insert(
        "development_iter1_normal".to_string(),
        crate::prompts::PromptHistoryEntry::from_string("other old prompt".to_string()),
    );

    let new_state = reduce(
        s,
        PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
            level: 4,
            attempt_count: 10,
            target_phase: PipelinePhase::Planning,
        }),
    );

    assert!(
        new_state.prompt_history.is_empty(),
        "Level 4 recovery must clear prompt_history to prevent stale replay after epoch rotation"
    );
    assert_eq!(new_state.recovery_epoch, 1);
}

#[test]
fn level_1_and_2_recovery_preserve_prompt_history() {
    let state = PipelineState::initial(1, 0);
    let s = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        failed_phase_for_recovery: Some(PipelinePhase::Development),
        ..state
    };
    s.prompt_history.insert(
        "planning_iter1_normal".to_string(),
        crate::prompts::PromptHistoryEntry::from_string("valid prompt".to_string()),
    );

    let new_state = reduce(
        s.clone(),
        PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
            level: 1,
            attempt_count: 1,
            target_phase: PipelinePhase::Development,
        }),
    );
    assert!(
        !new_state.prompt_history.is_empty(),
        "Level 1 recovery must NOT clear prompt_history"
    );

    let new_state2 = reduce(
        s,
        PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
            level: 2,
            attempt_count: 4,
            target_phase: PipelinePhase::Development,
        }),
    );
    assert!(
        !new_state2.prompt_history.is_empty(),
        "Level 2 recovery must NOT clear prompt_history"
    );
}

#[test]
fn level_2_planning_phase_start_recovery_resets_context_and_gitignore_prereqs() {
    let state = PipelineState::initial(1, 0);
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        context_cleaned: true,
        gitignore_entries_ensured: true,
        ..state
    };

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
