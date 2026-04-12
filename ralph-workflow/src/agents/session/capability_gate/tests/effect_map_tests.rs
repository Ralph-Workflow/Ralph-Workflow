//! Tests for effect_map.rs - Effect-to-capability and effect-kind mappings.

use crate::agents::session::capability_gate::{
    effect_kind, effect_name, required_capabilities, EffectKind,
};
use crate::agents::session::{AgentSession, Capability, SessionDrain};
use crate::reducer::effect::Effect;

fn planning_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1)
}

fn development_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
}

fn review_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Review, 1)
}

fn commit_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Commit, 1)
}

fn analysis_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Analysis, 1)
}

fn fix_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Fix, 1)
}

#[test]
fn planning_effects_require_workspace_read() {
    let effect = Effect::MaterializePlanningInputs { iteration: 1 };
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::WorkspaceRead));
}

#[test]
fn invoke_planning_agent_requires_artifact_submit() {
    let effect = Effect::InvokePlanningAgent { iteration: 1 };
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::ArtifactSubmit));
}

#[test]
fn write_planning_markdown_requires_ephemeral_write() {
    let effect = Effect::WritePlanningMarkdown { iteration: 1 };
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::WorkspaceWriteEphemeral));
}

#[test]
fn prepare_prompt_requires_no_capabilities() {
    let effect = Effect::PreparePlanningPrompt {
        iteration: 1,
        prompt_mode: crate::reducer::state::PromptMode::Normal,
    };
    let caps = required_capabilities(&effect);
    assert!(caps.is_empty());
}

#[test]
fn check_commit_diff_requires_git_capabilities() {
    let effect = Effect::CheckCommitDiff;
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::GitStatusRead));
    assert!(caps.contains(&Capability::GitDiffRead));
}

#[test]
fn create_commit_requires_git_write() {
    let effect = Effect::CreateCommit {
        message: "test".to_string(),
        files: vec![],
        excluded_files: vec![],
    };
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::GitWrite));
}

#[test]
fn invoke_development_agent_requires_write_and_exec() {
    let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::WorkspaceRead));
    assert!(caps.contains(&Capability::WorkspaceWriteTracked));
    assert!(caps.contains(&Capability::ProcessExecBounded));
    assert!(caps.contains(&Capability::ArtifactSubmit));
}

#[test]
fn invoke_analysis_agent_requires_read_and_artifact() {
    let effect = Effect::InvokeAnalysisAgent { iteration: 1 };
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::WorkspaceRead));
    assert!(caps.contains(&Capability::GitDiffRead));
    assert!(caps.contains(&Capability::ArtifactSubmit));
}

#[test]
fn invoke_review_agent_requires_read_and_artifact() {
    let effect = Effect::InvokeReviewAgent { pass: 1 };
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::WorkspaceRead));
    assert!(caps.contains(&Capability::GitDiffRead));
    assert!(caps.contains(&Capability::ArtifactSubmit));
}

#[test]
fn invoke_fix_agent_requires_write_and_exec() {
    let effect = Effect::InvokeFixAgent { pass: 1 };
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::WorkspaceRead));
    assert!(caps.contains(&Capability::WorkspaceWriteTracked));
    assert!(caps.contains(&Capability::ProcessExecBounded));
    assert!(caps.contains(&Capability::ArtifactSubmit));
}

#[test]
fn invoke_commit_agent_requires_git_and_artifact() {
    let effect = Effect::InvokeCommitAgent;
    let caps = required_capabilities(&effect);
    assert!(caps.contains(&Capability::GitStatusRead));
    assert!(caps.contains(&Capability::GitDiffRead));
    assert!(caps.contains(&Capability::ArtifactSubmit));
}

#[test]
fn planning_session_allows_planning_effects() {
    let session = planning_session();
    let effects = vec![
        Effect::PreparePlanningPrompt {
            iteration: 1,
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::MaterializePlanningInputs { iteration: 1 },
        Effect::InvokePlanningAgent { iteration: 1 },
        Effect::ExtractPlanningXml { iteration: 1 },
        Effect::ValidatePlanningXml { iteration: 1 },
        Effect::WritePlanningMarkdown { iteration: 1 },
        Effect::ArchivePlanningXml { iteration: 1 },
    ];
    for effect in effects {
        let caps = required_capabilities(&effect);
        for cap in caps {
            assert!(
                session.capabilities.contains(cap),
                "Planning session should have {:?} for {:?}",
                cap,
                effect
            );
        }
    }
}

#[test]
fn review_session_allows_review_effects() {
    let session = review_session();
    let effects = vec![
        Effect::PrepareReviewContext { pass: 1 },
        Effect::MaterializeReviewInputs { pass: 1 },
        Effect::PrepareReviewPrompt {
            pass: 1,
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::InvokeReviewAgent { pass: 1 },
        Effect::ExtractReviewIssuesXml { pass: 1 },
        Effect::ValidateReviewIssuesXml { pass: 1 },
        Effect::WriteIssuesMarkdown { pass: 1 },
        Effect::ExtractReviewIssueSnippets { pass: 1 },
        Effect::ArchiveReviewIssuesXml { pass: 1 },
    ];
    for effect in effects {
        let caps = required_capabilities(&effect);
        for cap in caps {
            assert!(
                session.capabilities.contains(cap),
                "Review session should have {:?} for {:?}",
                cap,
                effect
            );
        }
    }
}

#[test]
fn commit_session_allows_commit_effects() {
    let session = commit_session();
    let effects = vec![
        Effect::CheckCommitDiff,
        Effect::PrepareCommitPrompt {
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::MaterializeCommitInputs { attempt: 1 },
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
    ];
    for effect in effects {
        let caps = required_capabilities(&effect);
        for cap in caps {
            assert!(
                session.capabilities.contains(cap),
                "Commit session should have {:?} for {:?}",
                cap,
                effect
            );
        }
    }
}

#[test]
fn development_session_allows_all_development_effects() {
    let session = development_session();
    let effects = vec![
        Effect::PrepareDevelopmentContext { iteration: 1 },
        Effect::MaterializeDevelopmentInputs { iteration: 1 },
        Effect::PrepareDevelopmentPrompt {
            iteration: 1,
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::InvokeDevelopmentAgent { iteration: 1 },
        Effect::InvokeAnalysisAgent { iteration: 1 },
        Effect::ExtractDevelopmentXml { iteration: 1 },
        Effect::ValidateDevelopmentXml { iteration: 1 },
        Effect::ApplyDevelopmentOutcome { iteration: 1 },
        Effect::ArchiveDevelopmentXml { iteration: 1 },
    ];
    for effect in effects {
        let caps = required_capabilities(&effect);
        for cap in caps {
            assert!(
                session.capabilities.contains(cap),
                "Development session should have {:?} for {:?}",
                cap,
                effect
            );
        }
    }
}

#[test]
fn fix_session_allows_fix_effects() {
    let session = fix_session();
    let effects = vec![
        Effect::PrepareFixPrompt {
            pass: 1,
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::InvokeFixAgent { pass: 1 },
        Effect::InvokeFixAnalysisAgent { pass: 1 },
        Effect::ExtractFixResultXml { pass: 1 },
        Effect::ValidateFixResultXml { pass: 1 },
        Effect::ApplyFixOutcome { pass: 1 },
    ];
    for effect in effects {
        let caps = required_capabilities(&effect);
        for cap in caps {
            assert!(
                session.capabilities.contains(cap),
                "Fix session should have {:?} for {:?}",
                cap,
                effect
            );
        }
    }
}

#[test]
fn analysis_session_allows_analysis_effects() {
    let session = analysis_session();
    let effects = vec![
        Effect::InvokeAnalysisAgent { iteration: 1 },
        Effect::ExtractDevelopmentXml { iteration: 1 },
        Effect::ValidateDevelopmentXml { iteration: 1 },
    ];
    for effect in effects {
        let caps = required_capabilities(&effect);
        for cap in caps {
            assert!(
                session.capabilities.contains(cap),
                "Analysis session should have {:?} for {:?}",
                cap,
                effect
            );
        }
    }
}

#[test]
fn effect_name_produces_readable_string() {
    let effect = Effect::InvokePlanningAgent { iteration: 5 };
    let name = effect_name(&effect);
    assert!(name.contains("InvokePlanningAgent"));
    assert!(name.contains("5"));
}

#[test]
fn effect_name_for_create_commit() {
    let effect = Effect::CreateCommit {
        message: "my message".to_string(),
        files: vec!["file1.rs".to_string()],
        excluded_files: vec![],
    };
    let name = effect_name(&effect);
    assert!(name.contains("CreateCommit"));
    assert!(name.contains("message_len=10"));
}

#[test]
fn effect_kind_for_read_effects() {
    let effect = Effect::MaterializePlanningInputs { iteration: 1 };
    assert_eq!(effect_kind(&effect), EffectKind::WorkspaceRead);
}

#[test]
fn effect_kind_for_write_effects() {
    let effect = Effect::WritePlanningMarkdown { iteration: 1 };
    assert_eq!(effect_kind(&effect), EffectKind::WorkspaceWrite);
}

#[test]
fn effect_kind_for_git_write_effects() {
    let effect = Effect::CreateCommit {
        message: "test".to_string(),
        files: vec![],
        excluded_files: vec![],
    };
    assert_eq!(effect_kind(&effect), EffectKind::GitWrite);
}

#[test]
fn effect_kind_for_agent_invocation() {
    let effect = Effect::InvokePlanningAgent { iteration: 1 };
    assert_eq!(effect_kind(&effect), EffectKind::AgentInvocation);
}

#[test]
fn required_capabilities_returns_vec() {
    let effect = Effect::CheckCommitDiff;
    let caps = required_capabilities(&effect);
    assert!(!caps.is_empty());
}

#[test]
fn required_capabilities_for_all_effect_variants() {
    let effects: Vec<Effect> = vec![
        Effect::PreparePlanningPrompt {
            iteration: 1,
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::MaterializePlanningInputs { iteration: 1 },
        Effect::CleanupRequiredFiles {
            files: Box::new([]),
        },
        Effect::InvokePlanningAgent { iteration: 1 },
        Effect::ExtractPlanningXml { iteration: 1 },
        Effect::ValidatePlanningXml { iteration: 1 },
        Effect::WritePlanningMarkdown { iteration: 1 },
        Effect::ArchivePlanningXml { iteration: 1 },
        Effect::ApplyPlanningOutcome {
            iteration: 1,
            valid: true,
        },
        Effect::PrepareDevelopmentContext { iteration: 1 },
        Effect::MaterializeDevelopmentInputs { iteration: 1 },
        Effect::PrepareDevelopmentPrompt {
            iteration: 1,
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::InvokeDevelopmentAgent { iteration: 1 },
        Effect::InvokeAnalysisAgent { iteration: 1 },
        Effect::ExtractDevelopmentXml { iteration: 1 },
        Effect::ValidateDevelopmentXml { iteration: 1 },
        Effect::ApplyDevelopmentOutcome { iteration: 1 },
        Effect::ArchiveDevelopmentXml { iteration: 1 },
        Effect::PrepareReviewContext { pass: 1 },
        Effect::MaterializeReviewInputs { pass: 1 },
        Effect::PrepareReviewPrompt {
            pass: 1,
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::InvokeReviewAgent { pass: 1 },
        Effect::ExtractReviewIssuesXml { pass: 1 },
        Effect::ValidateReviewIssuesXml { pass: 1 },
        Effect::WriteIssuesMarkdown { pass: 1 },
        Effect::ExtractReviewIssueSnippets { pass: 1 },
        Effect::ArchiveReviewIssuesXml { pass: 1 },
        Effect::ApplyReviewOutcome {
            pass: 1,
            issues_found: true,
            clean_no_issues: false,
        },
        Effect::PrepareFixPrompt {
            pass: 1,
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::InvokeFixAgent { pass: 1 },
        Effect::InvokeFixAnalysisAgent { pass: 1 },
        Effect::ExtractFixResultXml { pass: 1 },
        Effect::ValidateFixResultXml { pass: 1 },
        Effect::ApplyFixOutcome { pass: 1 },
        Effect::ArchiveFixResultXml { pass: 1 },
        Effect::CheckCommitDiff,
        Effect::PrepareCommitPrompt {
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        },
        Effect::MaterializeCommitInputs { attempt: 1 },
        Effect::InvokeCommitAgent,
        Effect::ExtractCommitXml,
        Effect::ValidateCommitXml,
        Effect::ApplyCommitMessageOutcome,
        Effect::ArchiveCommitXml,
        Effect::CreateCommit {
            message: "".to_string(),
            files: vec![],
            excluded_files: vec![],
        },
        Effect::SkipCommit {
            reason: "".to_string(),
        },
        Effect::CheckResidualFiles { pass: 1 },
        Effect::CheckUncommittedChangesBeforeTermination,
        Effect::BackoffWait {
            role: crate::agents::AgentRole::Developer,
            cycle: 1,
            duration_ms: 100,
        },
        Effect::ReportAgentChainExhausted {
            role: crate::agents::AgentRole::Developer,
            phase: crate::reducer::event::PipelinePhase::Planning,
            cycle: 1,
        },
        Effect::ValidateFinalState,
        Effect::SaveCheckpoint {
            trigger: crate::reducer::event::CheckpointTrigger::Interrupt,
        },
        Effect::EnsureGitignoreEntries,
        Effect::CleanupContext,
        Effect::RestorePromptPermissions,
        Effect::LockPromptPermissions,
        Effect::WriteContinuationContext(crate::reducer::effect::ContinuationContextData {
            iteration: 1,
            attempt: 1,
            status: crate::reducer::state::DevelopmentStatus::Completed,
            summary: String::new(),
            files_changed: None,
            next_steps: None,
        }),
        Effect::CleanupContinuationContext,
        Effect::WriteTimeoutContext {
            role: crate::agents::AgentRole::Developer,
            logfile_path: "".to_string(),
            context_path: "".to_string(),
        },
        Effect::TriggerDevFixFlow {
            failed_phase: crate::reducer::event::PipelinePhase::Planning,
            failed_role: crate::agents::AgentRole::Developer,
            retry_cycle: 1,
        },
        Effect::EmitCompletionMarkerAndTerminate {
            is_failure: false,
            reason: None,
        },
        Effect::TriggerLoopRecovery {
            detected_loop: "".to_string(),
            loop_count: 1,
        },
        Effect::EmitRecoveryReset {
            reset_type: crate::reducer::effect::RecoveryResetType::CompleteReset,
            target_phase: crate::reducer::event::PipelinePhase::Planning,
        },
        Effect::AttemptRecovery {
            level: 1,
            attempt_count: 1,
        },
        Effect::EmitRecoverySuccess {
            level: 1,
            total_attempts: 1,
        },
        Effect::AgentInvocation {
            role: crate::agents::AgentRole::Developer,
            agent: "".to_string(),
            model: None,
            prompt: "".to_string(),
        },
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Planning,
        },
        Effect::ConfigureGitAuth {
            auth_method: "".to_string(),
        },
        Effect::PushToRemote {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            force: false,
            commit_sha: "".to_string(),
        },
        Effect::CreatePullRequest {
            base_branch: "main".to_string(),
            head_branch: "".to_string(),
            title: "".to_string(),
            body: "".to_string(),
        },
        Effect::EvaluateParallelPlan {
            plan: crate::agents::session::ParallelPlan {
                parent_plan_id: "".to_string(),
                work_units: vec![],
            },
        },
        Effect::DispatchParallelWorkers {
            plan: crate::agents::session::ParallelPlan {
                parent_plan_id: "".to_string(),
                work_units: vec![],
            },
        },
    ];
    for effect in effects {
        let _caps = required_capabilities(&effect);
        let name = effect_name(&effect);
        let kind = effect_kind(&effect);
        assert!(!name.is_empty());
        assert!(!format!("{:?}", kind).is_empty());
    }
}

#[test]
fn planning_session_has_workspace_write_ephemeral() {
    let session = planning_session();
    assert!(
        session
            .capabilities
            .contains(Capability::WorkspaceWriteEphemeral),
        "Planning session should have WorkspaceWriteEphemeral"
    );
}

#[test]
fn planning_session_does_not_have_workspace_write_tracked() {
    let session = planning_session();
    assert!(
        !session
            .capabilities
            .contains(Capability::WorkspaceWriteTracked),
        "Planning session should NOT have WorkspaceWriteTracked"
    );
}

#[test]
fn planning_session_does_not_have_process_exec() {
    let session = planning_session();
    assert!(
        !session
            .capabilities
            .contains(Capability::ProcessExecBounded),
        "Planning session should NOT have ProcessExecBounded"
    );
}
