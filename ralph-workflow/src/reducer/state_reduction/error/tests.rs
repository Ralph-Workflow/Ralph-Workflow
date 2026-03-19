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

    errors.into_iter().for_each(|error| {
        let new_state = reduce_error(&state, &error);
        assert_eq!(
            new_state.phase,
            crate::reducer::event::PipelinePhase::AwaitingDevFix
        );
        assert!(
            !new_state.dev_fix_triggered,
            "expected dev_fix_triggered reset when routing to AwaitingDevFix"
        );
    });
}

#[test]
fn test_reduce_missing_inputs_errors_route_to_awaiting_dev_fix() {
    let state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());

    let errors = vec![ErrorEvent::ReviewInputsNotMaterialized { pass: 1 }];

    errors.into_iter().for_each(|error| {
        let new_state = reduce_error(&state, &error);
        assert_eq!(
            new_state.phase,
            crate::reducer::event::PipelinePhase::AwaitingDevFix
        );
        assert!(
            !new_state.dev_fix_triggered,
            "expected dev_fix_triggered reset when routing to AwaitingDevFix"
        );
    });
}

#[test]
fn test_reduce_fix_prompt_missing_is_recoverable_by_clearing_prepared_flag() {
    use crate::reducer::event::PipelinePhase;

    let state = PipelineState::initial_with_continuation(0, 1, &ContinuationState::default());
    let state = {
        let mut s = state;
        s.phase = PipelinePhase::Review;
        s.fix_prompt_prepared_pass = Some(0);
        s
    };

    let new_state = reduce_error(&state, &ErrorEvent::FixPromptMissing);

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.fix_prompt_prepared_pass, None);
}

#[test]
fn test_reduce_agent_not_found_advances_agent_chain_instead_of_terminating() {
    use crate::agents::AgentRole;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::state::AgentChainState;

    let state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    let state = {
        let mut s = state;
        s.phase = PipelinePhase::Development;
        s.agent_chain = AgentChainState::initial().with_agents(
            vec!["missing".to_string(), "fallback".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        );
        s
    };

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

    let state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());
    let state = {
        let mut s = state;
        s.phase = PipelinePhase::Review;
        s
    };

    let error = ErrorEvent::WorkspaceWriteFailed {
        path: ".agent/tmp/out.txt".to_string(),
        kind: WorkspaceIoErrorKind::Other,
    };

    let new_state = reduce_error(&state, &error);
    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    assert_eq!(
        new_state.previous_phase,
        Some(PipelinePhase::Review),
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

    let state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());
    let state = {
        let mut s = state;
        s.phase = PipelinePhase::Development;
        s.previous_phase = Some(PipelinePhase::AwaitingDevFix);
        s.dev_fix_attempt_count = 2;
        s.recovery_escalation_level = 1;
        s.failed_phase_for_recovery = Some(PipelinePhase::Development);
        s
    };

    let error = ErrorEvent::AgentChainExhausted {
        role: AgentRole::Developer,
        phase: PipelinePhase::Development,
        cycle: 3,
    };

    let new_state = reduce_error(&state, &error);

    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);

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

    let state = PipelineState::initial_with_continuation(1, 1, &ContinuationState::default());
    let state = {
        let mut s = state;
        s.phase = PipelinePhase::Development;
        s.previous_phase = Some(PipelinePhase::Planning);
        s.dev_fix_attempt_count = 5;
        s.recovery_escalation_level = 2;
        s.failed_phase_for_recovery = Some(PipelinePhase::Planning);
        s
    };

    let error = ErrorEvent::AgentChainExhausted {
        role: AgentRole::Developer,
        phase: PipelinePhase::Development,
        cycle: 3,
    };

    let new_state = reduce_error(&state, &error);

    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);

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

    let state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    let state = {
        let mut s = state;
        s.phase = PipelinePhase::CommitMessage;
        s.previous_phase = Some(PipelinePhase::AwaitingDevFix);
        s.failed_phase_for_recovery = Some(PipelinePhase::CommitMessage);
        s.dev_fix_attempt_count = 6;
        s.recovery_escalation_level = 2;
        s
    };

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

    let state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    let state = {
        let mut s = state;
        s.phase = PipelinePhase::CommitMessage;
        s.previous_phase = Some(PipelinePhase::Planning);
        s.dev_fix_attempt_count = 5;
        s.recovery_escalation_level = 2;
        s.failed_phase_for_recovery = Some(PipelinePhase::Planning);
        s
    };

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

    let state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    let state = {
        let mut s = state;
        s.phase = PipelinePhase::Review;
        s.previous_phase = Some(PipelinePhase::AwaitingDevFix);
        s.dev_fix_attempt_count = 6;
        s.recovery_escalation_level = 2;
        s.failed_phase_for_recovery = Some(PipelinePhase::Development);
        s
    };

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

    let state = PipelineState::initial_with_continuation(1, 0, &ContinuationState::default());
    let state = {
        let mut s = state;
        s.phase = PipelinePhase::AwaitingDevFix;
        s.previous_phase = Some(PipelinePhase::Review);
        s.failed_phase_for_recovery = Some(PipelinePhase::Review);
        s.dev_fix_attempt_count = 3;
        s.recovery_escalation_level = 1;
        s
    };

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
