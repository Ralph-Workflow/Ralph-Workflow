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

// =========================================================================
// prompt_history clearing tests (RFC-007 corrective action #3)
// =========================================================================

#[test]
fn level_3_recovery_clears_prompt_history() {
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::AwaitingDevFix;
    state.recovery_epoch = 0;
    state.failed_phase_for_recovery = Some(PipelinePhase::Development);
    state.prompt_history.insert(
        "planning_iter1_normal".to_string(),
        crate::prompts::PromptHistoryEntry::from_string("old prompt".to_string()),
    );

    let new_state = reduce(
        state,
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
    let mut state = PipelineState::initial(3, 0);
    state.phase = PipelinePhase::AwaitingDevFix;
    state.recovery_epoch = 0;
    state.failed_phase_for_recovery = Some(PipelinePhase::Development);
    state.prompt_history.insert(
        "planning_iter1_normal".to_string(),
        crate::prompts::PromptHistoryEntry::from_string("old prompt".to_string()),
    );
    state.prompt_history.insert(
        "development_iter1_normal".to_string(),
        crate::prompts::PromptHistoryEntry::from_string("other old prompt".to_string()),
    );

    let new_state = reduce(
        state,
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
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::AwaitingDevFix;
    state.failed_phase_for_recovery = Some(PipelinePhase::Development);
    state.prompt_history.insert(
        "planning_iter1_normal".to_string(),
        crate::prompts::PromptHistoryEntry::from_string("valid prompt".to_string()),
    );

    // Level 1: should preserve prompt_history
    let new_state = reduce(
        state.clone(),
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

    // Level 2: should preserve prompt_history
    let new_state2 = reduce(
        state,
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
