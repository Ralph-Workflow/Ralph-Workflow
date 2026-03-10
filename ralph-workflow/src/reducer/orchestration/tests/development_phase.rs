// Development phase tests.
//
// Tests for development phase effect determination, agent chain states,
// and iteration counting.

use super::*;

#[test]
fn test_determine_effect_development_phase_empty_chain() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        agent_chain: AgentChainState::initial(),
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            role: AgentRole::Developer
        }
    ));
}

#[test]
fn test_determine_effect_development_phase_exhausted_chain() {
    let mut chain = AgentChainState::initial()
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

#[test]
fn test_determine_effect_exhausted_chain_after_checkpoint_aborts() {
    let mut chain = AgentChainState::initial()
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
        checkpoint_saved_count: 1,
        agent_chain: chain,
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ReportAgentChainExhausted { .. }));
}

#[test]
fn test_determine_effect_development_phase_with_chain() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        total_iterations: 5,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareDevelopmentContext { .. }));
}

#[test]
fn test_same_agent_retry_in_development_retries_analysis_when_chain_role_is_analysis() {
    // Regression: analysis runs during Development phase. If the analysis agent times out or
    // otherwise fails in a same-agent-retryable way, orchestration must retry the analysis
    // invocation (not restart the developer prompt flow).
    let mut chain = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec![]],
        AgentRole::Analysis,
    );
    // Ensure chain is not considered exhausted.
    chain = chain.with_max_cycles(3);

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
}

#[test]
fn test_development_initializes_analysis_chain_before_invoking_analysis() {
    // Regression: Analysis has its own fallback chain (FallbackConfig.analysis). The developer
    // chain must not be reused for analysis invocations.
    let chain = AgentChainState::initial().with_agents(
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
            role: AgentRole::Analysis
        }
    ));
}

#[test]
fn test_determine_effect_development_complete() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 6,
        total_iterations: 5,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::SaveCheckpoint { .. }));
}

#[test]
fn test_development_runs_exactly_n_iterations() {
    // When total_iterations=5, should run iterations 0,1,2,3,4 (5 total)
    let mut state = PipelineState::initial(5, 0);
    state.agent_chain = state.agent_chain.with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );

    // Track which iterations actually run
    let mut iterations_run = Vec::new();

    // Simulate the development phase
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
                // Context cleanup before planning
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
                    PipelineEvent::Development(
                        crate::reducer::event::DevelopmentEvent::AnalysisAgentInvoked { iteration },
                    ),
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
                state = reduce(
                    state,
                    PipelineEvent::development_iteration_completed(iteration, true),
                );
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
            Effect::SaveCheckpoint { .. } => {
                // Phase complete
                break;
            }
            Effect::InitializeAgentChain { role } => {
                state = reduce(
                    state,
                    PipelineEvent::agent_chain_initialized(
                        role,
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
            _ => panic!("Unexpected effect: {effect:?}"),
        }
    }

    // Should run exactly 5 iterations (0,1,2,3,4), not 6 (0,1,2,3,4,5)
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
fn test_resume_at_final_iteration_should_run_development_not_skip() {
    // BUG REPRODUCTION: When checkpoint saved at iteration=1, total=1
    // and all progress flags are None (reset on resume),
    // orchestration should derive development work effects,
    // NOT SaveCheckpoint (which would skip to Review phase).

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        total_iterations: 1,
        agent_chain: PipelineState::initial(1, 0).agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        // All progress flags None - simulating resume state
        development_context_prepared_iteration: None,
        development_prompt_prepared_iteration: None,
        development_required_files_cleaned_iteration: None,
        development_agent_invoked_iteration: None,
        analysis_agent_invoked_iteration: None,
        development_xml_extracted_iteration: None,
        development_validated_outcome: None,
        development_xml_archived_iteration: None,
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    // CRITICAL: Should derive development work, NOT phase transition
    // This test verifies the fix: previously would fail, now passes
    assert!(
        matches!(effect, Effect::PrepareDevelopmentContext { .. }),
        "Expected PrepareDevelopmentContext, got {effect:?}"
    );
}

#[test]
fn test_resume_iteration_0_total_1_should_run_development() {
    // Edge case: iteration=0, total=1
    // 0 < 1 is true, so this case may already work
    // But include it to verify boundary behavior

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        total_iterations: 1,
        agent_chain: PipelineState::initial(1, 0).agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        development_context_prepared_iteration: None,
        development_agent_invoked_iteration: None,
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::PrepareDevelopmentContext { .. }),
        "Expected PrepareDevelopmentContext for iteration 0, got {effect:?}"
    );
}

#[test]
fn test_timeout_context_write_derived_before_same_agent_retry() {
    // AC-1b: When a timeout with partial output occurs but the agent has no session ID,
    // orchestration must derive WriteTimeoutContext BEFORE the same-agent retry prompt.
    //
    // This test verifies the priority order:
    // 1. timeout_context_write_pending=true -> WriteTimeoutContext
    // 2. same_agent_retry_pending=true -> SameAgentRetry effect
    //
    // Without this ordering, the retry prompt would be prepared without the context file path,
    // and the agent would lose its partial progress.

    let mut chain = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );
    chain = chain.with_max_cycles(3);

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        total_iterations: 1,
        agent_chain: chain,
        continuation: crate::reducer::state::ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_pending: true,
            // CRITICAL: This flag must trigger WriteTimeoutContext, NOT SameAgentRetry
            timeout_context_write_pending: true,
            timeout_context_file_path: Some(".agent/logs/developer_1.log".to_string()),
            ..crate::reducer::state::ContinuationState::new()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    // MUST derive WriteTimeoutContext, NOT PrepareDevelopmentPrompt with SameAgentRetry mode
    assert!(
        matches!(
            effect,
            Effect::WriteTimeoutContext {
                role: AgentRole::Developer,
                logfile_path: _,
                context_path: _,
            }
        ),
        "Expected WriteTimeoutContext when timeout_context_write_pending=true, got {effect:?}"
    );
}

#[test]
fn test_timeout_context_write_uses_correct_paths() {
    // Verify that WriteTimeoutContext effect uses:
    // - logfile_path from continuation.timeout_context_file_path (set by reducer)
    // - context_path generated based on retry attempt number

    let mut chain = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );
    chain = chain.with_max_cycles(3);

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        total_iterations: 1,
        agent_chain: chain,
        continuation: crate::reducer::state::ContinuationState {
            same_agent_retry_count: 2,
            timeout_context_write_pending: true,
            timeout_context_file_path: Some(".agent/logs/developer_1_a1.log".to_string()),
            ..crate::reducer::state::ContinuationState::new()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    if let Effect::WriteTimeoutContext {
        logfile_path,
        context_path,
        ..
    } = effect
    {
        // logfile_path must match what reducer stored
        assert_eq!(
            logfile_path, ".agent/logs/developer_1_a1.log",
            "logfile_path should match timeout_context_file_path from continuation state"
        );
        // context_path should be generated based on retry count
        assert!(
            context_path.contains("timeout_context_2"),
            "context_path should contain retry count, got: {context_path}"
        );
    } else {
        panic!("Expected WriteTimeoutContext effect, got {effect:?}");
    }
}

#[test]
fn test_completed_final_iteration_should_transition_not_rerun() {
    // Verify: When iteration=total AND work is actually done
    // (development_xml_archived_iteration is Some),
    // orchestration should transition to next phase, not re-run work.
    use crate::reducer::state::DevelopmentStatus;
    use crate::reducer::state::DevelopmentValidatedOutcome;

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        total_iterations: 1,
        agent_chain: PipelineState::initial(1, 0).agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        // All progress flags set - work is DONE
        development_context_prepared_iteration: Some(1),
        development_prompt_prepared_iteration: Some(1),
        development_required_files_cleaned_iteration: Some(1),
        development_agent_invoked_iteration: Some(1),
        analysis_agent_invoked_iteration: Some(1),
        development_xml_extracted_iteration: Some(1),
        development_validated_outcome: Some(DevelopmentValidatedOutcome {
            iteration: 1,
            status: DevelopmentStatus::Completed,
            summary: "Test complete".to_string(),
            files_changed: None,
            next_steps: None,
        }),
        development_xml_archived_iteration: Some(1),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    // Should derive ApplyDevelopmentOutcome (next step after archiving)
    // NOT re-run development work
    assert!(
        matches!(effect, Effect::ApplyDevelopmentOutcome { .. }),
        "Expected ApplyDevelopmentOutcome for completed iteration, got {effect:?}"
    );
}

#[test]
fn test_resume_at_final_iteration_with_partial_progress_continues() {
    // Edge case: agent was invoked but iteration not fully archived
    // This simulates a crash after agent invocation but before XML archival

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        total_iterations: 1,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        development_agent_invoked_iteration: Some(1),
        development_xml_archived_iteration: None, // Not archived yet
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    // Should NOT skip to SaveCheckpoint - should continue processing
    // The next effect should be InvokeAnalysisAgent or ExtractDevelopmentXml
    assert!(
        !matches!(effect, Effect::SaveCheckpoint { .. }),
        "Should not SaveCheckpoint with partial progress, got {effect:?}"
    );
}

#[test]
fn test_resume_at_iteration_zero_with_total_one_runs_work() {
    // Boundary case: iteration=0, total_iterations=1
    // This is the first (and only) iteration - should run

    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        total_iterations: 1,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        development_agent_invoked_iteration: None,
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    // iteration < total_iterations (0 < 1), so should run
    assert!(
        matches!(effect, Effect::PrepareDevelopmentContext { iteration: 0 }),
        "Expected PrepareDevelopmentContext but got {effect:?}"
    );
}
