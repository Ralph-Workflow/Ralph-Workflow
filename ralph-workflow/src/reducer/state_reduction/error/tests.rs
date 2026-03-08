use super::*;
use crate::reducer::state::ContinuationState;

#[test]
fn test_reduce_continuation_not_supported_errors_route_to_awaiting_dev_fix() {
    let state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());

    let errors = vec![
        ErrorEvent::PlanningContinuationNotSupported,
        ErrorEvent::ReviewContinuationNotSupported,
        ErrorEvent::FixContinuationNotSupported,
        ErrorEvent::CommitContinuationNotSupported,
    ];

    for error in errors {
        let new_state = reduce_error(&state, &error);
        assert_eq!(
            new_state.phase,
            crate::reducer::event::PipelinePhase::AwaitingDevFix
        );
        assert!(
            !new_state.dev_fix_triggered,
            "expected dev_fix_triggered reset when routing to AwaitingDevFix"
        );
    }
}

#[test]
fn test_reduce_missing_inputs_errors_route_to_awaiting_dev_fix() {
    let state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());

    let errors = vec![ErrorEvent::ReviewInputsNotMaterialized { pass: 1 }];

    for error in errors {
        let new_state = reduce_error(&state, &error);
        assert_eq!(
            new_state.phase,
            crate::reducer::event::PipelinePhase::AwaitingDevFix
        );
        assert!(
            !new_state.dev_fix_triggered,
            "expected dev_fix_triggered reset when routing to AwaitingDevFix"
        );
    }
}

#[test]
fn test_reduce_fix_prompt_missing_is_recoverable_by_clearing_prepared_flag() {
    use crate::reducer::event::PipelinePhase;

    let mut state = PipelineState::initial_with_continuation(0, 1, &ContinuationState::default());
    state.phase = PipelinePhase::Review;
    state.fix_prompt_prepared_pass = Some(0);

    let new_state = reduce_error(&state, &ErrorEvent::FixPromptMissing);

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.fix_prompt_prepared_pass, None);
}

#[test]
fn test_reduce_agent_not_found_advances_agent_chain_instead_of_terminating() {
    use crate::agents::AgentRole;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::state::AgentChainState;

    let mut state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    state.phase = PipelinePhase::Development;
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["missing".to_string(), "fallback".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );

    let new_state = reduce_error(
        &state,
        &ErrorEvent::AgentNotFound {
            agent: "missing".to_string(),
        },
    );

    assert_eq!(new_state.phase, PipelinePhase::Development);
    assert_eq!(new_state.agent_chain.current_agent_index, 1);
}

#[test]
fn test_reduce_agent_chain_exhausted_transitions_to_awaiting_dev_fix() {
    use crate::agents::AgentRole;
    use crate::reducer::event::PipelinePhase;

    let state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());
    assert_eq!(state.phase, PipelinePhase::Planning);

    let error = ErrorEvent::AgentChainExhausted {
        role: AgentRole::Developer,
        phase: PipelinePhase::Development,
        cycle: 3,
    };

    let new_state = reduce_error(&state, &error);

    // Should transition to AwaitingDevFix phase for remediation
    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    assert_eq!(
        new_state.previous_phase,
        Some(state.phase),
        "previous_phase should be recorded for dev-fix transitions"
    );
}

#[test]
fn test_reduce_workspace_failures_transition_to_awaiting_dev_fix_and_set_previous_phase() {
    use crate::reducer::event::{PipelinePhase, WorkspaceIoErrorKind};

    let mut state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());
    state.phase = PipelinePhase::Review;

    let error = ErrorEvent::WorkspaceWriteFailed {
        path: ".agent/tmp/out.txt".to_string(),
        kind: WorkspaceIoErrorKind::Other,
    };

    let new_state = reduce_error(&state, &error);
    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    assert_eq!(
        new_state.previous_phase,
        Some(state.phase),
        "previous_phase should be recorded for awaiting-dev-fix transitions"
    );
    assert!(
        !new_state.dev_fix_triggered,
        "dev_fix_triggered should be reset on awaiting-dev-fix transitions"
    );
}

#[test]
fn test_agent_chain_exhausted_preserves_recovery_state_when_already_in_recovery() {
    use crate::agents::AgentRole;
    use crate::reducer::event::PipelinePhase;

    // Set up state that's already in recovery loop
    let mut state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());
    state.phase = PipelinePhase::Development;
    state.previous_phase = Some(PipelinePhase::AwaitingDevFix); // Key: already in recovery
    state.dev_fix_attempt_count = 2;
    state.recovery_escalation_level = 1;
    state.failed_phase_for_recovery = Some(PipelinePhase::Development);

    // Simulate failure again during recovery
    let error = ErrorEvent::AgentChainExhausted {
        role: AgentRole::Developer,
        phase: PipelinePhase::Development,
        cycle: 3,
    };

    let new_state = reduce_error(&state, &error);

    // Should transition to AwaitingDevFix
    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);

    // CRITICAL: Should preserve recovery state (not reset to 0)
    assert_eq!(
        new_state.dev_fix_attempt_count, 2,
        "dev_fix_attempt_count should be preserved when already in recovery loop"
    );
    assert_eq!(
        new_state.recovery_escalation_level, 1,
        "recovery_escalation_level should be preserved when already in recovery loop"
    );
}

#[test]
fn test_agent_chain_exhausted_resets_recovery_state_on_first_failure() {
    use crate::agents::AgentRole;
    use crate::reducer::event::PipelinePhase;

    // Set up state that's NOT in recovery (first failure)
    let mut state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());
    state.phase = PipelinePhase::Development;
    state.previous_phase = Some(PipelinePhase::Planning); // Not AwaitingDevFix
                                                          // Simulate stale recovery state from previous recovery
    state.dev_fix_attempt_count = 5;
    state.recovery_escalation_level = 2;
    state.failed_phase_for_recovery = Some(PipelinePhase::Planning);

    // Simulate first failure (not in recovery)
    let error = ErrorEvent::AgentChainExhausted {
        role: AgentRole::Developer,
        phase: PipelinePhase::Development,
        cycle: 3,
    };

    let new_state = reduce_error(&state, &error);

    // Should transition to AwaitingDevFix
    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);

    // Should reset recovery state (new failure, not recovery loop)
    assert_eq!(
        new_state.dev_fix_attempt_count, 0,
        "dev_fix_attempt_count should be reset on first failure"
    );
    assert_eq!(
        new_state.recovery_escalation_level, 0,
        "recovery_escalation_level should be reset on first failure"
    );
}

#[test]
fn test_workspace_and_git_failures_preserve_recovery_escalation_when_already_in_recovery_loop() {
    use crate::reducer::event::PipelinePhase;

    let mut state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::AwaitingDevFix);
    state.failed_phase_for_recovery = Some(PipelinePhase::CommitMessage);
    state.dev_fix_attempt_count = 6;
    state.recovery_escalation_level = 2;

    let new_state = reduce_error(
        &state,
        &ErrorEvent::GitAddAllFailed {
            kind: crate::reducer::event::WorkspaceIoErrorKind::Other,
        },
    );

    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    assert_eq!(new_state.dev_fix_attempt_count, 6);
    assert_eq!(new_state.recovery_escalation_level, 2);
    assert!(
        !new_state.dev_fix_triggered,
        "expected dev_fix_triggered reset when routing to AwaitingDevFix"
    );
}

#[test]
fn continuation_not_supported_sets_failed_phase_and_resets_recovery_counters_on_new_failure() {
    use crate::reducer::event::PipelinePhase;

    let mut state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::Planning);

    // Simulate stale recovery state from a prior recovery context.
    state.dev_fix_attempt_count = 5;
    state.recovery_escalation_level = 2;
    state.failed_phase_for_recovery = Some(PipelinePhase::Planning);

    let new_state = reduce_error(&state, &ErrorEvent::CommitContinuationNotSupported);

    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    assert_eq!(
        new_state.failed_phase_for_recovery,
        Some(PipelinePhase::CommitMessage)
    );
    assert_eq!(new_state.dev_fix_attempt_count, 0);
    assert_eq!(new_state.recovery_escalation_level, 0);
}

#[test]
fn missing_inputs_sets_failed_phase_and_preserves_recovery_counters_when_in_recovery_loop() {
    use crate::reducer::event::PipelinePhase;

    let mut state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    state.phase = PipelinePhase::Review;
    state.previous_phase = Some(PipelinePhase::AwaitingDevFix);

    state.dev_fix_attempt_count = 6;
    state.recovery_escalation_level = 2;
    state.failed_phase_for_recovery = Some(PipelinePhase::Development);

    let new_state = reduce_error(&state, &ErrorEvent::ReviewInputsNotMaterialized { pass: 1 });

    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    assert_eq!(
        new_state.failed_phase_for_recovery,
        Some(PipelinePhase::Review)
    );
    assert_eq!(new_state.dev_fix_attempt_count, 6);
    assert_eq!(new_state.recovery_escalation_level, 2);
}

#[test]
fn workspace_failures_do_not_overwrite_failed_phase_when_already_in_awaiting_dev_fix() {
    use crate::reducer::event::{PipelinePhase, WorkspaceIoErrorKind};

    let mut state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    state.phase = PipelinePhase::AwaitingDevFix;
    state.previous_phase = Some(PipelinePhase::Review);
    state.failed_phase_for_recovery = Some(PipelinePhase::Review);
    state.dev_fix_attempt_count = 3;
    state.recovery_escalation_level = 1;

    let new_state = reduce_error(
        &state,
        &ErrorEvent::WorkspaceWriteFailed {
            path: ".agent/tmp/out.txt".to_string(),
            kind: WorkspaceIoErrorKind::Other,
        },
    );

    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    assert_eq!(
        new_state.failed_phase_for_recovery,
        Some(PipelinePhase::Review)
    );
    assert_eq!(new_state.dev_fix_attempt_count, 3);
    assert_eq!(new_state.recovery_escalation_level, 1);
}
