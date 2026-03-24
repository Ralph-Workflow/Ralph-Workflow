//! Capability gate for RFC-009 Phase 2 policy enforcement.
//!
//! This module provides the core policy engine for Phase 2 enforcement:
//! - Pure function mapping from Effect variants to required Capabilities
//! - Session capability checking against required capabilities
//! - PolicyOutcome generation for audit trail
//!
//! # Design Principles
//!
//! - **Pure function**: No I/O, deterministic results based only on session and effect
//! - **Exhaustive coverage**: Every Effect variant has a capability mapping
//! - **Clear denials**: Denied outcomes include descriptive reasons
//! - **Audit-friendly**: Returns all information needed for audit trail records

use crate::agents::session::{AgentSession, Capability, PolicyOutcome};
use crate::reducer::effect::Effect;
use std::fmt;

/// Returns true if this effect is a Ralph-internal effect that should bypass
/// the capability gate (e.g., checkpoint save, recovery, lifecycle operations).
///
/// These effects are never user-driven and are part of Ralph's internal
/// orchestration machinery.
#[must_use]
pub fn is_ralph_internal_effect(effect: &Effect) -> bool {
    matches!(
        effect,
        Effect::InitializeAgentChain { .. }
            | Effect::SaveCheckpoint { .. }
            | Effect::RestorePromptPermissions
            | Effect::LockPromptPermissions
            | Effect::WriteContinuationContext(..)
            | Effect::CleanupContinuationContext
            | Effect::WriteTimeoutContext { .. }
            | Effect::TriggerDevFixFlow { .. }
            | Effect::EmitCompletionMarkerAndTerminate { .. }
            | Effect::TriggerLoopRecovery { .. }
            | Effect::EmitRecoveryReset { .. }
            | Effect::AttemptRecovery { .. }
            | Effect::EmitRecoverySuccess { .. }
            | Effect::ConfigureGitAuth { .. }
            | Effect::PushToRemote { .. }
            | Effect::CreatePullRequest { .. }
            | Effect::BackoffWait { .. }
            | Effect::ValidateFinalState
            | Effect::CleanupContext
            | Effect::EnsureGitignoreEntries
    )
}

/// Check whether the given session has sufficient capabilities to execute the effect.
///
/// Returns `PolicyOutcome::Approved` if all required capabilities are present,
/// or `PolicyOutcome::Denied { reason }` describing which capability is missing.
///
/// # Arguments
///
/// * `session` - The agent session to check capabilities against
/// * `effect` - The effect to be executed
///
/// # Example
///
/// ```ignore
/// let session = AgentSession::for_drain("run-123".to_string(), SessionDrain::Planning, 1);
/// let effect = Effect::InvokePlanningAgent { iteration: 1 };
/// let outcome = check_effect_capability(&session, &effect);
/// assert!(matches!(outcome, PolicyOutcome::Approved));
/// ```
#[must_use]
pub fn check_effect_capability(session: &AgentSession, effect: &Effect) -> PolicyOutcome {
    let required = required_capabilities(effect);
    for cap in &required {
        let outcome = session.check_capability(*cap);
        if !matches!(outcome, PolicyOutcome::Approved) {
            return outcome;
        }
    }
    PolicyOutcome::Approved
}

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
    }
}

/// Returns a human-readable description of the effect for audit purposes.
#[must_use]
pub fn effect_name(effect: &Effect) -> String {
    format_effect_name(effect)
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
    }
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
    }
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::{AgentSession, Capability, SessionDrain};
    use crate::reducer::effect::Effect;

    // ===================================================================
    // Test helper functions
    // ===================================================================

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

    // ===================================================================
    // Effect capability mapping tests
    // ===================================================================

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

    // ===================================================================
    // Session capability checking tests
    // ===================================================================

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
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Planning session should allow {:?}: {:?}",
                effect,
                outcome
            );
        }
    }

    #[test]
    fn planning_session_denies_development_effects() {
        let session = planning_session();

        // Development effects require WorkspaceWriteTracked and ProcessExecBounded
        // which planning session doesn't have
        let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Planning session should deny development effects"
        );
    }

    #[test]
    fn planning_session_denies_fix_effects() {
        let session = planning_session();

        let effect = Effect::InvokeFixAgent { pass: 1 };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Planning session should deny fix effects"
        );
    }

    #[test]
    fn planning_session_denies_git_write() {
        let session = planning_session();

        let effect = Effect::CreateCommit {
            message: "test".to_string(),
            files: vec![],
            excluded_files: vec![],
        };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Planning session should deny git write"
        );
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
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Review session should allow {:?}: {:?}",
                effect,
                outcome
            );
        }
    }

    #[test]
    fn review_session_denies_git_write() {
        let session = review_session();

        let effect = Effect::CreateCommit {
            message: "test".to_string(),
            files: vec![],
            excluded_files: vec![],
        };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Review session should deny git write"
        );
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
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Commit session should allow {:?}: {:?}",
                effect,
                outcome
            );
        }
    }

    #[test]
    fn commit_session_denies_workspace_write_tracked() {
        let session = commit_session();

        // Commit session doesn't have WorkspaceWriteTracked
        let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };
        let outcome = check_effect_capability(&session, &effect);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "Commit session should deny workspace write tracked"
        );
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
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Development session should allow {:?}: {:?}",
                effect,
                outcome
            );
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
            Effect::ArchiveFixResultXml { pass: 1 },
        ];

        for effect in effects {
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Fix session should allow {:?}: {:?}",
                effect,
                outcome
            );
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
            let outcome = check_effect_capability(&session, &effect);
            assert!(
                matches!(outcome, PolicyOutcome::Approved),
                "Analysis session should allow {:?}: {:?}",
                effect,
                outcome
            );
        }
    }

    // ===================================================================
    // Denial reason tests
    // ===================================================================

    #[test]
    fn denied_outcome_includes_reason() {
        let session = planning_session();
        let effect = Effect::InvokeDevelopmentAgent { iteration: 1 };
        let outcome = check_effect_capability(&session, &effect);

        if let PolicyOutcome::Denied { reason } = outcome {
            assert!(
                reason.contains("workspace.write_tracked")
                    || reason.contains("process.exec_bounded"),
                "Denial reason should mention missing capability"
            );
        } else {
            panic!("Expected denial, got {:?}", outcome);
        }
    }

    #[test]
    fn denied_outcome_includes_capability_name() {
        let session = planning_session();
        let effect = Effect::CreateCommit {
            message: "test".to_string(),
            files: vec![],
            excluded_files: vec![],
        };
        let outcome = check_effect_capability(&session, &effect);

        if let PolicyOutcome::Denied { reason } = outcome {
            assert!(
                reason.contains("git.write"),
                "Denial reason should mention git.write"
            );
        } else {
            panic!("Expected denial, got {:?}", outcome);
        }
    }

    // ===================================================================
    // Effect name tests
    // ===================================================================

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

    // ===================================================================
    // Effect kind tests
    // ===================================================================

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

    // ===================================================================
    // Audit trail helper tests
    // ===================================================================

    #[test]
    fn required_capabilities_returns_vec() {
        let effect = Effect::CheckCommitDiff;
        let caps = required_capabilities(&effect);
        assert!(!caps.is_empty());
    }

    #[test]
    fn required_capabilities_for_all_effect_variants() {
        // This test ensures we have coverage for all effect variants
        // If a new variant is added without updating this function,
        // this test will fail to compile (which is the intent)
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
        ];

        for effect in effects {
            let _caps = required_capabilities(&effect);
            let name = effect_name(&effect);
            let kind = effect_kind(&effect);
            // Just verify these don't panic
            assert!(!name.is_empty());
            assert!(!format!("{:?}", kind).is_empty());
        }
    }

    // ===================================================================
    // Backward compatibility tests
    // ===================================================================

    #[test]
    fn planning_session_has_workspace_write_ephemeral() {
        // Planning session SHOULD have WorkspaceWriteEphemeral capability
        // because Ralph writes plan files and markdown to .agent/ which is ephemeral
        let session = planning_session();
        assert!(
            session
                .capabilities
                .contains(Capability::WorkspaceWriteEphemeral),
            "Planning session should have WorkspaceWriteEphemeral for Ralph's own writes"
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
}
