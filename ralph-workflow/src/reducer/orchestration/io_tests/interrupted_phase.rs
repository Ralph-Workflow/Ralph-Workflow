// Interrupted phase tests.
//
// Tests for checkpoint behavior when pipeline is interrupted.

use super::*;

#[test]
fn test_interrupted_phase_saves_checkpoint_before_abort_loop() {
    // Regression: if agent chain exhaustion triggers ReportAgentChainExhausted and the reducer
    // transitions to Interrupted, orchestration must not keep returning ReportAgentChainExhausted.
    // It should drive a checkpoint save so the event loop can mark completion.
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        checkpoint_saved_count: 0,
        interrupted_by_user: false,
        pre_termination_commit_checked: false,
        agent_chain: AgentChainState::initial()
            .with_agents(vec!["a".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_max_cycles(0),
        ..PipelineState::initial(0, 1)
    };

    let effect = determine_next_effect(&state);

    // Programmatic interrupts must not bypass the pre-termination commit safety check.
    assert!(matches!(
        effect,
        Effect::CheckUncommittedChangesBeforeTermination
    ));
}
