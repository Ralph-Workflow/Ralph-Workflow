//! Effect-to-capability and effect-kind exhaustive mapping tables.
//!
//! This module contains the pure mapping functions from Effect variants to
//! their required capabilities and effect kind categories.
//!
//! # Design Principles
//!
//! - **Exhaustive coverage**: Every Effect variant has a mapping
//! - **Pure function**: No I/O, deterministic results
//! - **Audit-friendly**: Effect kind enables grouping for audit trails

use crate::agents::session::Capability;
use crate::reducer::effect::Effect;
use std::fmt;

/// Returns the set of capabilities required to execute the given effect.
///
/// This is a pure mapping from Effect variant to Vec<Capability>.
/// All Phase 2 effects should be covered exhaustively.
///
/// # Effect-to-Capability Mapping
///
/// | Effect Category | Required Capabilities | Phase 2 Drains |
/// | --- | --- | --- |
/// | Agent invocation (InvokePlanningAgent, InvokeReviewAgent, InvokeCommitAgent) | ArtifactSubmit | Planning, Review, Commit |
/// | Workspace reads (MaterializePlanningInputs, PrepareReviewContext, etc.) | WorkspaceRead | All read-only drains |
/// | Git reads (CheckCommitDiff, git diff/status in prompts) | GitStatusRead, GitDiffRead | All drains |
/// | Workspace writes (WritePlanningMarkdown, WriteIssuesMarkdown) | WorkspaceWriteEphemeral | Planning, Review |
/// | Git writes (CreateCommit) | GitWrite | Commit only |
/// | Process execution | ProcessExecBounded | Not in read-only drains |
#[must_use]
pub fn required_capabilities(effect: &Effect) -> Vec<Capability> {
    match effect {
        // =====================================================================
        // Planning Effects
        // =====================================================================
        Effect::PreparePlanningPrompt { .. } => vec![],
        Effect::MaterializePlanningInputs { .. } => vec![Capability::WorkspaceRead],
        Effect::CleanupRequiredFiles { .. } => vec![Capability::WorkspaceWriteEphemeral],
        Effect::InvokePlanningAgent { .. } => vec![Capability::ArtifactSubmit],
        Effect::ExtractPlanningXml { .. } => vec![Capability::WorkspaceRead],
        Effect::ValidatePlanningXml { .. } => vec![],
        Effect::WritePlanningMarkdown { .. } => vec![Capability::WorkspaceWriteEphemeral],
        Effect::ArchivePlanningXml { .. } => vec![Capability::WorkspaceWriteEphemeral],
        Effect::ApplyPlanningOutcome { .. } => vec![],

        // =====================================================================
        // Development Effects
        // =====================================================================
        Effect::PrepareDevelopmentContext { .. } => vec![Capability::WorkspaceRead],
        Effect::MaterializeDevelopmentInputs { .. } => vec![Capability::WorkspaceRead],
        Effect::PrepareDevelopmentPrompt { .. } => vec![],
        Effect::InvokeDevelopmentAgent { .. } => vec![
            Capability::WorkspaceRead,
            Capability::WorkspaceWriteTracked,
            Capability::ProcessExecBounded,
            Capability::ArtifactSubmit,
        ],
        Effect::InvokeAnalysisAgent { .. } => vec![
            Capability::WorkspaceRead,
            Capability::GitDiffRead,
            Capability::ArtifactSubmit,
        ],
        Effect::ExtractDevelopmentXml { .. } => vec![Capability::WorkspaceRead],
        Effect::ValidateDevelopmentXml { .. } => vec![],
        Effect::ApplyDevelopmentOutcome { .. } => vec![],
        Effect::ArchiveDevelopmentXml { .. } => vec![Capability::WorkspaceWriteEphemeral],

        // =====================================================================
        // Review Effects
        // =====================================================================
        Effect::PrepareReviewContext { .. } => vec![Capability::WorkspaceRead],
        Effect::MaterializeReviewInputs { .. } => vec![Capability::WorkspaceRead],
        Effect::PrepareReviewPrompt { .. } => vec![],
        Effect::InvokeReviewAgent { .. } => vec![
            Capability::WorkspaceRead,
            Capability::GitDiffRead,
            Capability::ArtifactSubmit,
        ],
        Effect::ExtractReviewIssuesXml { .. } => vec![Capability::WorkspaceRead],
        Effect::ValidateReviewIssuesXml { .. } => vec![],
        Effect::WriteIssuesMarkdown { .. } => vec![Capability::WorkspaceWriteEphemeral],
        Effect::ExtractReviewIssueSnippets { .. } => vec![],
        Effect::ArchiveReviewIssuesXml { .. } => vec![Capability::WorkspaceWriteEphemeral],
        Effect::ApplyReviewOutcome { .. } => vec![],

        // =====================================================================
        // Fix Effects
        // =====================================================================
        Effect::PrepareFixPrompt { .. } => vec![],
        Effect::InvokeFixAgent { .. } => vec![
            Capability::WorkspaceRead,
            Capability::WorkspaceWriteTracked,
            Capability::ProcessExecBounded,
            Capability::ArtifactSubmit,
        ],
        Effect::InvokeFixAnalysisAgent { .. } => vec![
            Capability::WorkspaceRead,
            Capability::GitDiffRead,
            Capability::ArtifactSubmit,
        ],
        Effect::ExtractFixResultXml { .. } => vec![Capability::WorkspaceRead],
        Effect::ValidateFixResultXml { .. } => vec![],
        Effect::ApplyFixOutcome { .. } => vec![],
        Effect::ArchiveFixResultXml { .. } => vec![Capability::WorkspaceWriteEphemeral],

        // =====================================================================
        // Commit Effects
        // =====================================================================
        Effect::CheckCommitDiff => vec![Capability::GitStatusRead, Capability::GitDiffRead],
        Effect::PrepareCommitPrompt { .. } => vec![],
        Effect::MaterializeCommitInputs { .. } => vec![Capability::GitDiffRead],
        Effect::InvokeCommitAgent => vec![
            Capability::GitStatusRead,
            Capability::GitDiffRead,
            Capability::ArtifactSubmit,
        ],
        Effect::ExtractCommitXml => vec![Capability::WorkspaceRead],
        Effect::ValidateCommitXml => vec![],
        Effect::ApplyCommitMessageOutcome => vec![],
        Effect::ArchiveCommitXml => vec![Capability::WorkspaceWriteEphemeral],
        Effect::CreateCommit { .. } => vec![Capability::GitWrite],
        Effect::SkipCommit { .. } => vec![],
        Effect::CheckResidualFiles { .. } => vec![Capability::GitStatusRead],
        Effect::CheckUncommittedChangesBeforeTermination => vec![Capability::GitStatusRead],

        // =====================================================================
        // Rebase Effects
        // =====================================================================
        Effect::RunRebase { .. } => vec![Capability::GitStatusRead, Capability::GitDiffRead],
        Effect::ResolveRebaseConflicts { .. } => vec![
            Capability::GitStatusRead,
            Capability::GitDiffRead,
            Capability::GitWrite,
        ],

        // =====================================================================
        // System Effects
        // =====================================================================
        Effect::BackoffWait { .. } => vec![],
        Effect::ReportAgentChainExhausted { .. } => vec![],
        Effect::ValidateFinalState => vec![],
        Effect::SaveCheckpoint { .. } => vec![],
        Effect::EnsureGitignoreEntries => {
            vec![Capability::WorkspaceRead, Capability::GitStatusRead]
        }
        Effect::CleanupContext => vec![],
        Effect::RestorePromptPermissions => vec![Capability::WorkspaceWriteTracked],
        Effect::LockPromptPermissions => vec![Capability::WorkspaceWriteTracked],
        Effect::WriteContinuationContext(..) => vec![Capability::WorkspaceWriteEphemeral],
        Effect::CleanupContinuationContext => vec![],
        Effect::WriteTimeoutContext { .. } => vec![Capability::WorkspaceWriteEphemeral],
        Effect::TriggerDevFixFlow { .. } => vec![],
        Effect::EmitCompletionMarkerAndTerminate { .. } => vec![],
        Effect::TriggerLoopRecovery { .. } => vec![],
        Effect::EmitRecoveryReset { .. } => vec![],
        Effect::AttemptRecovery { .. } => vec![],
        Effect::EmitRecoverySuccess { .. } => vec![],

        // =====================================================================
        // Agent Invocation Effect
        // =====================================================================
        Effect::AgentInvocation { .. } => {
            vec![Capability::WorkspaceRead, Capability::ArtifactSubmit]
        }
        Effect::InitializeAgentChain { .. } => vec![],

        // =====================================================================
        // Cloud Mode Effects (internal use only)
        // =====================================================================
        Effect::ConfigureGitAuth { .. } => vec![Capability::EnvRead],
        Effect::PushToRemote { .. } => vec![Capability::GitStatusRead, Capability::GitWrite],
        Effect::CreatePullRequest { .. } => vec![Capability::GitStatusRead, Capability::GitWrite],

        // =====================================================================
        // Phase 4: Parallel Worker Effects
        // =====================================================================
        Effect::EvaluateParallelPlan { .. } => vec![],
        Effect::DispatchParallelWorkers { .. } => vec![],
        Effect::InvokeParallelVerifier { .. } => vec![],
    }
}

/// Returns the effect kind category for grouping in audit trails.
#[must_use]
pub fn effect_kind(effect: &Effect) -> EffectKind {
    match effect {
        Effect::PreparePlanningPrompt { .. } => EffectKind::PromptPreparation,
        Effect::MaterializePlanningInputs { .. } => EffectKind::WorkspaceRead,
        Effect::CleanupRequiredFiles { .. } => EffectKind::WorkspaceWrite,
        Effect::InvokePlanningAgent { .. } => EffectKind::AgentInvocation,
        Effect::ExtractPlanningXml { .. } => EffectKind::WorkspaceRead,
        Effect::ValidatePlanningXml { .. } => EffectKind::Validation,
        Effect::WritePlanningMarkdown { .. } => EffectKind::WorkspaceWrite,
        Effect::ArchivePlanningXml { .. } => EffectKind::WorkspaceWrite,
        Effect::ApplyPlanningOutcome { .. } => EffectKind::StateTransition,

        Effect::PrepareDevelopmentContext { .. } => EffectKind::WorkspaceRead,
        Effect::MaterializeDevelopmentInputs { .. } => EffectKind::WorkspaceRead,
        Effect::PrepareDevelopmentPrompt { .. } => EffectKind::PromptPreparation,
        Effect::InvokeDevelopmentAgent { .. } => EffectKind::AgentInvocation,
        Effect::InvokeAnalysisAgent { .. } => EffectKind::AgentInvocation,
        Effect::ExtractDevelopmentXml { .. } => EffectKind::WorkspaceRead,
        Effect::ValidateDevelopmentXml { .. } => EffectKind::Validation,
        Effect::ApplyDevelopmentOutcome { .. } => EffectKind::StateTransition,
        Effect::ArchiveDevelopmentXml { .. } => EffectKind::WorkspaceWrite,

        Effect::PrepareReviewContext { .. } => EffectKind::WorkspaceRead,
        Effect::MaterializeReviewInputs { .. } => EffectKind::WorkspaceRead,
        Effect::PrepareReviewPrompt { .. } => EffectKind::PromptPreparation,
        Effect::InvokeReviewAgent { .. } => EffectKind::AgentInvocation,
        Effect::ExtractReviewIssuesXml { .. } => EffectKind::WorkspaceRead,
        Effect::ValidateReviewIssuesXml { .. } => EffectKind::Validation,
        Effect::WriteIssuesMarkdown { .. } => EffectKind::WorkspaceWrite,
        Effect::ExtractReviewIssueSnippets { .. } => EffectKind::WorkspaceRead,
        Effect::ArchiveReviewIssuesXml { .. } => EffectKind::WorkspaceWrite,
        Effect::ApplyReviewOutcome { .. } => EffectKind::StateTransition,

        Effect::PrepareFixPrompt { .. } => EffectKind::PromptPreparation,
        Effect::InvokeFixAgent { .. } => EffectKind::AgentInvocation,
        Effect::InvokeFixAnalysisAgent { .. } => EffectKind::AgentInvocation,
        Effect::ExtractFixResultXml { .. } => EffectKind::WorkspaceRead,
        Effect::ValidateFixResultXml { .. } => EffectKind::Validation,
        Effect::ApplyFixOutcome { .. } => EffectKind::StateTransition,
        Effect::ArchiveFixResultXml { .. } => EffectKind::WorkspaceWrite,

        Effect::CheckCommitDiff => EffectKind::GitRead,
        Effect::PrepareCommitPrompt { .. } => EffectKind::PromptPreparation,
        Effect::MaterializeCommitInputs { .. } => EffectKind::GitRead,
        Effect::InvokeCommitAgent => EffectKind::AgentInvocation,
        Effect::ExtractCommitXml => EffectKind::WorkspaceRead,
        Effect::ValidateCommitXml => EffectKind::Validation,
        Effect::ApplyCommitMessageOutcome => EffectKind::StateTransition,
        Effect::ArchiveCommitXml => EffectKind::WorkspaceWrite,
        Effect::CreateCommit { .. } => EffectKind::GitWrite,
        Effect::SkipCommit { .. } => EffectKind::StateTransition,
        Effect::CheckResidualFiles { .. } => EffectKind::GitRead,
        Effect::CheckUncommittedChangesBeforeTermination => EffectKind::GitRead,

        Effect::RunRebase { .. } => EffectKind::GitWrite,
        Effect::ResolveRebaseConflicts { .. } => EffectKind::GitWrite,

        Effect::BackoffWait { .. } => EffectKind::System,
        Effect::ReportAgentChainExhausted { .. } => EffectKind::System,
        Effect::ValidateFinalState => EffectKind::Validation,
        Effect::SaveCheckpoint { .. } => EffectKind::System,
        Effect::EnsureGitignoreEntries => EffectKind::WorkspaceRead,
        Effect::CleanupContext => EffectKind::System,
        Effect::RestorePromptPermissions => EffectKind::WorkspaceWrite,
        Effect::LockPromptPermissions => EffectKind::WorkspaceWrite,
        Effect::WriteContinuationContext(..) => EffectKind::WorkspaceWrite,
        Effect::CleanupContinuationContext => EffectKind::System,
        Effect::WriteTimeoutContext { .. } => EffectKind::WorkspaceWrite,
        Effect::TriggerDevFixFlow { .. } => EffectKind::System,
        Effect::EmitCompletionMarkerAndTerminate { .. } => EffectKind::System,
        Effect::TriggerLoopRecovery { .. } => EffectKind::System,
        Effect::EmitRecoveryReset { .. } => EffectKind::System,
        Effect::AttemptRecovery { .. } => EffectKind::System,
        Effect::EmitRecoverySuccess { .. } => EffectKind::System,

        Effect::AgentInvocation { .. } => EffectKind::AgentInvocation,
        Effect::InitializeAgentChain { .. } => EffectKind::System,

        Effect::ConfigureGitAuth { .. } => EffectKind::EnvRead,
        Effect::PushToRemote { .. } => EffectKind::GitWrite,
        Effect::CreatePullRequest { .. } => EffectKind::GitWrite,

        // =====================================================================
        // Phase 4: Parallel Worker Effects
        // =====================================================================
        Effect::EvaluateParallelPlan { .. } => EffectKind::System,
        Effect::DispatchParallelWorkers { .. } => EffectKind::System,
        Effect::InvokeParallelVerifier { .. } => EffectKind::AgentInvocation,
    }
}

/// Returns the effect name as a human-readable string for logging/debugging.
#[must_use]
pub fn effect_name(effect: &Effect) -> String {
    format_effect_name(effect)
}

/// Categorizes effects by kind for audit trail grouping.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EffectKind {
    /// Reading from workspace
    WorkspaceRead,
    /// Writing to workspace (ephemeral or tracked)
    WorkspaceWrite,
    /// Reading from git (status/diff)
    GitRead,
    /// Writing to git (commit, merge, push)
    GitWrite,
    /// Invoking an agent
    AgentInvocation,
    /// Validating artifacts
    Validation,
    /// Prompt preparation (no I/O)
    PromptPreparation,
    /// State transitions (no I/O)
    StateTransition,
    /// Environment variable reads
    EnvRead,
    /// System operations (checkpoint, cleanup, etc.)
    System,
}

impl fmt::Display for EffectKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EffectKind::WorkspaceRead => write!(f, "workspace_read"),
            EffectKind::WorkspaceWrite => write!(f, "workspace_write"),
            EffectKind::GitRead => write!(f, "git_read"),
            EffectKind::GitWrite => write!(f, "git_write"),
            EffectKind::AgentInvocation => write!(f, "agent_invocation"),
            EffectKind::Validation => write!(f, "validation"),
            EffectKind::PromptPreparation => write!(f, "prompt_preparation"),
            EffectKind::StateTransition => write!(f, "state_transition"),
            EffectKind::EnvRead => write!(f, "env_read"),
            EffectKind::System => write!(f, "system"),
        }
    }
}

fn format_effect_name(effect: &Effect) -> String {
    match effect {
        Effect::PreparePlanningPrompt { iteration, .. } => {
            format!("PreparePlanningPrompt(iteration={})", iteration)
        }
        Effect::MaterializePlanningInputs { iteration, .. } => {
            format!("MaterializePlanningInputs(iteration={})", iteration)
        }
        Effect::CleanupRequiredFiles { files, .. } => {
            format!("CleanupRequiredFiles(files={})", files.len())
        }
        Effect::InvokePlanningAgent { iteration, .. } => {
            format!("InvokePlanningAgent(iteration={})", iteration)
        }
        Effect::ExtractPlanningXml { iteration, .. } => {
            format!("ExtractPlanningXml(iteration={})", iteration)
        }
        Effect::ValidatePlanningXml { iteration, .. } => {
            format!("ValidatePlanningXml(iteration={})", iteration)
        }
        Effect::WritePlanningMarkdown { iteration, .. } => {
            format!("WritePlanningMarkdown(iteration={})", iteration)
        }
        Effect::ArchivePlanningXml { iteration, .. } => {
            format!("ArchivePlanningXml(iteration={})", iteration)
        }
        Effect::ApplyPlanningOutcome { iteration, .. } => {
            format!("ApplyPlanningOutcome(iteration={})", iteration)
        }
        Effect::PrepareDevelopmentContext { iteration, .. } => {
            format!("PrepareDevelopmentContext(iteration={})", iteration)
        }
        Effect::MaterializeDevelopmentInputs { iteration, .. } => {
            format!("MaterializeDevelopmentInputs(iteration={})", iteration)
        }
        Effect::PrepareDevelopmentPrompt { iteration, .. } => {
            format!("PrepareDevelopmentPrompt(iteration={})", iteration)
        }
        Effect::InvokeDevelopmentAgent { iteration, .. } => {
            format!("InvokeDevelopmentAgent(iteration={})", iteration)
        }
        Effect::InvokeAnalysisAgent { iteration, .. } => {
            format!("InvokeAnalysisAgent(iteration={})", iteration)
        }
        Effect::ExtractDevelopmentXml { iteration, .. } => {
            format!("ExtractDevelopmentXml(iteration={})", iteration)
        }
        Effect::ValidateDevelopmentXml { iteration, .. } => {
            format!("ValidateDevelopmentXml(iteration={})", iteration)
        }
        Effect::ApplyDevelopmentOutcome { iteration, .. } => {
            format!("ApplyDevelopmentOutcome(iteration={})", iteration)
        }
        Effect::ArchiveDevelopmentXml { iteration, .. } => {
            format!("ArchiveDevelopmentXml(iteration={})", iteration)
        }
        Effect::PrepareReviewContext { pass, .. } => {
            format!("PrepareReviewContext(pass={})", pass)
        }
        Effect::MaterializeReviewInputs { pass, .. } => {
            format!("MaterializeReviewInputs(pass={})", pass)
        }
        Effect::PrepareReviewPrompt { pass, .. } => {
            format!("PrepareReviewPrompt(pass={})", pass)
        }
        Effect::InvokeReviewAgent { pass, .. } => {
            format!("InvokeReviewAgent(pass={})", pass)
        }
        Effect::ExtractReviewIssuesXml { pass, .. } => {
            format!("ExtractReviewIssuesXml(pass={})", pass)
        }
        Effect::ValidateReviewIssuesXml { pass, .. } => {
            format!("ValidateReviewIssuesXml(pass={})", pass)
        }
        Effect::WriteIssuesMarkdown { pass, .. } => {
            format!("WriteIssuesMarkdown(pass={})", pass)
        }
        Effect::ExtractReviewIssueSnippets { pass, .. } => {
            format!("ExtractReviewIssueSnippets(pass={})", pass)
        }
        Effect::ArchiveReviewIssuesXml { pass, .. } => {
            format!("ArchiveReviewIssuesXml(pass={})", pass)
        }
        Effect::ApplyReviewOutcome { pass, .. } => {
            format!("ApplyReviewOutcome(pass={})", pass)
        }
        Effect::PrepareFixPrompt { pass, .. } => {
            format!("PrepareFixPrompt(pass={})", pass)
        }
        Effect::InvokeFixAgent { pass, .. } => {
            format!("InvokeFixAgent(pass={})", pass)
        }
        Effect::InvokeFixAnalysisAgent { pass, .. } => {
            format!("InvokeFixAnalysisAgent(pass={})", pass)
        }
        Effect::ExtractFixResultXml { pass, .. } => {
            format!("ExtractFixResultXml(pass={})", pass)
        }
        Effect::ValidateFixResultXml { pass, .. } => {
            format!("ValidateFixResultXml(pass={})", pass)
        }
        Effect::ApplyFixOutcome { pass, .. } => {
            format!("ApplyFixOutcome(pass={})", pass)
        }
        Effect::ArchiveFixResultXml { pass, .. } => {
            format!("ArchiveFixResultXml(pass={})", pass)
        }
        Effect::RunRebase {
            phase,
            target_branch,
            ..
        } => {
            format!(
                "RunRebase(phase={:?}, target_branch={})",
                phase, target_branch
            )
        }
        Effect::ResolveRebaseConflicts { strategy, .. } => {
            format!("ResolveRebaseConflicts(strategy={:?})", strategy)
        }
        Effect::CheckCommitDiff => "CheckCommitDiff".to_string(),
        Effect::PrepareCommitPrompt { .. } => "PrepareCommitPrompt".to_string(),
        Effect::MaterializeCommitInputs { attempt, .. } => {
            format!("MaterializeCommitInputs(attempt={})", attempt)
        }
        Effect::InvokeCommitAgent => "InvokeCommitAgent".to_string(),
        Effect::ExtractCommitXml => "ExtractCommitXml".to_string(),
        Effect::ValidateCommitXml => "ValidateCommitXml".to_string(),
        Effect::ApplyCommitMessageOutcome => "ApplyCommitMessageOutcome".to_string(),
        Effect::ArchiveCommitXml => "ArchiveCommitXml".to_string(),
        Effect::CreateCommit { message, files, .. } => {
            format!(
                "CreateCommit(message_len={}, files={})",
                message.len(),
                files.len()
            )
        }
        Effect::SkipCommit { reason } => {
            format!("SkipCommit(reason={})", reason)
        }
        Effect::CheckResidualFiles { pass, .. } => {
            format!("CheckResidualFiles(pass={})", pass)
        }
        Effect::CheckUncommittedChangesBeforeTermination => {
            "CheckUncommittedChangesBeforeTermination".to_string()
        }
        Effect::BackoffWait {
            role,
            cycle,
            duration_ms,
            ..
        } => {
            format!(
                "BackoffWait(role={:?}, cycle={}, duration_ms={})",
                role, cycle, duration_ms
            )
        }
        Effect::ReportAgentChainExhausted {
            role, phase, cycle, ..
        } => {
            format!(
                "ReportAgentChainExhausted(role={:?}, phase={:?}, cycle={})",
                role, phase, cycle
            )
        }
        Effect::ValidateFinalState => "ValidateFinalState".to_string(),
        Effect::SaveCheckpoint { trigger, .. } => {
            format!("SaveCheckpoint(trigger={:?})", trigger)
        }
        Effect::EnsureGitignoreEntries => "EnsureGitignoreEntries".to_string(),
        Effect::CleanupContext => "CleanupContext".to_string(),
        Effect::RestorePromptPermissions => "RestorePromptPermissions".to_string(),
        Effect::LockPromptPermissions => "LockPromptPermissions".to_string(),
        Effect::WriteContinuationContext(data) => {
            format!("WriteContinuationContext(iteration={})", data.iteration)
        }
        Effect::CleanupContinuationContext => "CleanupContinuationContext".to_string(),
        Effect::WriteTimeoutContext {
            role,
            logfile_path,
            context_path,
            ..
        } => {
            format!(
                "WriteTimeoutContext(role={:?}, logfile={}, context={})",
                role, logfile_path, context_path
            )
        }
        Effect::TriggerDevFixFlow {
            failed_phase,
            failed_role,
            retry_cycle,
            ..
        } => {
            format!(
                "TriggerDevFixFlow(failed_phase={:?}, failed_role={:?}, retry_cycle={})",
                failed_phase, failed_role, retry_cycle
            )
        }
        Effect::EmitCompletionMarkerAndTerminate {
            is_failure, reason, ..
        } => {
            format!(
                "EmitCompletionMarkerAndTerminate(is_failure={}, reason={:?})",
                is_failure, reason
            )
        }
        Effect::TriggerLoopRecovery {
            detected_loop,
            loop_count,
            ..
        } => {
            format!(
                "TriggerLoopRecovery(detected_loop={}, loop_count={})",
                detected_loop, loop_count
            )
        }
        Effect::EmitRecoveryReset {
            reset_type,
            target_phase,
            ..
        } => {
            format!(
                "EmitRecoveryReset(reset_type={:?}, target_phase={:?})",
                reset_type, target_phase
            )
        }
        Effect::AttemptRecovery {
            level,
            attempt_count,
            ..
        } => {
            format!(
                "AttemptRecovery(level={}, attempt_count={})",
                level, attempt_count
            )
        }
        Effect::EmitRecoverySuccess {
            level,
            total_attempts,
            ..
        } => {
            format!(
                "EmitRecoverySuccess(level={}, total_attempts={})",
                level, total_attempts
            )
        }
        Effect::AgentInvocation {
            role, agent, model, ..
        } => {
            format!(
                "AgentInvocation(role={:?}, agent={}, model={:?})",
                role, agent, model
            )
        }
        Effect::InitializeAgentChain { drain, .. } => {
            format!("InitializeAgentChain(drain={:?})", drain)
        }
        Effect::ConfigureGitAuth { auth_method, .. } => {
            format!("ConfigureGitAuth(auth_method={})", auth_method)
        }
        Effect::PushToRemote {
            remote,
            branch,
            force,
            ..
        } => {
            format!(
                "PushToRemote(remote={}, branch={}, force={})",
                remote, branch, force
            )
        }
        Effect::CreatePullRequest {
            base_branch,
            head_branch,
            title,
            ..
        } => {
            format!(
                "CreatePullRequest(base={}, head={}, title=\"{}\")",
                base_branch, head_branch, title
            )
        }
        // =====================================================================
        // Phase 4: Parallel Worker Effects
        // =====================================================================
        Effect::EvaluateParallelPlan { .. } => "EvaluateParallelPlan".to_string(),
        Effect::DispatchParallelWorkers { .. } => "DispatchParallelWorkers".to_string(),
        Effect::InvokeParallelVerifier { .. } => "InvokeParallelVerifier".to_string(),
    }
}
