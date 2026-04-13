//! Reducer-only control flow tests and agent chain state management tests.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;

// ============================================================================
// REDUCER-ONLY CONTROL FLOW TESTS
// ============================================================================

/// Test that state transitions are purely driven by events through the reducer.
///
/// This verifies that phase transitions happen via the `reduce()` function,
/// not through any direct state mutation.
#[test]
fn test_state_transitions_via_reducer_only() {
    use ralph_workflow::reducer::event::{CommitEvent, PipelineEvent, PipelinePhase};
    use ralph_workflow::reducer::state::PipelineState;
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        // Phase 2 flow: Dev → Review → CommitMessage (per iteration), not Dev → CommitMessage.
        // Start at Planning with 2 dev iterations and 1 reviewer pass each.
        let state = with_locked_prompt_permissions(PipelineState::initial(2, 1));
        assert_eq!(state.phase, PipelinePhase::Planning);
        assert_eq!(state.iteration, 0, "Initial iteration is 0");

        // Planning -> Development transition via reduce()
        let state = reduce(state, PipelineEvent::plan_generation_completed(1, true));
        assert_eq!(
            state.phase,
            PipelinePhase::Development,
            "Transition to Development must happen via reducer"
        );
        assert_eq!(state.iteration, 0, "Iteration unchanged by plan completion");

        // Phase 2: Development iteration completion -> Review (not CommitMessage directly)
        let state = reduce(
            state,
            PipelineEvent::development_iteration_completed(0, true),
        );
        assert_eq!(
            state.phase,
            PipelinePhase::Review,
            "Phase 2: Dev iteration completion transitions to Review"
        );
        assert_eq!(state.iteration, 0, "Iteration unchanged until commit");

        // Review -> CommitMessage (after review pass completes)
        let state = reduce(state, PipelineEvent::review_phase_completed(false));
        assert_eq!(
            state.phase,
            PipelinePhase::CommitMessage,
            "Review completes and transitions to CommitMessage"
        );

        // After commit created with more iterations, goes to Planning for next iteration
        // The reducer pattern is: Dev -> Review -> Commit -> Planning -> Dev (per iteration)
        let state = reduce(
            state,
            PipelineEvent::Commit(CommitEvent::Created {
                message: "test commit".to_string(),
                hash: "abc123".to_string(),
            }),
        );
        assert_eq!(
            state.phase,
            PipelinePhase::Planning,
            "After commit with more iterations, goes to Planning"
        );
        assert_eq!(state.iteration, 1, "Iteration incremented to 1");

        // Planning again -> Development (iteration 1)
        let state = reduce(state, PipelineEvent::plan_generation_completed(2, true));
        assert_eq!(state.phase, PipelinePhase::Development);

        // Phase 2: Complete iteration 1 -> Review
        let state = reduce(
            state,
            PipelineEvent::development_iteration_completed(1, true),
        );
        assert_eq!(state.phase, PipelinePhase::Review);

        // Review -> CommitMessage
        let state = reduce(state, PipelineEvent::review_phase_completed(false));
        assert_eq!(state.phase, PipelinePhase::CommitMessage);

        // After final commit (iteration 1+1=2 >= total=2), transitions to FinalValidation
        let state = reduce(
            state,
            PipelineEvent::Commit(CommitEvent::Created {
                message: "final commit".to_string(),
                hash: "def456".to_string(),
            }),
        );
        assert_eq!(
            state.phase,
            PipelinePhase::FinalValidation,
            "After final iteration commit with all reviews done, should transition to FinalValidation"
        );
        // Phase 2: iteration is not incremented when transitioning to FinalValidation
        // (only incremented when transitioning to Planning for the next iteration)
        assert_eq!(
            state.iteration, 1,
            "Iteration stays at 1 when reaching FinalValidation"
        );
    });
}

/// Test that effect determination is based solely on reducer state.
///
/// The `determine_next_effect()` function should be a pure function of state,
/// not reading any external configuration or files.
#[test]
fn test_effects_determined_from_state_only() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::effect::Effect;
    use ralph_workflow::reducer::event::PipelinePhase;
    use ralph_workflow::reducer::orchestration::determine_next_effect;
    use ralph_workflow::reducer::state::PipelineState;

    with_default_timeout(|| {
        // Initial state needs agent chain initialization
        let state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Planning,
                    ..
                }
            ),
            "Effect should be determined purely from state: {effect:?}"
        );

        // State with agents but no gitignore ensured -> ensure gitignore
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.agent_chain = state
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Planning);
        state.gitignore_entries_ensured = false;
        state.context_cleaned = false;
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::EnsureGitignoreEntries),
            "Should ensure gitignore before cleanup: {effect:?}"
        );

        // State with gitignore ensured but no context cleaned -> clean context
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.agent_chain = state
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Planning);
        state.gitignore_entries_ensured = true;
        state.context_cleaned = false;
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CleanupContext),
            "Should clean context before planning: {effect:?}"
        );

        // State ready for planning
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.agent_chain = state
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Planning);
        state.gitignore_entries_ensured = true;
        state.context_cleaned = true;
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::MaterializePlanningInputs { .. }),
            "Should materialize planning inputs when state is ready: {effect:?}"
        );

        // Development phase
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.iteration = 1;
        state.agent_chain = state
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Development);
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::PrepareDevelopmentContext { .. }),
            "Should start development chain from state: {effect:?}"
        );
    });
}

/// Test that agent selection comes from reducer state, not config lookups.
///
/// The `agent_chain` in `PipelineState` should be the single source of truth
/// for which agent to use next.
#[test]
fn test_agent_selection_from_reducer_state() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::effect::Effect;
    use ralph_workflow::reducer::event::PipelinePhase;
    use ralph_workflow::reducer::orchestration::determine_next_effect;
    use ralph_workflow::reducer::state::PipelineState;

    with_default_timeout(|| {
        // Set up state with specific agents in chain
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.iteration = 1;
        state.agent_chain = state.agent_chain.with_agents(
            vec!["custom-agent".to_string(), "fallback-agent".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        );

        // The effect doesn't contain agent name - handler reads from state.agent_chain
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::PrepareDevelopmentContext { iteration: 1 }),
            "Expected PrepareDevelopmentContext, got {effect:?}"
        );

        // Verify agent chain has our custom agent as current
        assert_eq!(
            state.agent_chain.current_agent(),
            Some(&"custom-agent".to_string()),
            "Agent should be selected from state.agent_chain"
        );

        // After switching to next agent, chain should point to fallback
        state.agent_chain = state.agent_chain.switch_to_next_agent();
        assert_eq!(
            state.agent_chain.current_agent(),
            Some(&"fallback-agent".to_string()),
            "Should use next agent in chain after switch"
        );
    });
}

/// Test that pipeline completion is determined by reducer state, not file existence.
///
/// The pipeline should complete when state.phase == Complete, not when
/// certain files exist on disk.
#[test]
fn test_completion_from_state_not_files() {
    use ralph_workflow::reducer::effect::Effect;
    use ralph_workflow::reducer::event::{CheckpointTrigger, PipelinePhase};
    use ralph_workflow::reducer::orchestration::determine_next_effect;
    use ralph_workflow::reducer::state::PipelineState;

    with_default_timeout(|| {
        // State at Complete phase
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Complete;

        // First cycle: safety check
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CheckUncommittedChangesBeforeTermination),
            "Complete phase first checks for uncommitted changes: {effect:?}"
        );

        // After safety check passes
        state.pre_termination_commit_checked = true;
        let effect = determine_next_effect(&state);
        // Complete phase emits SaveCheckpoint with PhaseTransition trigger
        assert!(
            matches!(
                effect,
                Effect::SaveCheckpoint {
                    trigger: CheckpointTrigger::PhaseTransition
                }
            ),
            "Should save checkpoint on complete based on state.phase, not file checks: {effect:?}"
        );
    });
}

// ============================================================================
// AGENT CHAIN STATE MANAGEMENT TESTS
// ============================================================================

/// Test that agent chain is cleared on dev->review transition via reducer.
///
/// When transitioning from Development to Review phase, the agent chain must
/// be cleared so that the orchestrator initializes a fresh Reviewer chain.
/// This prevents the developer agent chain from leaking into review phase.
#[test]
fn test_agent_chain_cleared_on_dev_to_review_transition() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::event::{CommitEvent, PipelineEvent, PipelinePhase};
    use ralph_workflow::reducer::state::PipelineState;
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        // Start with populated developer agent chain that has been used
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 1));
        state.agent_chain = state.agent_chain.with_agents(
            vec!["dev-primary".to_string(), "dev-fallback".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        );
        state.phase = PipelinePhase::CommitMessage;
        state.previous_phase = Some(PipelinePhase::Development);
        state.commit = ralph_workflow::reducer::state::CommitState::Generated {
            message: "test commit".to_string(),
        };

        // Verify developer chain is populated
        assert!(!state.agent_chain.agents.is_empty());
        assert_eq!(state.agent_chain.current_role, AgentRole::Developer);

        // Transition via CommitEvent::Created - this should go to Review since
        // iteration 0 + 1 = 1 >= total_iterations (1)
        let new_state = reduce(
            state,
            PipelineEvent::Commit(CommitEvent::Created {
                hash: "abc123".to_string(),
                message: "test commit".to_string(),
            }),
        );

        // Should be in Review phase
        assert_eq!(
            new_state.phase,
            PipelinePhase::Review,
            "Should transition to Review phase"
        );

        // Observable behavior: the orchestrator should need to initialize
        // a new Reviewer agent chain (the old Developer chain was cleared).
        let effect = ralph_workflow::reducer::orchestration::determine_next_effect(&new_state);
        assert!(
            matches!(
                effect,
                ralph_workflow::reducer::effect::Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Review,
                    ..
                }
            ),
            "After dev->review transition, next effect must initialize the review drain, got {effect:?}"
        );
    });
}
