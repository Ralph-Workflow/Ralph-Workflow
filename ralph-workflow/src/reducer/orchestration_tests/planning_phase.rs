// Planning phase orchestration tests.
//
// Tests for planning phase: agent chain initialization, prompt preparation,
// XSD retry mode, and transition to development phase.

use super::*;

#[test]
fn test_planning_initializes_agent_chain_when_empty() {
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
fn test_planning_prepares_prompt_when_agents_ready() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        gitignore_entries_ensured: true,
        context_cleaned: true, // Context must be cleaned before planning
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
fn test_planning_role_mismatch_initializes_developer_chain() {
    // Regression: entering Planning with a non-developer chain must still initialize
    // the developer chain so FallbackConfig.developer is honored.
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        agent_chain: crate::reducer::state::AgentChainState::initial().with_agents(
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
fn test_planning_resume_with_legacy_development_drain_keeps_planning_flow() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        agent_chain: crate::reducer::state::AgentChainState::initial()
            .with_agents(
                vec!["dev-agent".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(crate::agents::AgentDrain::Development),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::MaterializePlanningInputs { .. }));
}

#[test]
fn test_planning_prompt_uses_xsd_retry_mode_when_pending() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        iteration: 0,
        total_iterations: 1,
        continuation: PipelineState::initial(1, 1)
            .continuation
            .trigger_xsd_retry(),
        agent_chain: PipelineState::initial(1, 1).agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    assert!(matches!(
        effect,
        Effect::PreparePlanningPrompt {
            iteration: 0,
            prompt_mode: PromptMode::XsdRetry
        }
    ));
}

#[test]
fn test_planning_emits_prepare_prompt_effect() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        iteration: 0,
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

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::MaterializePlanningInputs { .. }),
        "Planning should emit MaterializePlanningInputs, got {effect:?}"
    );
}

#[test]
fn test_planning_transitions_to_development_after_completion() {
    let mut state = PipelineState {
        phase: PipelinePhase::Planning,
        iteration: 1,
        total_iterations: 5,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        ..create_test_state()
    };

    // Plan generation completes
    state = reduce(state, PipelineEvent::plan_generation_completed(1, true));

    assert_eq!(
        state.phase,
        PipelinePhase::Development,
        "Phase should transition to Development after PlanGenerationCompleted"
    );

    // Orchestration should now return PrepareDevelopmentContext, not planning effects
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::PrepareDevelopmentContext { .. }),
        "Expected PrepareDevelopmentContext, got {effect:?}"
    );
}

#[test]
fn test_planning_markdown_written_invalidates_downstream_materialized_inputs() {
    // Regression test: writing PLAN.md should invalidate any already-materialized
    // development/review inputs so downstream phases don't reuse stale copies.
    let mut state = PipelineState {
        phase: PipelinePhase::Planning,
        iteration: 0,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        prompt_inputs: crate::reducer::state::PromptInputsState {
            development: Some(crate::reducer::state::MaterializedDevelopmentInputs {
                iteration: 0,
                prompt: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Prompt,
                    content_id_sha256: "prompt".to_string(),
                    consumer_signature_sha256: "sig".to_string(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
                plan: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Plan,
                    content_id_sha256: "old-plan".to_string(),
                    consumer_signature_sha256: "sig".to_string(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
            }),
            review: Some(crate::reducer::state::MaterializedReviewInputs {
                pass: 0,
                plan: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Plan,
                    content_id_sha256: "old-plan".to_string(),
                    consumer_signature_sha256: "sig".to_string(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
                diff: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
                    content_id_sha256: "diff".to_string(),
                    consumer_signature_sha256: "sig".to_string(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
            }),
            ..Default::default()
        },
        ..create_test_state()
    };

    state = reduce(state, PipelineEvent::planning_markdown_written(0));

    assert!(
        state.prompt_inputs.development.is_none(),
        "Expected development inputs to be invalidated when PLAN.md is written"
    );
    assert!(
        state.prompt_inputs.review.is_none(),
        "Expected review inputs to be invalidated when PLAN.md is written"
    );
}
