// Complete pipeline flow orchestration tests.
//
// Tests for end-to-end pipeline flow: Planning -> Development -> Review ->
// Fix -> Commit -> FinalValidation -> Complete, plus edge cases like
// zero iterations.

use crate::agents::AgentRole;
use crate::common::domain_types::AgentName;
use crate::reducer::determine_next_effect;
use crate::reducer::effect::Effect;
use crate::reducer::event::DevelopmentEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::PipelinePhase;
use crate::reducer::reduce;
use crate::reducer::state::PipelineState;

#[test]
fn test_complete_pipeline_flow() {
    // Test Planning → Development → Review → Fix → Commit → FinalValidation → Complete
    let mut state = PipelineState::initial(2, 1);
    state.agent_chain = state.agent_chain.with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );

    let mut phase_sequence = Vec::new();
    let mut iterations_run = Vec::new();
    let mut review_passes_run = Vec::new();

    // This test simulates the reducer-driven pipeline loop. The exact number of effects
    // can change as we add role-specific chain initialization (Developer/Analysis/Commit),
    // so keep a generous step budget.
    let max_steps = 160;
    let mut step = 0;
    while step < max_steps {
        phase_sequence.push(state.phase);
        let effect = determine_next_effect(&state);

        match effect {
            Effect::InitializeAgentChain { drain, .. } => {
                state = reduce(
                    state,
                    PipelineEvent::agent_chain_initialized(
                        drain,
                        vec![AgentName::from("claude")],
                        vec![],
                        3,
                        1000,
                        2.0,
                        60000,
                    ),
                );
            }
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
            Effect::PrepareReviewContext { pass } => {
                review_passes_run.push(pass);
                // Simulate a pass that finds issues.
                state = reduce(state, PipelineEvent::review_context_prepared(pass));
                state = reduce(state, PipelineEvent::review_prompt_prepared(pass));
                state = reduce(state, PipelineEvent::review_issues_xml_cleaned(pass));
                state = reduce(state, PipelineEvent::review_agent_invoked(pass));
                state = reduce(state, PipelineEvent::review_issues_xml_extracted(pass));
                state = reduce(
                    state,
                    PipelineEvent::review_issues_xml_validated(
                        pass,
                        true,
                        false,
                        vec!["issue".to_string()],
                        None,
                    ),
                );
                state = reduce(state, PipelineEvent::review_issues_markdown_written(pass));
                state = reduce(state, PipelineEvent::review_issue_snippets_extracted(pass));
                state = reduce(state, PipelineEvent::review_issues_xml_archived(pass));
                state = reduce(state, PipelineEvent::review_completed(pass, true));
            }
            Effect::MaterializeReviewInputs { pass } => {
                let sig = state.agent_chain.consumer_signature_sha256();
                let plan = crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Plan,
                    content_id_sha256: "id".to_string(),
                    consumer_signature_sha256: sig.clone(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                };
                let diff = crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
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
                    PipelineEvent::review_inputs_materialized(pass, plan, diff),
                );
            }
            Effect::PrepareFixPrompt { pass, .. } => {
                state = reduce(state, PipelineEvent::fix_prompt_prepared(pass));
                state = reduce(state, PipelineEvent::fix_result_xml_cleaned(pass));
                state = reduce(state, PipelineEvent::fix_agent_invoked(pass));
                state = reduce(state, PipelineEvent::fix_result_xml_extracted(pass));
                state = reduce(
                    state,
                    PipelineEvent::fix_result_xml_validated(
                        pass,
                        crate::reducer::state::FixStatus::AllIssuesAddressed,
                        None,
                    ),
                );
                state = reduce(state, PipelineEvent::fix_result_xml_archived(pass));
                state = reduce(state, PipelineEvent::fix_outcome_applied(pass));
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
            Effect::CheckCommitDiff => {
                state = reduce(
                    state,
                    PipelineEvent::commit_diff_prepared(false, "id".to_string()),
                );
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
                    PipelineEvent::commit_xml_validated(
                        "test commit".to_string(),
                        vec![],
                        vec![],
                        1,
                    ),
                );
            }
            Effect::ApplyCommitMessageOutcome => {
                state = reduce(
                    state,
                    PipelineEvent::commit_message_generated("test commit".to_string(), 1),
                );
            }
            Effect::ArchiveCommitXml => {
                state = reduce(state, PipelineEvent::commit_xml_archived(1));
            }
            Effect::CreateCommit { .. } => {
                state = reduce(
                    state,
                    PipelineEvent::commit_created("abc123".to_string(), "test commit".to_string()),
                );
            }
            Effect::CheckUncommittedChangesBeforeTermination => {
                // Pre-termination safety check - simulate clean working directory
                state = reduce(state, PipelineEvent::pre_termination_safety_check_passed());
            }
            Effect::ValidateFinalState => {
                state = reduce(state, PipelineEvent::final_state_validation_completed());
            }
            Effect::SaveCheckpoint { .. } => {
                if state.phase == PipelinePhase::Complete {
                    break;
                }
            }
            Effect::LockPromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_locked(None));
            }
            Effect::RestorePromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_restored());
            }
            _ => panic!("Unexpected effect at step {step}: {effect:?}"),
        }

        step += 1;

        if state.phase == PipelinePhase::Complete {
            break;
        }
    }

    assert_eq!(iterations_run, vec![0, 1], "Should run 2 dev iterations");
    assert_eq!(
        review_passes_run,
        vec![0, 0],
        "Phase 2: each of 2 dev iterations runs review pass 0 before committing"
    );
    assert_eq!(state.phase, PipelinePhase::Complete);

    // Verify phase progression
    assert!(phase_sequence.contains(&PipelinePhase::Planning));
    assert!(phase_sequence.contains(&PipelinePhase::Development));
    assert!(phase_sequence.contains(&PipelinePhase::Review));
    assert!(phase_sequence.contains(&PipelinePhase::CommitMessage));
    assert!(phase_sequence.contains(&PipelinePhase::FinalValidation));
}

#[test]
fn test_pipeline_skips_planning_dev_when_zero_iterations() {
    let base = PipelineState::initial(0, 2); // 0 dev, 2 review
    assert_eq!(base.phase, PipelinePhase::Review);
    let mut state = PipelineState {
        agent_chain: base.agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        ),
        ..base
    };

    let mut review_passes = Vec::new();
    let max_steps = 30;
    let mut _step = 0;
    while _step < max_steps {
        let effect = determine_next_effect(&state);

        match effect {
            Effect::InitializeAgentChain { drain, .. } => {
                state = reduce(
                    state,
                    PipelineEvent::agent_chain_initialized(
                        drain,
                        vec![AgentName::from("claude")],
                        vec![],
                        3,
                        1000,
                        2.0,
                        60000,
                    ),
                );
            }
            Effect::PrepareReviewContext { pass } => {
                review_passes.push(pass);
                state = reduce(state, PipelineEvent::review_context_prepared(pass));
                state = reduce(state, PipelineEvent::review_prompt_prepared(pass));
                state = reduce(state, PipelineEvent::review_issues_xml_cleaned(pass));
                state = reduce(state, PipelineEvent::review_agent_invoked(pass));
                state = reduce(state, PipelineEvent::review_issues_xml_extracted(pass));
                state = reduce(
                    state,
                    PipelineEvent::review_issues_xml_validated(
                        pass,
                        false,
                        true,
                        Vec::new(),
                        Some("ok".to_string()),
                    ),
                );
                state = reduce(state, PipelineEvent::review_issues_markdown_written(pass));
                state = reduce(state, PipelineEvent::review_issue_snippets_extracted(pass));
                state = reduce(state, PipelineEvent::review_issues_xml_archived(pass));
                state = reduce(state, PipelineEvent::review_pass_completed_clean(pass));
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
            Effect::CheckCommitDiff => {
                state = reduce(
                    state,
                    PipelineEvent::commit_diff_prepared(false, "id".to_string()),
                );
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
                    PipelineEvent::commit_created("abc".to_string(), "test".to_string()),
                );
            }
            Effect::CheckUncommittedChangesBeforeTermination => {
                // Pre-termination safety check - simulate clean working directory
                state = reduce(state, PipelineEvent::pre_termination_safety_check_passed());
            }
            Effect::ValidateFinalState => {
                state = reduce(state, PipelineEvent::final_state_validation_completed());
            }
            Effect::SaveCheckpoint { .. } => {
                if state.phase == PipelinePhase::Complete {
                    break;
                }
            }
            Effect::LockPromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_locked(None));
            }
            Effect::RestorePromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_restored());
            }
            _ => panic!("Unexpected effect: {effect:?}"),
        }

        _step += 1;
    }

    assert_eq!(review_passes, vec![0, 1]);
    assert_eq!(state.phase, PipelinePhase::Complete);
}

#[test]
fn test_pipeline_goes_straight_to_commit_when_zero_work() {
    let state = PipelineState::initial(0, 0); // No dev, no review
    assert_eq!(
        state.phase,
        PipelinePhase::CommitMessage,
        "Should skip straight to commit when no work needed"
    );
}
