//! Effect single-task verification tests and phase module control flow tests.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;

// ============================================================================
// EFFECT SINGLE-TASK VERIFICATION TESTS
// ============================================================================

/// Test that all Effect variants represent single logical operations.
///
/// This test uses an exhaustive match (no wildcard) to ensure new variants
/// are explicitly considered. It verifies each variant maps to exactly one
/// observable operation without creating a parallel classification taxonomy.
#[test]
fn test_effects_are_single_task() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::effect::{ContinuationContextData, Effect};
    use ralph_workflow::reducer::event::{CheckpointTrigger, ConflictStrategy, RebasePhase};
    use ralph_workflow::reducer::state::DevelopmentStatus;

    with_default_timeout(|| {
        // Construct one instance of every Effect variant. The exhaustive match below
        // will fail to compile if a new variant is added, forcing the developer to
        // explicitly add it here and confirm it represents a single operation.
        let effects: Vec<Effect> = vec![
            Effect::AgentInvocation {
                role: AgentRole::Developer,
                agent: "test".into(),
                model: None,
                prompt: "test".to_string(),
            },
            Effect::InitializeAgentChain {
                drain: ralph_workflow::agents::AgentDrain::Development,
            },
            Effect::PreparePlanningPrompt {
                iteration: 0,
                prompt_mode: ralph_workflow::reducer::state::PromptMode::Normal,
            },
            Effect::MaterializePlanningInputs { iteration: 0 },
            Effect::CleanupRequiredFiles {
                files: vec!["plan.xml".to_string()].into_boxed_slice(),
            },
            Effect::InvokePlanningAgent { iteration: 0 },
            Effect::ExtractPlanningXml { iteration: 0 },
            Effect::ValidatePlanningXml { iteration: 0 },
            Effect::WritePlanningMarkdown { iteration: 0 },
            Effect::ArchivePlanningXml { iteration: 0 },
            Effect::ApplyPlanningOutcome {
                iteration: 0,
                valid: true,
            },
            Effect::PrepareDevelopmentContext { iteration: 0 },
            Effect::MaterializeDevelopmentInputs { iteration: 0 },
            Effect::PrepareDevelopmentPrompt {
                iteration: 0,
                prompt_mode: ralph_workflow::reducer::state::PromptMode::Normal,
            },
            Effect::CleanupRequiredFiles {
                files: vec!["development_result.xml".to_string()].into_boxed_slice(),
            },
            Effect::InvokeDevelopmentAgent { iteration: 0 },
            Effect::InvokeAnalysisAgent { iteration: 0 },
            Effect::ExtractDevelopmentXml { iteration: 0 },
            Effect::ValidateDevelopmentXml { iteration: 0 },
            Effect::ApplyDevelopmentOutcome { iteration: 0 },
            Effect::ArchiveDevelopmentXml { iteration: 0 },
            Effect::PrepareReviewContext { pass: 0 },
            Effect::MaterializeReviewInputs { pass: 0 },
            Effect::PrepareReviewPrompt {
                pass: 0,
                prompt_mode: ralph_workflow::reducer::state::PromptMode::Normal,
            },
            Effect::CleanupRequiredFiles {
                files: vec!["issues.xml".to_string()].into_boxed_slice(),
            },
            Effect::InvokeReviewAgent { pass: 0 },
            Effect::ExtractReviewIssuesXml { pass: 0 },
            Effect::ValidateReviewIssuesXml { pass: 0 },
            Effect::WriteIssuesMarkdown { pass: 0 },
            Effect::ExtractReviewIssueSnippets { pass: 0 },
            Effect::ArchiveReviewIssuesXml { pass: 0 },
            Effect::ApplyReviewOutcome {
                pass: 0,
                issues_found: false,
                clean_no_issues: true,
            },
            Effect::PrepareFixPrompt {
                pass: 0,
                prompt_mode: ralph_workflow::reducer::state::PromptMode::Normal,
            },
            Effect::CleanupRequiredFiles {
                files: vec!["fix_result.xml".to_string()].into_boxed_slice(),
            },
            Effect::InvokeFixAgent { pass: 0 },
            Effect::InvokeFixAnalysisAgent { pass: 0 },
            Effect::ExtractFixResultXml { pass: 0 },
            Effect::ValidateFixResultXml { pass: 0 },
            Effect::ApplyFixOutcome { pass: 0 },
            Effect::ArchiveFixResultXml { pass: 0 },
            Effect::RunRebase {
                phase: RebasePhase::Initial,
                target_branch: "main".to_string(),
            },
            Effect::ResolveRebaseConflicts {
                strategy: ConflictStrategy::Abort,
            },
            Effect::CheckCommitDiff,
            Effect::MaterializeCommitInputs { attempt: 1 },
            Effect::PrepareCommitPrompt {
                prompt_mode: ralph_workflow::reducer::state::PromptMode::Normal,
            },
            Effect::CleanupRequiredFiles {
                files: vec!["commit_message.xml".to_string()].into_boxed_slice(),
            },
            Effect::InvokeCommitAgent,
            Effect::ExtractCommitXml,
            Effect::ValidateCommitXml,
            Effect::ApplyCommitMessageOutcome,
            Effect::ArchiveCommitXml,
            Effect::CreateCommit {
                message: "test".to_string(),
                files: vec![],
                excluded_files: vec![],
            },
            Effect::SkipCommit {
                reason: "test".to_string(),
            },
            Effect::PushToRemote {
                remote: "origin".to_string(),
                branch: "main".to_string(),
                force: false,
                commit_sha: "abc123".to_string(),
            },
            Effect::CreatePullRequest {
                base_branch: "main".to_string(),
                head_branch: "feature".to_string(),
                title: "test".to_string(),
                body: "test body".to_string(),
            },
            Effect::BackoffWait {
                role: AgentRole::Developer,
                cycle: 1,
                duration_ms: 1,
            },
            Effect::ReportAgentChainExhausted {
                role: AgentRole::Developer,
                phase: ralph_workflow::reducer::event::PipelinePhase::Development,
                cycle: 1,
            },
            Effect::ValidateFinalState,
            Effect::SaveCheckpoint {
                trigger: CheckpointTrigger::PhaseTransition,
            },
            Effect::EnsureGitignoreEntries,
            Effect::CleanupContext,
            Effect::ConfigureGitAuth {
                auth_method: "token:oauth2".to_string(),
            },
            Effect::LockPromptPermissions,
            Effect::RestorePromptPermissions,
            Effect::WriteContinuationContext(ContinuationContextData {
                iteration: 0,
                attempt: 0,
                status: DevelopmentStatus::Completed,
                summary: "test".to_string(),
                files_changed: None,
                next_steps: None,
            }),
            Effect::CleanupContinuationContext,
            Effect::TriggerDevFixFlow {
                failed_phase: ralph_workflow::reducer::event::PipelinePhase::Development,
                failed_role: AgentRole::Developer,
                retry_cycle: 1,
            },
            Effect::TriggerLoopRecovery {
                detected_loop: "test-loop".to_string(),
                loop_count: 2,
            },
            Effect::EmitRecoveryReset {
                reset_type: ralph_workflow::reducer::effect::RecoveryResetType::PhaseStart,
                target_phase: ralph_workflow::reducer::event::PipelinePhase::Development,
            },
            Effect::AttemptRecovery {
                level: 1,
                attempt_count: 1,
            },
            Effect::EmitRecoverySuccess {
                level: 1,
                total_attempts: 1,
            },
            Effect::EmitCompletionMarkerAndTerminate {
                is_failure: true,
                reason: Some("test".to_string()),
            },
            Effect::CheckUncommittedChangesBeforeTermination,
            Effect::CheckResidualFiles { pass: 1 },
            // Phase 4: Parallel worker effects
            Effect::EvaluateParallelPlan {
                plan: ralph_workflow::agents::session::ParallelPlan {
                    parent_plan_id: "test-plan".to_string(),
                    work_units: vec![],
                },
            },
            Effect::DispatchParallelWorkers {
                plan: ralph_workflow::agents::session::ParallelPlan {
                    parent_plan_id: "test-plan".to_string(),
                    work_units: vec![],
                },
            },
            Effect::WriteTimeoutContext {
                role: AgentRole::Developer,
                logfile_path: "test.log".to_string(),
                context_path: ".agent/tmp/timeout_context_1.txt".to_string(),
            },
            Effect::InvokeParallelVerifier {
                plan: ralph_workflow::agents::session::ParallelPlan {
                    parent_plan_id: "test-plan".to_string(),
                    work_units: vec![],
                },
                worker_results: vec![],
                iteration: 0,
            },
            Effect::CheckNetworkConnectivity,
            Effect::PollForConnectivity { interval_ms: 5000 },
        ];

        // Exhaustive match guard: forces compile error when new variants are added.
        // Each arm maps to a single-word description confirming single-responsibility.
        // No parallel enum needed - the exhaustive match IS the compile-time guard.
        for effect in &effects {
            let _description: &str = match effect {
                Effect::AgentInvocation { .. }
                | Effect::InvokePlanningAgent { .. }
                | Effect::InvokeDevelopmentAgent { .. }
                | Effect::InvokeAnalysisAgent { .. }
                | Effect::InvokeReviewAgent { .. }
                | Effect::InvokeFixAgent { .. }
                | Effect::InvokeFixAnalysisAgent { .. }
                | Effect::InvokeCommitAgent => "invoke-agent",
                Effect::InitializeAgentChain { .. } => "init-chain",
                Effect::PreparePlanningPrompt { .. }
                | Effect::PrepareDevelopmentPrompt { .. }
                | Effect::PrepareReviewPrompt { .. }
                | Effect::PrepareFixPrompt { .. }
                | Effect::PrepareCommitPrompt { .. } => "prepare-prompt",
                Effect::MaterializePlanningInputs { .. }
                | Effect::MaterializeDevelopmentInputs { .. }
                | Effect::MaterializeReviewInputs { .. }
                | Effect::MaterializeCommitInputs { .. } => "materialize-inputs",
                Effect::ExtractPlanningXml { .. }
                | Effect::ExtractDevelopmentXml { .. }
                | Effect::ExtractReviewIssuesXml { .. }
                | Effect::ExtractFixResultXml { .. }
                | Effect::ExtractCommitXml => "extract-xml",
                Effect::ValidatePlanningXml { .. }
                | Effect::ValidateDevelopmentXml { .. }
                | Effect::ValidateReviewIssuesXml { .. }
                | Effect::ValidateFixResultXml { .. }
                | Effect::ValidateCommitXml => "validate-xml",
                Effect::ApplyPlanningOutcome { .. }
                | Effect::ApplyDevelopmentOutcome { .. }
                | Effect::ApplyReviewOutcome { .. }
                | Effect::ApplyFixOutcome { .. }
                | Effect::ApplyCommitMessageOutcome => "apply-outcome",
                Effect::ArchivePlanningXml { .. }
                | Effect::ArchiveDevelopmentXml { .. }
                | Effect::ArchiveReviewIssuesXml { .. }
                | Effect::ArchiveFixResultXml { .. }
                | Effect::ArchiveCommitXml => "archive-file",
                Effect::CleanupRequiredFiles { .. } => "cleanup-files",
                Effect::PrepareDevelopmentContext { .. } | Effect::PrepareReviewContext { .. } => {
                    "prepare-context"
                }
                Effect::WritePlanningMarkdown { .. } | Effect::WriteIssuesMarkdown { .. } => {
                    "write-file"
                }
                Effect::ExtractReviewIssueSnippets { .. } => "extract-snippets",
                Effect::RunRebase { .. } => "run-rebase",
                Effect::ResolveRebaseConflicts { .. } => "resolve-conflicts",
                Effect::CheckCommitDiff => "check-diff",
                Effect::CreateCommit { .. } => "create-commit",
                Effect::SkipCommit { .. } => "skip-commit",
                Effect::PushToRemote { .. } => "push-remote",
                Effect::CreatePullRequest { .. } => "create-pr",
                Effect::BackoffWait { .. } => "backoff-wait",
                Effect::ReportAgentChainExhausted { .. } => "report-exhausted",
                Effect::ValidateFinalState => "validate-state",
                Effect::SaveCheckpoint { .. } => "save-checkpoint",
                Effect::EnsureGitignoreEntries => "ensure-gitignore",
                Effect::CleanupContext => "cleanup-context",
                Effect::ConfigureGitAuth { .. } => "configure-auth",
                Effect::LockPromptPermissions => "lock-permissions",
                Effect::RestorePromptPermissions => "restore-permissions",
                Effect::WriteContinuationContext(_) => "write-continuation",
                Effect::CleanupContinuationContext => "cleanup-continuation",
                Effect::WriteTimeoutContext { .. } => "write-timeout-context",
                Effect::TriggerDevFixFlow { .. } => "trigger-devfix",
                Effect::TriggerLoopRecovery { .. } => "trigger-recovery",
                Effect::EmitRecoveryReset { .. } => "emit-reset",
                Effect::AttemptRecovery { .. } => "attempt-recovery",
                Effect::EmitRecoverySuccess { .. } => "emit-success",
                Effect::EmitCompletionMarkerAndTerminate { .. } => "emit-completion",
                Effect::CheckUncommittedChangesBeforeTermination => "check-uncommitted",
                Effect::CheckResidualFiles { .. } => "check-residual-files",
                // Phase 4: Parallel worker effects
                Effect::EvaluateParallelPlan { .. } => "evaluate-parallel-plan",
                Effect::DispatchParallelWorkers { .. } => "dispatch-parallel-workers",
                Effect::InvokeParallelVerifier { .. } => "invoke-parallel-verifier",
                Effect::CheckNetworkConnectivity => "check-connectivity",
                Effect::PollForConnectivity { .. } => "poll-connectivity",
            };
        }

        // Variant count guard: catches additions/removals even if the match is updated.
        assert_eq!(
            effects.len(),
            80,
            "Expected 80 Effect instances; update this test if variants were added or removed"
        );
    });
}

/// Test that agent fallback happens exclusively via reducer events.
///
/// Agent switching occurs through reducer event processing, not through
/// any ad-hoc logic in phase code. This test verifies the reducer is the
/// single source of truth for agent chain advancement.
#[test]
fn test_agent_fallback_only_via_reducer_events() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::event::{AgentEvent, PipelineEvent, PipelinePhase};
    use ralph_workflow::reducer::state::ContinuationState;
    use ralph_workflow::reducer::state::PipelineState;
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.continuation = ContinuationState::with_limits(3, 2);
        state.agent_chain = state.agent_chain.with_agents(
            vec![
                "agent-a".to_string(),
                "agent-b".to_string(),
                "agent-c".to_string(),
            ],
            vec![vec![], vec![], vec![]],
            AgentRole::Developer,
        );

        // Verify initial agent
        assert_eq!(
            state.agent_chain.current_agent(),
            Some(&"agent-a".to_string())
        );

        // FallbackTriggered event should switch to next agent
        let state = reduce(
            state,
            PipelineEvent::Agent(AgentEvent::FallbackTriggered {
                role: AgentRole::Developer,
                from_agent: "agent-a".into(),
                to_agent: "agent-b".into(),
            }),
        );

        assert_eq!(
            state.agent_chain.current_agent(),
            Some(&"agent-b".to_string()),
            "FallbackTriggered should switch to next agent"
        );

        // InvocationFailed with retriable=false should retry the same agent first (except auth/429),
        // and only switch agents after exhausting the same-agent retry budget.
        let after_first_failure = reduce(
            state,
            PipelineEvent::Agent(AgentEvent::InvocationFailed {
                role: AgentRole::Developer,
                agent: "agent-b".into(),
                exit_code: 1,
                error_kind: ralph_workflow::reducer::event::AgentErrorKind::FileSystem,
                retriable: false,
            }),
        );

        assert_eq!(
            after_first_failure.agent_chain.current_agent(),
            Some(&"agent-b".to_string()),
            "InvocationFailed(retriable=false) should retry same agent first (except auth/429)"
        );

        // Observable behavior: determine_next_effect should still target agent-b
        // (i.e., the reducer schedules a same-agent retry, not a fallback).
        let retry_effect =
            ralph_workflow::reducer::orchestration::determine_next_effect(&after_first_failure);
        assert!(
            !matches!(
                retry_effect,
                ralph_workflow::reducer::effect::Effect::BackoffWait { .. }
                    | ralph_workflow::reducer::effect::Effect::ReportAgentChainExhausted { .. }
            ),
            "After first failure, should retry same agent, not backoff/exhaust: {retry_effect:?}"
        );

        let after_second_failure = reduce(
            after_first_failure,
            PipelineEvent::Agent(AgentEvent::InvocationFailed {
                role: AgentRole::Developer,
                agent: "agent-b".into(),
                exit_code: 1,
                error_kind: ralph_workflow::reducer::event::AgentErrorKind::FileSystem,
                retriable: false,
            }),
        );

        assert_eq!(
            after_second_failure.agent_chain.current_agent(),
            Some(&"agent-c".to_string()),
            "After exhausting same-agent retry budget, InvocationFailed(retriable=false) should switch agents"
        );

        // InvocationFailed with retriable=true should NOT switch agents (tries next model)
        // Reset to test retriable case
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.agent_chain = state.agent_chain.with_agents(
            vec!["primary".to_string(), "fallback".to_string()],
            vec![vec!["model-a".to_string(), "model-b".to_string()], vec![]],
            AgentRole::Developer,
        );

        let state = reduce(
            state,
            PipelineEvent::Agent(AgentEvent::InvocationFailed {
                role: AgentRole::Developer,
                agent: "primary".into(),
                exit_code: 1,
                error_kind: ralph_workflow::reducer::event::AgentErrorKind::Network,
                retriable: true,
            }),
        );

        // Retriable failure should advance model, not agent
        assert_eq!(
            state.agent_chain.current_agent(),
            Some(&"primary".to_string()),
            "InvocationFailed(retriable=true) should NOT switch agent"
        );
    });
}

/// Test that effect determination is stateless and deterministic.
///
/// The same state should always produce the same effect. This is a key
/// property of the reducer architecture - no external state influences
/// effect determination.
#[test]
fn test_effect_determination_is_pure_function_of_state() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::effect::Effect;
    use ralph_workflow::reducer::event::PipelinePhase;
    use ralph_workflow::reducer::orchestration::determine_next_effect;
    use ralph_workflow::reducer::state::PipelineState;

    with_default_timeout(|| {
        // Create a specific state
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.iteration = 1;
        state.agent_chain = state.agent_chain.with_agents(
            vec!["test-agent".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        );

        // Call determine_next_effect multiple times
        let effect1 = determine_next_effect(&state);
        let effect2 = determine_next_effect(&state);
        let effect3 = determine_next_effect(&state);

        // All calls should produce the same effect (purity)
        assert!(
            matches!(&effect1, Effect::PrepareDevelopmentContext { iteration: 1 }),
            "First call: {effect1:?}"
        );
        assert!(
            matches!(&effect2, Effect::PrepareDevelopmentContext { iteration: 1 }),
            "Second call: {effect2:?}"
        );
        assert!(
            matches!(&effect3, Effect::PrepareDevelopmentContext { iteration: 1 }),
            "Third call: {effect3:?}"
        );
    });
}

// ============================================================================
// PHASE MODULE CONTROL FLOW TESTS
// ============================================================================

/// Test that review phase validation failures surface as reducer events.
///
/// When XML validation fails during review, the phase module must emit an event
/// and let the reducer decide retry policy. The phase module should NOT internally
/// hide failures or make retry decisions autonomously.
#[test]
fn test_review_validation_failure_surfaces_via_event() {
    use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase, ReviewEvent};
    use ralph_workflow::reducer::state::PipelineState;
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        // Start in Review phase
        let mut state = with_locked_prompt_permissions(PipelineState::initial(0, 3));
        state.phase = PipelinePhase::Review;
        state.reviewer_pass = 0;

        // When review output validation fails, reducer should track the attempt
        // via the OutputValidationFailed event (not hidden inside phase module)
        let state = reduce(
            state,
            PipelineEvent::Review(ReviewEvent::OutputValidationFailed {
                pass: 0,
                attempt: 0,
                error_detail: None,
            }),
        );

        // Observable behavior: after one validation failure, the reducer should
        // still schedule a retry (not exhaust or advance agents).
        let effect = ralph_workflow::reducer::orchestration::determine_next_effect(&state);
        assert!(
            !matches!(
                effect,
                ralph_workflow::reducer::effect::Effect::ReportAgentChainExhausted { .. }
            ),
            "After one review validation failure, pipeline should retry, not exhaust: {effect:?}"
        );

        // Another failure should still allow retry (reducer controls retry logic)
        let state = reduce(
            state,
            PipelineEvent::Review(ReviewEvent::OutputValidationFailed {
                pass: 0,
                attempt: 1,
                error_detail: None,
            }),
        );

        let effect = ralph_workflow::reducer::orchestration::determine_next_effect(&state);
        assert!(
            !matches!(
                effect,
                ralph_workflow::reducer::effect::Effect::ReportAgentChainExhausted { .. }
            ),
            "After two review validation failures, pipeline should still retry: {effect:?}"
        );
    });
}

/// Test that development continuation decisions come from reducer state.
///
/// When development returns status="partial" or "failed", the decision to continue
/// must come from reducer state transitions, not from autonomous phase module logic.
#[test]
fn test_development_continuation_is_reducer_driven() {
    use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent, PipelinePhase};
    use ralph_workflow::reducer::state::{DevelopmentStatus, PipelineState};
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        // Start in Development phase
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;

        // Simulate a "partial" status from development via reducer event
        // The reducer state should track continuation context
        let state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: DevelopmentStatus::Partial,
                summary: "Work partially done".to_string(),
                files_changed: Some(vec!["file.rs".to_string()]),
                next_steps: Some("Continue implementation".to_string()),
            }),
        );

        // Verify reducer state tracks continuation
        assert!(
            state.continuation.is_continuation(),
            "Continuation decision must be tracked in reducer state"
        );
        assert_eq!(
            state.continuation.previous_status,
            Some(DevelopmentStatus::Partial),
            "Previous status must be tracked for continuation"
        );
        assert_eq!(
            state.continuation.continuation_attempt, 1,
            "Continuation attempt counter must be incremented"
        );
    });
}

/// Test that phase transitions only happen via reducer events.
///
/// Phase modules must NOT directly advance phases. All phase transitions
/// must occur through reducer event processing, ensuring state is the
/// single source of truth.
#[test]
fn test_phase_transitions_only_via_reducer_events() {
    use ralph_workflow::reducer::event::{
        CommitEvent, DevelopmentEvent, PipelineEvent, PipelinePhase, PlanningEvent,
    };
    use ralph_workflow::reducer::state::PipelineState;
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        // Commit-gated flow: Planning → Development → CommitMessage → FinalValidation
        // Note: With 1 iteration and 1 review pass, Review phase is never visited
        // because Review is only entered after the final development iteration.
        let state = with_locked_prompt_permissions(PipelineState::initial(1, 1));
        assert_eq!(state.phase, PipelinePhase::Planning);

        // Transition Planning -> Development via event
        let state = reduce(
            state,
            PipelineEvent::Planning(PlanningEvent::GenerationCompleted {
                iteration: 0,
                valid: true,
            }),
        );
        assert_eq!(
            state.phase,
            PipelinePhase::Development,
            "Planning->Development must happen via reducer event"
        );

        // Commit-gated: Transition Development -> CommitMessage via event
        let state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::IterationCompleted {
                iteration: 0,
                output_valid: true,
            }),
        );
        assert_eq!(
            state.phase,
            PipelinePhase::CommitMessage,
            "Commit-gated: Development->CommitMessage must happen via reducer event"
        );

        // Transition CommitMessage -> FinalValidation via event (all iterations done)
        let state = reduce(
            state,
            PipelineEvent::Commit(CommitEvent::Created {
                hash: "abc123".to_string(),
                message: "test".to_string(),
            }),
        );
        assert_eq!(
            state.phase,
            PipelinePhase::FinalValidation,
            "CommitMessage->FinalValidation must happen via reducer event when all iterations done"
        );
    });
}
