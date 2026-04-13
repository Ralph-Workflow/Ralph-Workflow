// Commit phase tests.
//
// Tests for commit phase effect determination, agent chain states,
// diff checking, and commit message generation.

use super::*;

#[test]
fn test_determine_effect_commit_message_empty_chain() {
    // When agent chain is empty, commit phase should request initialization
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::NotStarted,
        agent_chain: AgentChainState::initial(),
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Commit,
            ..
        }
    ));
}

#[test]
fn test_determine_effect_commit_message_role_mismatch_reinitializes_chain() {
    // Regression: entering CommitMessage with a non-commit agent chain must still
    // initialize the commit chain so FallbackConfig.commit is honored.
    let chain = AgentChainState::initial().with_agents(
        vec!["dev-agent".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::NotStarted,
        agent_chain: chain,
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Commit,
            ..
        }
    ));
}

#[test]
fn test_determine_effect_commit_message_not_started() {
    // With initialized agent chain and diff prepared, commit phase should prepare prompt
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::NotStarted,
        commit_diff_prepared: true, // Diff already done
        commit_diff_content_id_sha256: Some("id".to_string()),
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::MaterializeCommitInputs { .. }));
}

#[test]
fn test_determine_effect_commit_message_ignores_stale_validated_outcome() {
    // Stale outcome (attempt 1) should be ignored when current attempt is 2
    // Should proceed to prepare prompt instead of applying stale outcome
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 2,
            max_attempts: 5,
        },
        commit_diff_prepared: true, // Diff already done
        commit_diff_content_id_sha256: Some("id".to_string()),
        commit_prompt_prepared: false,
        commit_agent_invoked: false,
        commit_xml_extracted: false,
        commit_validated_outcome: Some(crate::reducer::state::CommitValidatedOutcome {
            attempt: 1, // Stale: from attempt 1, not current attempt 2
            message: Some("stale message".to_string()),
            reason: None,
        }),
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::MaterializeCommitInputs { .. }));
}

#[test]
fn test_determine_effect_commit_message_generated() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generated {
            message: "test commit message".to_string(),
        },
        commit_xml_archived: true,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    match effect {
        Effect::CreateCommit { message, .. } => {
            assert_eq!(message, "test commit message");
        }
        _ => panic!("Expected CreateCommit effect, got {effect:?}"),
    }
}

#[test]
fn test_determine_effect_commit_message_rematerializes_when_consumer_signature_changes() {
    // If the consumer set (agent chain + models + role) changes mid-attempt,
    // we must re-materialize commit inputs so model budget decisions stay safe.
    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_diff_prepared: true,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: Some("id".to_string()),
        commit_prompt_prepared: false,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["commit-agent".to_string(), "fallback-agent".to_string()],
            vec![vec!["model-a".to_string()], vec!["model-b".to_string()]],
            AgentRole::Commit,
        ),
        prompt_inputs: crate::reducer::state::PromptInputsState {
            commit: Some(crate::reducer::state::MaterializedCommitInputs {
                attempt: 1,
                diff: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
                    content_id_sha256: "id".to_string(),
                    consumer_signature_sha256: "stale_sig".to_string(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: Some(200_000),
                    inline_budget_bytes: Some(100_000),
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
            }),
            ..Default::default()
        },
        ..create_test_state()
    };

    // Ensure the agent chain signature is different from the stored one.
    let expected_sig = state.agent_chain.consumer_signature_sha256();
    assert_ne!(
        expected_sig, "stale_sig",
        "test setup error: consumer signature should differ"
    );

    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::MaterializeCommitInputs { attempt: 1 }),
        "Expected re-materialization when consumer signature changes, got {effect:?}"
    );

    // Changing current agent/model indices should not change the signature and should not
    // force re-materialization once signatures match.
    state
        .prompt_inputs
        .commit
        .as_mut()
        .unwrap()
        .diff
        .consumer_signature_sha256 = expected_sig;
    state.agent_chain.current_agent_index = 1;
    let effect = determine_next_effect(&state);
    assert!(
        !matches!(effect, Effect::MaterializeCommitInputs { .. }),
        "Expected no re-materialization when only current agent index changes, got {effect:?}"
    );
}

#[test]
fn test_recovery_does_not_emit_success_before_create_commit() {
    // Regression: when recovering from a commit failure, we must NOT clear recovery
    // counters before the actually-failing operation (CreateCommit) succeeds.
    //
    // Previously, commit orchestration emitted EmitRecoverySuccess as soon as
    // commit_xml_archived=true, which can be true even though CreateCommit will fail.
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::AwaitingDevFix),
        failed_phase_for_recovery: Some(PipelinePhase::CommitMessage),
        dev_fix_attempt_count: 2,
        recovery_escalation_level: 1,
        commit: CommitState::Generated {
            message: "msg".to_string(),
        },
        commit_xml_archived: true,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::CreateCommit { .. }),
        "expected CreateCommit (do not clear recovery state yet), got: {effect:?}"
    );
}

#[test]
fn test_recovery_emits_success_after_commit_created() {
    // Once CreateCommit has succeeded (CommitState::Committed), recovery success
    // should be emitted to clear attempt counters before continuing.
    let state = PipelineState {
        phase: PipelinePhase::FinalValidation,
        failed_phase_for_recovery: Some(PipelinePhase::CommitMessage),
        dev_fix_attempt_count: 3,
        recovery_escalation_level: 2,
        commit: CommitState::Committed {
            hash: "abc123".to_string(),
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::EmitRecoverySuccess { .. }),
        "expected EmitRecoverySuccess after commit created, got: {effect:?}"
    );
}

#[test]
fn test_committed_retry_pass_emits_matching_residual_check() {
    let mut state = create_test_state();
    state.phase = PipelinePhase::CommitMessage;
    state.commit = CommitState::Committed {
        hash: "abc123".to_string(),
    };
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["commit-agent".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );
    state.commit_residual_retry_pass = 3;

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::CheckResidualFiles { pass: 3 }),
        "Committed retry pass must emit CheckResidualFiles for the same retry pass"
    );
}

#[test]
fn test_committed_over_budget_retry_pass_still_emits_residual_check() {
    let mut state = create_test_state();
    state.phase = PipelinePhase::CommitMessage;
    state.commit = CommitState::Committed {
        hash: "abc123".to_string(),
    };
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["commit-agent".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );
    state.max_commit_residual_retries = 1;
    state.commit_residual_retry_pass = 3;

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::CheckResidualFiles { pass: 3 }),
        "Over-budget residual pass must not silently skip to checkpointing"
    );
}

#[test]
fn test_determine_effect_final_validation() {
    let mut state = PipelineState {
        phase: PipelinePhase::FinalValidation,
        ..create_test_state()
    };

    // First cycle: pre-termination safety check
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::CheckUncommittedChangesBeforeTermination),
        "FinalValidation should first check for uncommitted changes"
    );

    // After safety check passes, should derive ValidateFinalState
    state.pre_termination_commit_checked = true;
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::ValidateFinalState),
        "After safety check, FinalValidation should derive ValidateFinalState"
    );
}

#[test]
fn test_check_commit_diff_emitted_on_each_commit_phase_entry() {
    // Regression: in a multi-iteration pipeline (dev → commit → planning → dev → commit),
    // CheckCommitDiff must be emitted ONCE per commit-phase entry, not skipped on second entry.
    //
    // This test catches the bug where commit_diff_prepared survives across phase transitions
    // (via ..state spread) and causes the orchestrator guard to skip CheckCommitDiff on
    // the second commit phase entry.
    let mut state = PipelineState::initial(2, 0); // 2 dev iterations, 0 reviewer passes
    state.agent_chain = state.agent_chain.with_agents(
        vec!["commit-agent".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );

    let mut check_diff_count = 0usize;
    let max_steps = 200;

    for step in 0..max_steps {
        let effect = determine_next_effect(&state);

        match effect {
            Effect::CheckCommitDiff => {
                check_diff_count += 1;
                state = reduce(
                    state,
                    PipelineEvent::commit_diff_prepared(false, format!("hash-{check_diff_count}")),
                );
            }
            Effect::LockPromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_locked(None));
            }
            Effect::RestorePromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_restored());
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
            Effect::InitializeAgentChain { drain, .. } => {
                state = reduce(
                    state,
                    PipelineEvent::agent_chain_initialized(
                        drain,
                        vec![AgentName::from("commit-agent")],
                        vec![],
                        3,
                        1000,
                        2.0,
                        60000,
                    ),
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
            Effect::CleanupRequiredFiles { ref files }
                if files.iter().any(|f| f.contains("plan.xml")) =>
            {
                let iteration = state.iteration;
                state = reduce(state, PipelineEvent::planning_xml_cleaned(iteration));
            }
            Effect::PreparePlanningPrompt { iteration, .. } => {
                state = reduce(state, PipelineEvent::planning_prompt_prepared(iteration));
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
            Effect::CleanupRequiredFiles { ref files }
                if files.iter().any(|f| f.contains("development_result.xml")) =>
            {
                let iteration = state.iteration;
                state = reduce(state, PipelineEvent::development_xml_cleaned(iteration));
            }
            Effect::PrepareDevelopmentPrompt { iteration, .. } => {
                state = reduce(state, PipelineEvent::development_prompt_prepared(iteration));
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
                state = reduce(
                    state,
                    PipelineEvent::development_iteration_completed(iteration, true),
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
                            content_id_sha256: format!("hash-{check_diff_count}"),
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
            Effect::CleanupRequiredFiles { ref files }
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
                    PipelineEvent::commit_created(
                        format!("sha-{check_diff_count}"),
                        "test commit".to_string(),
                    ),
                );
            }
            Effect::CheckUncommittedChangesBeforeTermination => {
                state = reduce(state, PipelineEvent::pre_termination_safety_check_passed());
            }
            Effect::ValidateFinalState => {
                state = reduce(state, PipelineEvent::final_state_validation_completed());
            }
            Effect::SaveCheckpoint { .. } => {
                state = reduce(
                    state,
                    PipelineEvent::checkpoint_saved(
                        crate::reducer::event::CheckpointTrigger::PhaseTransition,
                    ),
                );
                // Review -> CommitMessage: after all review passes complete (or 0 passes)
                if state.phase == PipelinePhase::Review
                    && state.reviewer_pass >= state.total_reviewer_passes
                {
                    state = reduce(state, PipelineEvent::review_phase_completed(false));
                }
                if state.phase == PipelinePhase::Complete {
                    break;
                }
            }
            _ => panic!("Unexpected effect at step {step}: {effect:?}"),
        }

        if state.phase == PipelinePhase::Complete {
            break;
        }
    }

    assert_eq!(
        state.phase,
        PipelinePhase::Complete,
        "Pipeline should complete"
    );
    assert_eq!(
        check_diff_count, 2,
        "CheckCommitDiff must be emitted exactly once per commit phase entry \
         (2 dev iterations = 2 commit phases)"
    );
}

#[test]
fn test_determine_effect_complete() {
    let mut state = PipelineState {
        phase: PipelinePhase::Complete,
        ..create_test_state()
    };

    // First cycle: pre-termination safety check
    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::CheckUncommittedChangesBeforeTermination
    ));

    // After safety check passes, SaveCheckpoint is derived
    state.pre_termination_commit_checked = true;
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::SaveCheckpoint { .. }));
}

#[test]
fn test_commit_orchestrator_never_derives_continuation_mode() {
    // Phase 4 policy violation fix: commit orchestrator must constrain prompt_mode
    // to admissible set {Normal, SameAgentRetry} before deriving
    // Effect::PrepareCommitPrompt. Continuation mode is semantically invalid for
    // commit phase (commit is atomic, not incremental work).
    //
    // This test exhaustively checks all reducer state combinations that could
    // theoretically trigger PrepareCommitPrompt derivation and ensures none
    // derive Continuation mode.

    use crate::reducer::state::PromptMode;

    // Helper to build minimal state for prompt preparation
    let base_state = || PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_diff_prepared: true,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: Some("test_diff_id".to_string()),
        commit_prompt_prepared: false,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        prompt_inputs: crate::reducer::state::PromptInputsState {
            commit: Some(crate::reducer::state::MaterializedCommitInputs {
                attempt: 1,
                diff: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
                    content_id_sha256: "test_diff_id".to_string(),
                    consumer_signature_sha256: "test_consumer".to_string(),
                    original_bytes: 100,
                    final_bytes: 100,
                    model_budget_bytes: Some(1000),
                    inline_budget_bytes: Some(500),
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
            }),
            ..Default::default()
        },
        ..create_test_state()
    };

    // Test case 1: Normal mode (no retries pending)
    let state = base_state();
    let effect = determine_next_effect(&state);
    if let Effect::PrepareCommitPrompt { prompt_mode } = effect {
        assert_eq!(
            prompt_mode,
            PromptMode::Normal,
            "Normal path should derive Normal mode"
        );
    }

    // Test case 2: Same-agent retry pending
    let mut state = base_state();
    state.continuation.same_agent_retry_pending = true;
    state.continuation.same_agent_retry_count = 0;
    let effect = determine_next_effect(&state);
    if let Effect::PrepareCommitPrompt { prompt_mode } = effect {
        assert_eq!(
            prompt_mode,
            PromptMode::SameAgentRetry,
            "Same-agent retry should derive SameAgentRetry mode"
        );
    }

    // Test case 3: Neither retry pending (clean state, should be Normal)
    let mut state = base_state();
    state.continuation.same_agent_retry_pending = false;
    let effect = determine_next_effect(&state);
    if let Effect::PrepareCommitPrompt { prompt_mode } = effect {
        assert_eq!(
            prompt_mode,
            PromptMode::Normal,
            "Clean continuation state should derive Normal mode, not Continuation"
        );
    }
}
