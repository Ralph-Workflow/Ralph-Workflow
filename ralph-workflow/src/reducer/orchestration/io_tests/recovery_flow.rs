use super::*;

#[test]
fn recovery_escalation_level_does_not_override_real_phase_work_after_recovery_attempt() {
    use crate::agents::AgentRole;
    use crate::reducer::event::PipelinePhase;

    let mut state = create_test_state();
    state.phase = PipelinePhase::Development;
    state.previous_phase = Some(PipelinePhase::AwaitingDevFix);
    state.failed_phase_for_recovery = Some(PipelinePhase::Development);
    state.dev_fix_attempt_count = 4;
    state.recovery_escalation_level = 2;
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["dev".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );

    // If orchestration incorrectly treats "recovery in progress" as an override
    // outside AwaitingDevFix, it will keep deriving EmitRecoveryReset forever.
    // Correct behavior: once the reducer has transitioned back to Development,
    // we derive the next real Development effect.
    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::PrepareDevelopmentContext { iteration } if iteration == state.iteration),
        "expected real Development work after recovery attempt, got: {effect:?}"
    );
}
