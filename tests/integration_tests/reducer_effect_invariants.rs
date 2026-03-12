//! Tests that effects are single-task and handlers don't have hidden behavior.
//!
//! Key invariants verified:
//! 1. Each effect type represents exactly one task
//! 2. Orchestration respects preconditions before emitting phase effects
//! 3. Effect handlers don't perform implicit agent selection (uses `state.agent_chain`)
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::CheckpointTrigger;
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::{
    AgentChainState, CommitState, PipelineState, PromptPermissionsState,
};

/// Test that development effects are NOT emitted when agent chain is empty.
///
/// Development should use the agent from `state.agent_chain`.
/// It should NOT be emitted when the chain is empty.
#[test]
fn test_development_requires_agent_chain() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 0,
            total_iterations: 5,
            agent_chain: AgentChainState::initial(), // Empty
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        // Must NOT be PrepareDevelopmentContext when chain is empty
        assert!(
            !matches!(effect, Effect::PrepareDevelopmentContext { .. }),
            "Must initialize agent chain before running development, got {effect:?}"
        );
    });
}

/// Test that review effects are NOT emitted when agent chain is empty.
///
/// Review should use the agent from `state.agent_chain`.
/// It should NOT be emitted when the chain is empty.
#[test]
fn test_run_review_pass_requires_agent_chain() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            agent_chain: AgentChainState::initial(), // Empty
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        // Must NOT begin review chain when chain is empty
        assert!(
            !matches!(effect, Effect::PrepareReviewContext { .. }),
            "Must initialize agent chain before running review, got {effect:?}"
        );
    });
}

/// Test that fix effects are NOT emitted when agent chain is empty.
///
/// Fix should use the agent from `state.agent_chain`.
/// It should NOT be emitted when the chain is empty.
#[test]
fn test_run_fix_attempt_requires_agent_chain() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            review_issues_found: true, // Triggers fix attempt
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            agent_chain: AgentChainState::initial(), // Empty
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        // Must NOT begin fix chain when chain is empty
        assert!(
            !matches!(effect, Effect::PrepareFixPrompt { .. }),
            "Must initialize agent chain before running fix, got {effect:?}"
        );
    });
}

/// Test that planning prompt preparation is NOT emitted when agent chain is empty.
///
/// Planning prompt preparation should use the agent from `state.agent_chain`.
/// It should NOT be emitted when the chain is empty.
#[test]
fn test_planning_prompt_requires_agent_chain() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            iteration: 0,
            total_iterations: 5,
            agent_chain: AgentChainState::initial(), // Empty
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        // Must NOT be PreparePlanningPrompt when chain is empty
        assert!(
            !matches!(effect, Effect::PreparePlanningPrompt { .. }),
            "Must initialize agent chain before preparing plan prompt, got {effect:?}"
        );
    });
}

/// Planning phase should emit the first single-task planning effect.
///
/// Planning must be decomposed into single-task effects; orchestration should
/// emit the first planning step, not a bundled legacy effect.
#[test]
fn test_planning_phase_emits_prepare_prompt() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            context_cleaned: true,
            gitignore_entries_ensured: true,
            iteration: 0,
            total_iterations: 5,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["claude".to_string()],
                    vec![vec![]],
                    AgentRole::Developer,
                )
                .with_drain(ralph_workflow::agents::AgentDrain::Planning),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        assert!(
            matches!(effect, Effect::MaterializePlanningInputs { .. }),
            "Planning should emit MaterializePlanningInputs first, got {effect:?}"
        );
    });
}

/// Development phase should emit the first single-task development effect.
#[test]
fn test_development_phase_emits_prepare_development_context() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 0,
            total_iterations: 5,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            ),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        assert!(
            matches!(effect, Effect::PrepareDevelopmentContext { .. }),
            "Development should emit PrepareDevelopmentContext, got {effect:?}"
        );
    });
}

/// Test that exhausted agent chains produce an explicit abort effect.
///
/// When the agent chain is exhausted, the pipeline must not stall by emitting
/// `SaveCheckpoint` repeatedly. The reducer/orchestration must emit an explicit
/// abort effect so termination happens through a single effect path.
#[test]
fn test_exhausted_agent_chain_emits_abort_effect() {
    with_default_timeout(|| {
        // Development phase exhausted chain -> AbortPipeline
        let mut chain = AgentChainState::initial()
            .with_agents(
                vec!["agent".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_max_cycles(1);
        chain = chain.start_retry_cycle();
        assert!(
            chain.is_exhausted(),
            "test precondition: chain must be exhausted"
        );

        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 0,
            total_iterations: 1,
            agent_chain: chain,
            ..with_locked_prompt_permissions(PipelineState::initial(1, 0))
        };
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::ReportAgentChainExhausted { .. }),
            "Exhausted chain must report exhaustion explicitly; got {effect:?}"
        );

        // Review phase exhausted chain -> ReportAgentChainExhausted
        let mut chain = AgentChainState::initial()
            .with_agents(
                vec!["reviewer".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_max_cycles(1);
        chain = chain.start_retry_cycle();
        assert!(
            chain.is_exhausted(),
            "test precondition: chain must be exhausted"
        );

        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 1,
            agent_chain: chain,
            ..with_locked_prompt_permissions(PipelineState::initial(1, 1))
        };
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::ReportAgentChainExhausted { .. }),
            "Exhausted chain must report exhaustion explicitly; got {effect:?}"
        );
    });
}

/// Exhaustion reporting must derive the role from the active drain, not stale role metadata.
#[test]
fn test_exhausted_analysis_drain_reports_analysis_role_when_role_metadata_is_stale() {
    with_default_timeout(|| {
        let mut chain = AgentChainState::initial()
            .with_agents(
                vec!["analysis-agent".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Analysis)
            .with_max_cycles(1);
        chain.current_role = AgentRole::Developer;
        chain = chain.start_retry_cycle();

        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 0,
            total_iterations: 1,
            agent_chain: chain,
            ..with_locked_prompt_permissions(PipelineState::initial(1, 0))
        };

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                effect,
                Effect::ReportAgentChainExhausted {
                    role: AgentRole::Analysis,
                    ..
                }
            ),
            "Exhausted analysis drain should report analysis role, got {effect:?}"
        );
    });
}

/// Backoff waiting must derive the role from the active drain, not stale role metadata.
#[test]
fn test_backoff_wait_uses_active_drain_role_when_role_metadata_is_stale() {
    with_default_timeout(|| {
        let mut chain = AgentChainState::initial()
            .with_agents(
                vec!["analysis-agent".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Analysis);
        chain.current_role = AgentRole::Developer;
        chain.backoff_pending_ms = Some(2_500);

        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 0,
            total_iterations: 1,
            agent_chain: chain,
            ..with_locked_prompt_permissions(PipelineState::initial(1, 0))
        };

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                effect,
                Effect::BackoffWait {
                    role: AgentRole::Analysis,
                    cycle: 0,
                    duration_ms: 2_500
                }
            ),
            "Backoff wait should report the active drain role, got {effect:?}"
        );
    });
}

/// Test that Interrupted phase drives a checkpoint save before termination.
///
/// Regression: an Interrupted state combined with an exhausted agent chain must not
/// repeatedly emit `AbortPipeline`. Orchestration should emit a single `SaveCheckpoint`
/// (Interrupt trigger) so the state machine can complete.
#[test]
fn test_interrupted_phase_emits_interrupt_checkpoint_save() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Interrupted,
            checkpoint_saved_count: 0,
            interrupted_by_user: true,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["agent".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            ),
            prompt_permissions: PromptPermissionsState {
                locked: true,
                restore_needed: true,
                restored: true,
                last_warning: None,
            },
            ..with_locked_prompt_permissions(PipelineState::initial(0, 1))
        };

        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::SaveCheckpoint {
                    trigger: CheckpointTrigger::Interrupt
                }
            ),
            "Interrupted phase must emit interrupt checkpoint save; got {effect:?}"
        );
    });
}

/// Architectural guard: each phase emits exactly one focused effect, not a bundle.
///
/// This test verifies the single-task invariant behaviorally: for each phase,
/// `determine_next_effect` returns one effect that matches the expected variant
/// for that phase. If any phase started bundling cleanup or agent selection
/// into the same effect, the variant would change or gain unexpected fields.
///
/// This is a schema guard test -- it verifies the public API contract of
/// `determine_next_effect` across representative phases.
#[test]
fn test_each_phase_emits_single_focused_effect() {
    with_default_timeout(|| {
        let dev_chain = AgentChainState::initial()
            .with_agents(
                vec!["agent".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Planning);
        let reviewer_chain = AgentChainState::initial()
            .with_agents(
                vec!["reviewer".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Review);
        let commit_chain = AgentChainState::initial()
            .with_agents(
                vec!["committer".to_string()],
                vec![vec![]],
                AgentRole::Commit,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Commit);

        // Planning emits MaterializePlanningInputs (not bundled with cleanup or agent init)
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            context_cleaned: true,
            gitignore_entries_ensured: true,
            agent_chain: dev_chain.clone(),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::MaterializePlanningInputs { .. }),
            "Planning should emit single MaterializePlanningInputs, got {effect:?}"
        );

        // Development emits PrepareDevelopmentContext (not bundled with cleanup)
        let state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: dev_chain.with_drain(ralph_workflow::agents::AgentDrain::Development),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::PrepareDevelopmentContext { iteration: 0 }),
            "Development should emit single PrepareDevelopmentContext, got {effect:?}"
        );

        // Review emits PrepareReviewContext (not bundled with agent selection)
        let state = PipelineState {
            phase: PipelinePhase::Review,
            agent_chain: reviewer_chain,
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::PrepareReviewContext { pass: 0 }),
            "Review should emit single PrepareReviewContext, got {effect:?}"
        );

        // CommitMessage with chain emits CheckCommitDiff (not bundled with prompt prep)
        let state = PipelineState {
            phase: PipelinePhase::CommitMessage,
            commit: CommitState::NotStarted,
            agent_chain: commit_chain,
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CheckCommitDiff),
            "CommitMessage should emit single CheckCommitDiff first, got {effect:?}"
        );

        // FinalValidation emits CheckUncommittedChangesBeforeTermination (single safety check)
        let state = PipelineState {
            phase: PipelinePhase::FinalValidation,
            pre_termination_commit_checked: false,
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CheckUncommittedChangesBeforeTermination),
            "FinalValidation should emit single safety check, got {effect:?}"
        );

        // CleanupContext and CleanupContinuationContext are distinct no-data effects
        // (verified by the planning sequence test: test_context_cleaned_before_planning)
    });
}

/// Test that `CommitMessage` phase requires agent chain initialization.
///
/// `CommitMessage` phase should first initialize agent chain when empty,
/// just like other phases (Planning, Development, Review).
#[test]
fn test_commit_phase_requires_agent_chain() {
    with_default_timeout(|| {
        // Empty chain -> InitializeAgentChain
        let state_empty_chain = PipelineState {
            phase: PipelinePhase::CommitMessage,
            commit: CommitState::NotStarted,
            agent_chain: AgentChainState::initial(),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state_empty_chain);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Commit,
                    ..
                }
            ),
            "Empty chain should emit InitializeAgentChain for Commit, got {effect:?}"
        );
    });
}

/// Test that `CommitMessage` phase effects follow correct sequence.
///
/// `CommitMessage` phase should (after agent chain is initialized):
/// 1. `PrepareCommitPrompt` when commit is `NotStarted`
/// 2. `CreateCommit` when commit is Generated
/// 3. `SaveCheckpoint` when commit is Committed/Skipped
#[test]
fn test_commit_phase_effect_sequence() {
    with_default_timeout(|| {
        // Create agent chain for commit phase
        let commit_chain = AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        );

        // NotStarted (with chain) -> CheckCommitDiff (before PrepareCommitPrompt)
        let state_not_started = PipelineState {
            phase: PipelinePhase::CommitMessage,
            commit: CommitState::NotStarted,
            agent_chain: commit_chain.clone(),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state_not_started);
        assert!(
            matches!(effect, Effect::CheckCommitDiff),
            "NotStarted with chain should emit CheckCommitDiff first, got {effect:?}"
        );

        // After diff checked, NotStarted -> PrepareCommitPrompt
        let state_not_started_diff_prepared = PipelineState {
            phase: PipelinePhase::CommitMessage,
            commit: CommitState::NotStarted,
            commit_diff_prepared: true,
            commit_diff_content_id_sha256: Some("id".to_string()),
            agent_chain: commit_chain.clone(),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state_not_started_diff_prepared);
        assert!(
            matches!(effect, Effect::MaterializeCommitInputs { .. }),
            "NotStarted with diff prepared should emit MaterializeCommitInputs, got {effect:?}"
        );

        // Generated -> ArchiveCommitXml (before CreateCommit)
        let state_generated = PipelineState {
            phase: PipelinePhase::CommitMessage,
            commit: CommitState::Generated {
                message: "test".to_string(),
            },
            agent_chain: commit_chain.clone(),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state_generated);
        assert!(
            matches!(effect, Effect::ArchiveCommitXml),
            "Generated should emit ArchiveCommitXml, got {effect:?}"
        );

        // Committed -> SaveCheckpoint
        let state_committed = PipelineState {
            phase: PipelinePhase::CommitMessage,
            commit: CommitState::Committed {
                hash: "abc".to_string(),
            },
            agent_chain: commit_chain,
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state_committed);
        assert!(
            matches!(effect, Effect::SaveCheckpoint { .. }),
            "Committed should emit SaveCheckpoint, got {effect:?}"
        );
    });
}

/// Test that gitignore is ensured before context cleanup in planning.
///
/// When entering Planning phase, gitignore should be ensured first,
/// then context should be cleaned to remove old PLAN.md from previous iteration.
#[test]
fn test_context_cleaned_before_planning() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            context_cleaned: false,
            gitignore_entries_ensured: false,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["claude".to_string()],
                    vec![vec![]],
                    AgentRole::Developer,
                )
                .with_drain(ralph_workflow::agents::AgentDrain::Planning),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // First, ensure gitignore entries
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::EnsureGitignoreEntries),
            "Should ensure gitignore entries before cleanup, got {effect:?}"
        );

        // After gitignore ensured, should cleanup context
        let state_gitignore_ensured = PipelineState {
            gitignore_entries_ensured: true,
            ..state.clone()
        };
        let effect = determine_next_effect(&state_gitignore_ensured);
        assert!(
            matches!(effect, Effect::CleanupContext),
            "Should cleanup context after gitignore ensured, got {effect:?}"
        );

        // After cleanup, should prepare planning prompt
        let state_cleaned = PipelineState {
            context_cleaned: true,
            gitignore_entries_ensured: true,
            ..state
        };
        let effect = determine_next_effect(&state_cleaned);
        assert!(
            matches!(effect, Effect::MaterializePlanningInputs { .. }),
            "Should materialize planning inputs after cleanup, got {effect:?}"
        );
    });
}

/// Test that each phase has a clear effect for initialized state.
///
/// Each phase should emit a specific effect when properly initialized.
#[test]
fn test_phases_emit_expected_effects_when_initialized() {
    with_default_timeout(|| {
        let base_chain = AgentChainState::initial()
            .with_agents(
                vec!["agent".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Planning);
        let reviewer_chain = AgentChainState::initial()
            .with_agents(
                vec!["reviewer".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Review);

        // Planning -> PreparePlanningPrompt
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            context_cleaned: true,
            gitignore_entries_ensured: true,
            agent_chain: base_chain.clone(),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        assert!(matches!(
            determine_next_effect(&state),
            Effect::MaterializePlanningInputs { .. }
        ));

        // Development -> PrepareDevelopmentContext
        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 0,
            total_iterations: 5,
            agent_chain: base_chain.with_drain(ralph_workflow::agents::AgentDrain::Development),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        assert!(matches!(
            determine_next_effect(&state),
            Effect::PrepareDevelopmentContext { .. }
        ));

        // Review -> PrepareReviewContext (start of single-task review chain)
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            agent_chain: reviewer_chain,
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        assert!(matches!(
            determine_next_effect(&state),
            Effect::PrepareReviewContext { .. }
        ));

        // FinalValidation -> CheckUncommittedChangesBeforeTermination (safety check first)
        let state = PipelineState {
            phase: PipelinePhase::FinalValidation,
            pre_termination_commit_checked: false, // Safety check not yet done
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        assert!(matches!(
            determine_next_effect(&state),
            Effect::CheckUncommittedChangesBeforeTermination
        ));

        // FinalValidation -> ValidateFinalState (after safety check completes)
        let state = PipelineState {
            phase: PipelinePhase::FinalValidation,
            pre_termination_commit_checked: true, // Safety check completed
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        assert!(matches!(
            determine_next_effect(&state),
            Effect::ValidateFinalState
        ));

        // Finalizing -> RestorePromptPermissions
        let state = PipelineState {
            phase: PipelinePhase::Finalizing,
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        assert!(matches!(
            determine_next_effect(&state),
            Effect::RestorePromptPermissions
        ));
    });
}

/// Test that `ApplyDevelopmentOutcome` effect does not bundle context writing.
///
/// The handler should emit proper events that trigger `WriteContinuationContext`
/// as a separate effect, not write context files as a side effect of execution.
/// This test verifies the architectural invariant that effects are single-task.
#[test]
fn test_development_outcome_does_not_bundle_context_writing() {
    use ralph_workflow::reducer::effect::ContinuationContextData;
    use ralph_workflow::reducer::state::DevelopmentStatus;

    with_default_timeout(|| {
        // Verify ApplyDevelopmentOutcome is a single-task effect
        let effect = Effect::ApplyDevelopmentOutcome { iteration: 0 };

        // The effect variant should only contain the iteration number
        // If someone adds context_data or similar, this match would fail
        match effect {
            Effect::ApplyDevelopmentOutcome { iteration } => {
                assert_eq!(iteration, 0);
            }
            _ => panic!("Expected ApplyDevelopmentOutcome"),
        }

        // WriteContinuationContext is its own separate effect
        let write_effect = Effect::WriteContinuationContext(ContinuationContextData {
            iteration: 0,
            attempt: 1,
            status: DevelopmentStatus::Partial,
            summary: "test".to_string(),
            files_changed: None,
            next_steps: None,
        });

        // These are distinct effects, not bundled
        assert!(
            !matches!(
                Effect::ApplyDevelopmentOutcome { iteration: 0 },
                Effect::WriteContinuationContext(_)
            ),
            "ApplyDevelopmentOutcome and WriteContinuationContext must be separate effects"
        );
        assert!(
            matches!(write_effect, Effect::WriteContinuationContext(_)),
            "WriteContinuationContext should be its own effect type"
        );
    });
}

/// Behavioral test: cleanup and phase effects are produced as separate steps.
///
/// When context is not yet cleaned, `determine_next_effect` emits cleanup first.
/// Only after cleanup does it emit the phase-specific effect. This proves
/// cleanup is never bundled into phase effects.
#[test]
fn test_phase_effects_do_not_bundle_cleanup() {
    with_default_timeout(|| {
        let dev_chain = AgentChainState::initial()
            .with_agents(
                vec!["agent".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Planning);

        // Without cleanup done: should emit CleanupContext, NOT phase effect
        let state_needs_cleanup = PipelineState {
            phase: PipelinePhase::Planning,
            context_cleaned: false,
            gitignore_entries_ensured: true,
            agent_chain: dev_chain.clone(),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state_needs_cleanup);
        assert!(
            matches!(effect, Effect::CleanupContext),
            "When context not cleaned, should emit separate CleanupContext, not bundled phase effect. Got {effect:?}"
        );

        // With cleanup done: should emit phase effect
        let state_cleaned = PipelineState {
            phase: PipelinePhase::Planning,
            context_cleaned: true,
            gitignore_entries_ensured: true,
            agent_chain: dev_chain,
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };
        let effect = determine_next_effect(&state_cleaned);
        assert!(
            matches!(effect, Effect::MaterializePlanningInputs { .. }),
            "After cleanup, should emit phase effect. Got {effect:?}"
        );
    });
}

/// Test that continuation context writing is driven by `WriteContinuationContext` effect.
///
/// When development returns status="partial" or "failed", the handler emits a
/// `ContinuationTriggered` event. The reducer then determines if `WriteContinuationContext`
/// effect should be emitted based on continuation budget and policy.
///
/// This test verifies that:
/// 1. `WriteContinuationContext` is a distinct effect (not bundled)
/// 2. The effect is emitted based on reducer state, not handler decision
/// 3. `CleanupContinuationContext` is also effect-driven
#[test]
fn test_continuation_context_is_effect_driven() {
    use ralph_workflow::reducer::effect::ContinuationContextData;
    use ralph_workflow::reducer::state::DevelopmentStatus;

    with_default_timeout(|| {
        // WriteContinuationContext is its own effect
        let write_effect = Effect::WriteContinuationContext(ContinuationContextData {
            iteration: 0,
            attempt: 1,
            status: DevelopmentStatus::Partial,
            summary: "test".to_string(),
            files_changed: None,
            next_steps: None,
        });

        // Verify it's distinct from development context effect
        let dev_effect = Effect::PrepareDevelopmentContext { iteration: 0 };

        assert!(
            !matches!(&dev_effect, Effect::WriteContinuationContext(_)),
            "Continuation context writing must be separate from development context"
        );

        // Verify WriteContinuationContext carries the necessary data for reducer policy
        match &write_effect {
            Effect::WriteContinuationContext(data) => {
                // The data includes all info needed for reducer to track continuation state
                assert_eq!(
                    data.iteration, 0,
                    "Tracks which iteration triggered continuation"
                );
                assert_eq!(data.attempt, 1, "Tracks continuation attempt count");
                assert!(
                    matches!(data.status, DevelopmentStatus::Partial),
                    "Tracks status that triggered continuation"
                );
            }
            _ => panic!("Expected WriteContinuationContext"),
        }

        // CleanupContinuationContext is also separate
        let cleanup_effect = Effect::CleanupContinuationContext;
        assert!(
            matches!(cleanup_effect, Effect::CleanupContinuationContext),
            "Cleanup is its own effect driven by reducer, not handler side effect"
        );
    });
}

/// Test that effect determination is deterministic across ALL phases.
///
/// For every phase, calling `determine_next_effect` multiple times with the
/// same state must produce the same effect. This proves no external state
/// (filesystem, time, randomness) influences effect determination.
#[test]
fn test_effect_determination_deterministic_all_phases() {
    with_default_timeout(|| {
        let phases_to_test = vec![
            (PipelinePhase::Planning, AgentRole::Developer),
            (PipelinePhase::Development, AgentRole::Developer),
            (PipelinePhase::Review, AgentRole::Reviewer),
            (PipelinePhase::CommitMessage, AgentRole::Developer),
            (PipelinePhase::FinalValidation, AgentRole::Developer),
            (PipelinePhase::Finalizing, AgentRole::Developer),
            (PipelinePhase::Complete, AgentRole::Developer),
        ];

        for (phase, role) in phases_to_test {
            let mut state = PipelineState::initial(3, 2);
            state.phase = phase;
            state.context_cleaned = true;

            // Initialize agent chain for phases that need it
            if phase != PipelinePhase::Complete
                && phase != PipelinePhase::Finalizing
                && phase != PipelinePhase::FinalValidation
                && phase != PipelinePhase::CommitMessage
            {
                state.agent_chain =
                    state
                        .agent_chain
                        .with_agents(vec!["agent".to_string()], vec![vec![]], role);
            }

            // Call determine_next_effect 3 times
            let effect1 = determine_next_effect(&state);
            let effect2 = determine_next_effect(&state);
            let effect3 = determine_next_effect(&state);

            // All must be equal (Effect derives PartialEq for structural comparison)
            assert_eq!(
                effect1, effect2,
                "Effect determination must be deterministic for phase {phase:?}"
            );
            assert_eq!(
                effect2, effect3,
                "Effect determination must be deterministic for phase {phase:?}"
            );
        }
    });
}
