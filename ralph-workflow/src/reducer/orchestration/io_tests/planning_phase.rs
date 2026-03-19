// Planning phase tests.
//
// Tests for planning phase effect determination, agent chain initialization,
// and transition to development.

use super::*;

#[test]
fn test_determine_effect_planning_phase() {
    let state = create_test_state();
    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Planning,
            ..
        }
    ));
}

#[test]
fn test_determine_effect_planning_with_agents() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        gitignore_entries_ensured: true, // Gitignore must be ensured before planning
        context_cleaned: true,           // Context must be cleaned before planning
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(crate::agents::AgentDrain::Planning),
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::MaterializePlanningInputs { .. }));
}

#[test]
fn test_determine_effect_planning_non_developer_chain_reinitializes_chain() {
    // Regression: Planning must use the Developer chain, even if we enter Planning
    // with a previously-initialized non-developer chain from a prior phase.
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        context_cleaned: true,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Planning,
            ..
        }
    ));
}

#[test]
fn test_determine_effect_planning_rematerializes_when_consumer_signature_changes() {
    let mut state = PipelineState {
        phase: PipelinePhase::Planning,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        iteration: 2,
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string(), "fallback".to_string()],
                vec![vec!["m1".to_string()], vec!["m2".to_string()]],
                AgentRole::Developer,
            )
            .with_drain(crate::agents::AgentDrain::Planning),
        prompt_inputs: crate::reducer::state::PromptInputsState {
            planning: Some(crate::reducer::state::MaterializedPlanningInputs {
                iteration: 2,
                prompt: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Prompt,
                    content_id_sha256: "id".to_string(),
                    consumer_signature_sha256: "stale_sig".to_string(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: Some(100_000),
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
            }),
            ..Default::default()
        },
        ..create_test_state()
    };

    let expected_sig = state.agent_chain.consumer_signature_sha256();
    assert_ne!(expected_sig, "stale_sig");

    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::MaterializePlanningInputs { iteration: 2 }),
        "Expected re-materialization when consumer signature changes, got {effect:?}"
    );

    // When signatures match, only changing current agent index should not trigger rematerialization.
    state
        .prompt_inputs
        .planning
        .as_mut()
        .unwrap()
        .prompt
        .consumer_signature_sha256 = expected_sig;
    state.agent_chain.current_agent_index = 1;
    let effect = determine_next_effect(&state);
    assert!(
        !matches!(effect, Effect::MaterializePlanningInputs { .. }),
        "Expected no re-materialization when only current agent index changes, got {effect:?}"
    );
}

#[test]
fn test_planning_phase_emits_single_task_effect() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        iteration: 0,
        total_iterations: 3,
        agent_chain: PipelineState::initial(3, 0)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(crate::agents::AgentDrain::Planning),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::MaterializePlanningInputs { .. }),
        "Planning should emit MaterializePlanningInputs, got {effect:?}"
    );
}

#[test]
fn test_planning_phase_uses_xsd_retry_prompt_when_pending() {
    let sig = PipelineState::initial(5, 2)
        .agent_chain
        .with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_drain(crate::agents::AgentDrain::Planning)
        .consumer_signature_sha256();

    let state = PipelineState {
        phase: PipelinePhase::Planning,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        iteration: 1,
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(crate::agents::AgentDrain::Planning),
        prompt_inputs: crate::reducer::state::PromptInputsState {
            planning: Some(crate::reducer::state::MaterializedPlanningInputs {
                iteration: 1,
                prompt: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Prompt,
                    content_id_sha256: "id".to_string(),
                    consumer_signature_sha256: sig,
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: Some(100_000),
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
            }),
            ..Default::default()
        },
        continuation: crate::reducer::state::ContinuationState {
            xsd_retry_pending: true,
            ..crate::reducer::state::ContinuationState::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            Effect::PreparePlanningPrompt {
                iteration: 1,
                prompt_mode: PromptMode::XsdRetry
            }
        ),
        "Expected XSD retry prompt when xsd_retry_pending=true, got {effect:?}"
    );
}

#[test]
fn test_planning_phase_transitions_to_development_after_completion() {
    // Create state in Planning phase with agents initialized
    let mut state = PipelineState {
        phase: PipelinePhase::Planning,
        iteration: 1,
        total_iterations: 5,
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(crate::agents::AgentDrain::Planning),
        ..create_test_state()
    };

    // Simulate plan generation completing
    state = reduce(state, PipelineEvent::plan_generation_completed(1, true));

    // After plan generation completes, phase should transition to Development
    assert_eq!(
        state.phase,
        PipelinePhase::Development,
        "Phase should transition to Development after PlanGenerationCompleted"
    );

    // Orchestration should now initialize the development drain before preparing context.
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Development,
                ..
            }
        ),
        "Expected development drain initialization, got {effect:?}"
    );
}

#[test]
fn test_initial_state_skips_planning_when_zero_developer_iters() {
    // When developer_iters=0, the initial state should skip Planning phase entirely
    let state = PipelineState::initial(0, 2);
    assert_eq!(
        state.phase,
        PipelinePhase::Review,
        "Initial phase should be Review when developer_iters=0 and reviewer_reviews>0"
    );
}

#[test]
fn test_initial_state_skips_to_commit_when_zero_iters_and_reviews() {
    // When both developer_iters=0 and reviewer_reviews=0, skip to CommitMessage
    let state = PipelineState::initial(0, 0);
    assert_eq!(
        state.phase,
        PipelinePhase::CommitMessage,
        "Initial phase should be CommitMessage when developer_iters=0 and reviewer_reviews=0"
    );
}

#[test]
fn test_initial_state_starts_planning_when_developer_iters_nonzero() {
    // When developer_iters>0, start in Planning phase as normal
    let state = PipelineState::initial(1, 0);
    assert_eq!(
        state.phase,
        PipelinePhase::Planning,
        "Initial phase should be Planning when developer_iters>0"
    );
}
