// Development phase orchestration tests.
//
// Tests for development phase: iteration count, continuation prompt mode,
// and agent chain exhaustion.

use crate::agents::AgentRole;
use crate::reducer::create_test_state;
use crate::reducer::determine_next_effect;
use crate::reducer::effect::Effect;
use crate::reducer::event::DevelopmentEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::PipelinePhase;
use crate::reducer::reduce;
use crate::reducer::state::PipelineState;
use crate::reducer::state::PromptMode;
use crate::reducer::state::PromptPermissionsState;
use crate::reducer::TimeoutOutputKind;

fn initial_with_locked_permissions(dev_iters: u32, review_passes: u32) -> PipelineState {
    PipelineState {
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..PipelineState::initial(dev_iters, review_passes)
    }
}

#[test]
fn test_development_runs_exactly_n_iterations() {
    // When total_iterations=5, should run iterations 0,1,2,3,4 (5 total)
    let mut state = initial_with_locked_permissions(5, 0);
    state.agent_chain = state.agent_chain.with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );

    let mut iterations_run = Vec::new();

    // Simulate the development phase (includes CommitMessage after each iteration)
    while state.phase == PipelinePhase::Planning
        || state.phase == PipelinePhase::Development
        || state.phase == PipelinePhase::CommitMessage
    {
        let effect = determine_next_effect(&state);

        match effect {
            Effect::EnsureGitignoreEntries => {
                state = reduce(
                    state,
                    PipelineEvent::gitignore_entries_ensured(
                        vec!["/PROMPT*".to_string(), ".agent/".to_string()],
                        vec![],
                        false,
                    ),
                );
            }
            Effect::CleanupContext => {
                state = reduce(state, PipelineEvent::ContextCleaned);
            }
            Effect::CleanupContinuationContext => {
                state = reduce(
                    state,
                    PipelineEvent::development_continuation_context_cleaned(),
                );
            }
            Effect::MaterializePlanningInputs { iteration } => {
                let sig = state.agent_chain.consumer_signature_sha256();
                state = reduce(
                    state,
                    PipelineEvent::planning_inputs_materialized(
                        iteration,
                        crate::reducer::state::MaterializedPromptInput {
                            kind: crate::reducer::state::PromptInputKind::Prompt,
                            content_id_sha256: "id".to_string(),
                            consumer_signature_sha256: sig,
                            original_bytes: 1,
                            final_bytes: 1,
                            model_budget_bytes: None,
                            inline_budget_bytes: None,
                            representation:
                                crate::reducer::state::PromptInputRepresentation::Inline,
                            reason:
                                crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                        },
                    ),
                );
            }
            Effect::PreparePlanningPrompt { iteration, .. } => {
                state = reduce(state, PipelineEvent::planning_prompt_prepared(iteration));
            }
            Effect::CleanupRequiredFiles { files }
                if files.iter().any(|f| f.contains("plan.xml")) =>
            {
                let iteration = state.iteration;
                state = reduce(state, PipelineEvent::planning_xml_cleaned(iteration));
            }
            Effect::InvokePlanningAgent { iteration } => {
                state = reduce(state, PipelineEvent::planning_agent_invoked(iteration));
            }
            Effect::ExtractPlanningXml { iteration } => {
                state = reduce(state, PipelineEvent::planning_xml_extracted(iteration));
            }
            Effect::ValidatePlanningXml { iteration } => {
                state = reduce(
                    state,
                    PipelineEvent::planning_xml_validated(
                        iteration,
                        true,
                        Some("# Plan\n\n- step\n".to_string()),
                    ),
                );
            }
            Effect::WritePlanningMarkdown { iteration } => {
                state = reduce(state, PipelineEvent::planning_markdown_written(iteration));
            }
            Effect::ArchivePlanningXml { iteration } => {
                state = reduce(state, PipelineEvent::planning_xml_archived(iteration));
            }
            Effect::ApplyPlanningOutcome { iteration, valid } => {
                state = reduce(
                    state,
                    PipelineEvent::plan_generation_completed(iteration, valid),
                );
            }
            Effect::PrepareDevelopmentContext { iteration } => {
                state = reduce(
                    state,
                    PipelineEvent::development_context_prepared(iteration),
                );
            }
            Effect::MaterializeDevelopmentInputs { iteration } => {
                let sig = state.agent_chain.consumer_signature_sha256();
                let prompt = crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Prompt,
                    content_id_sha256: "id".to_string(),
                    consumer_signature_sha256: sig.clone(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                };
                let plan = crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Plan,
                    content_id_sha256: "id".to_string(),
                    consumer_signature_sha256: sig,
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                };
                state = reduce(
                    state,
                    PipelineEvent::development_inputs_materialized(iteration, prompt, plan),
                );
            }
            Effect::PrepareDevelopmentPrompt { iteration, .. } => {
                state = reduce(state, PipelineEvent::development_prompt_prepared(iteration));
            }
            Effect::CleanupRequiredFiles { files }
                if files.iter().any(|f| f.contains("development_result.xml")) =>
            {
                let iteration = state.iteration;
                state = reduce(state, PipelineEvent::development_xml_cleaned(iteration));
            }
            Effect::InvokeDevelopmentAgent { iteration } => {
                state = reduce(state, PipelineEvent::development_agent_invoked(iteration));
            }
            Effect::InvokeAnalysisAgent { iteration } => {
                // XSD retry / resume safety: analysis invocations may require switching agent role.
                assert_eq!(
                    state.agent_chain.current_role,
                    AgentRole::Analysis,
                    "InvokeAnalysisAgent must only occur when agent chain is initialized for Analysis"
                );
                state = reduce(
                    state,
                    PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked {
                        iteration,
                    }),
                );
            }
            Effect::ExtractDevelopmentXml { iteration } => {
                state = reduce(state, PipelineEvent::development_xml_extracted(iteration));
            }
            Effect::ValidateDevelopmentXml { iteration } => {
                state = reduce(
                    state,
                    PipelineEvent::development_xml_validated(
                        iteration,
                        crate::reducer::state::DevelopmentStatus::Completed,
                        "done".to_string(),
                        None,
                        None,
                    ),
                );
            }
            Effect::ArchiveDevelopmentXml { iteration } => {
                state = reduce(state, PipelineEvent::development_xml_archived(iteration));
            }
            Effect::ApplyDevelopmentOutcome { iteration } => {
                iterations_run.push(iteration);
                state = reduce(state, PipelineEvent::development_outcome_applied(iteration));
            }
            Effect::CheckCommitDiff => {
                state = reduce(
                    state,
                    PipelineEvent::commit_diff_prepared(false, "id".to_string()),
                );
            }
            Effect::MaterializeCommitInputs { attempt } => {
                let sig = state.agent_chain.consumer_signature_sha256();
                state = reduce(
                    state,
                    PipelineEvent::commit_inputs_materialized(
                        attempt,
                        crate::reducer::state::MaterializedPromptInput {
                            kind: crate::reducer::state::PromptInputKind::Diff,
                            content_id_sha256: "id".to_string(),
                            consumer_signature_sha256: sig,
                            original_bytes: 1,
                            final_bytes: 1,
                            model_budget_bytes: None,
                            inline_budget_bytes: None,
                            representation:
                                crate::reducer::state::PromptInputRepresentation::Inline,
                            reason:
                                crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                        },
                    ),
                );
            }
            Effect::PrepareCommitPrompt { .. } => {
                state = reduce(state, PipelineEvent::commit_generation_started());
                state = reduce(state, PipelineEvent::commit_prompt_prepared(1));
            }
            Effect::CleanupRequiredFiles { files }
                if files.iter().any(|f| f.contains("commit_message.xml")) =>
            {
                state = reduce(state, PipelineEvent::commit_required_files_cleaned(1));
            }
            Effect::InvokeCommitAgent => {
                state = reduce(state, PipelineEvent::commit_agent_invoked(1));
            }
            Effect::ExtractCommitXml => {
                state = reduce(state, PipelineEvent::commit_xml_extracted(1));
            }
            Effect::ValidateCommitXml => {
                state = reduce(
                    state,
                    PipelineEvent::commit_xml_validated("test".to_string(), vec![], vec![], 1),
                );
            }
            Effect::ApplyCommitMessageOutcome => {
                state = reduce(
                    state,
                    PipelineEvent::commit_message_generated("test".to_string(), 1),
                );
            }
            Effect::ArchiveCommitXml => {
                state = reduce(state, PipelineEvent::commit_xml_archived(1));
            }
            Effect::CreateCommit { .. } => {
                state = reduce(
                    state,
                    PipelineEvent::commit_created(
                        format!("abc{}", iterations_run.len()),
                        "test".to_string(),
                    ),
                );
            }
            Effect::SaveCheckpoint { .. } => break,
            Effect::InitializeAgentChain { drain, .. } => {
                state = reduce(
                    state,
                    PipelineEvent::agent_chain_initialized(
                        drain,
                        vec!["claude".to_string()],
                        3,
                        1000,
                        2.0,
                        60000,
                    ),
                );
            }
            Effect::LockPromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_locked(None));
            }
            Effect::RestorePromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_restored());
            }
            _ => panic!("Unexpected effect: {effect:?}"),
        }
    }

    assert_eq!(
        iterations_run.len(),
        5,
        "Should run exactly 5 iterations, ran: {iterations_run:?}"
    );
    assert_eq!(
        iterations_run,
        vec![0, 1, 2, 3, 4],
        "Should run iterations 0-4"
    );
    // With total_reviewer_passes=0, we go to FinalValidation, not Review
    assert_eq!(
        state.phase,
        PipelinePhase::FinalValidation,
        "Should transition to FinalValidation after 5 iterations when reviewer_passes=0"
    );
}

#[test]
fn test_development_continuation_emits_prompt_mode_continuation() {
    let mut state = initial_with_locked_permissions(1, 0);
    state.phase = PipelinePhase::Development;
    state.iteration = 0;
    state.total_iterations = 1;
    state.development_context_prepared_iteration = Some(0);
    state.continuation.continuation_attempt = 1;
    state.continuation.continue_pending = false;
    state.prompt_inputs.development = Some(crate::reducer::state::MaterializedDevelopmentInputs {
        iteration: 0,
        prompt: crate::reducer::state::MaterializedPromptInput {
            kind: crate::reducer::state::PromptInputKind::Prompt,
            content_id_sha256: "id".to_string(),
            consumer_signature_sha256: String::new(),
            original_bytes: 1,
            final_bytes: 1,
            model_budget_bytes: None,
            inline_budget_bytes: None,
            representation: crate::reducer::state::PromptInputRepresentation::Inline,
            reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
        },
        plan: crate::reducer::state::MaterializedPromptInput {
            kind: crate::reducer::state::PromptInputKind::Plan,
            content_id_sha256: "id".to_string(),
            consumer_signature_sha256: String::new(),
            original_bytes: 1,
            final_bytes: 1,
            model_budget_bytes: None,
            inline_budget_bytes: None,
            representation: crate::reducer::state::PromptInputRepresentation::Inline,
            reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
        },
    });
    state.agent_chain = state.agent_chain.with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );
    let sig = state.agent_chain.consumer_signature_sha256();
    if let Some(inputs) = state.prompt_inputs.development.as_mut() {
        inputs.prompt.consumer_signature_sha256 = sig.clone();
        inputs.plan.consumer_signature_sha256 = sig;
    }

    let effect = determine_next_effect(&state);

    assert!(matches!(
        effect,
        Effect::PrepareDevelopmentPrompt {
            iteration: 0,
            prompt_mode: PromptMode::Continuation
        }
    ));
}

#[test]
fn test_development_timeout_retry_does_not_use_xsd_retry_prompt_mode() {
    // Timeouts should retry the same agent with timeout-aware guidance, not via XSD retry.
    // This test asserts orchestration does not select PromptMode::XsdRetry after a timeout.
    let mut state = initial_with_locked_permissions(1, 0);
    state.phase = PipelinePhase::Development;
    state.iteration = 0;
    state.total_iterations = 1;
    state.development_context_prepared_iteration = Some(0);
    state.continuation.xsd_retry_pending = false;
    state.continuation.xsd_retry_count = 0;
    state.agent_chain = state.agent_chain.with_agents(
        vec!["agent-a".to_string(), "agent-b".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );
    // Set a session ID so session reuse is used instead of WriteTimeoutContext
    state.agent_chain.last_session_id = Some("session-123".to_string());

    state = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            "agent-a".to_string(),
            TimeoutOutputKind::PartialOutput,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    let effect = determine_next_effect(&state);

    if let Effect::PrepareDevelopmentPrompt { prompt_mode, .. } = effect {
        assert_ne!(
            prompt_mode,
            PromptMode::XsdRetry,
            "Timeout retry should not use XsdRetry prompt mode"
        );
    } else {
        panic!("Expected PrepareDevelopmentPrompt after timeout, got {effect:?}");
    }
}

#[test]
fn test_same_agent_retry_in_development_retries_analysis_when_chain_role_is_analysis() {
    // Regression: analysis runs inside Development. Same-agent retries must retry analysis,
    // not restart the developer prompt flow.
    let chain = crate::reducer::state::AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec![]],
        AgentRole::Analysis,
    );

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        total_iterations: 1,
        agent_chain: chain,
        continuation: crate::reducer::state::ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_pending: true,
            ..crate::reducer::state::ContinuationState::new()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InvokeAnalysisAgent { iteration: 0 }
    ));

    let stale_continuation_state = {
        let base = initial_with_locked_permissions(1, 0);
        PipelineState {
            phase: PipelinePhase::Development,
            iteration: 0,
            total_iterations: 1,
            development_context_prepared_iteration: Some(0),
            development_prompt_prepared_iteration: Some(0),
            development_required_files_cleaned_iteration: Some(0),
            development_agent_invoked_iteration: Some(0),
            analysis_agent_invoked_iteration: None,
            agent_chain: crate::reducer::state::AgentChainState::initial()
                .with_agents(
                    vec!["claude".to_string()],
                    vec![vec![]],
                    AgentRole::Analysis,
                )
                .with_drain(crate::agents::AgentDrain::Analysis),
            continuation: crate::reducer::state::ContinuationState {
                continue_pending: true,
                continuation_attempt: 1,
                ..crate::reducer::state::ContinuationState::default()
            },
            ..base
        }
    };

    let effect = determine_next_effect(&stale_continuation_state);
    assert!(matches!(
        effect,
        Effect::InvokeAnalysisAgent { iteration: 0 }
    ));
}

#[test]
fn test_development_initializes_analysis_chain_before_invoking_analysis() {
    // Regression: analysis invocations should honor FallbackConfig.analysis.
    let chain = crate::reducer::state::AgentChainState::initial().with_agents(
        vec!["dev-agent".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        total_iterations: 5,
        agent_chain: chain,
        development_context_prepared_iteration: Some(1),
        development_prompt_prepared_iteration: Some(1),
        development_required_files_cleaned_iteration: Some(1),
        development_agent_invoked_iteration: Some(1),
        analysis_agent_invoked_iteration: None,
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Analysis
        }
    ));
}

#[test]
fn test_development_with_agent_chain_exhaustion() {
    let mut chain = initial_with_locked_permissions(5, 2)
        .agent_chain
        .with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_max_cycles(3);
    chain = chain.start_retry_cycle();
    chain = chain.start_retry_cycle();
    chain = chain.start_retry_cycle();

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        agent_chain: chain,
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::SaveCheckpoint { .. }));
}
