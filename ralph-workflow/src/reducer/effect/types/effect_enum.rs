//! Effect type definitions for the Ralph pipeline.
//!
//! Defines the [`Effect`] enum representing all side-effect operations executed
//! by the pipeline. Effects are determined by the reducer's orchestration logic
//! and executed by effect handlers.
//!
//! See `docs/architecture/effect-system.md` for the design overview.

use crate::agents::{AgentDrain, AgentRole};
use serde::{Deserialize, Serialize};

use super::effect_support_types::{ContinuationContextData, RecoveryResetType};
use crate::reducer::event::{CheckpointTrigger, ConflictStrategy, PipelinePhase, RebasePhase};
use crate::reducer::state::PromptMode;

/// Effects represent side-effect operations.
///
/// The reducer determines which effect to execute next based on state.
/// Effect handlers execute effects and emit events.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub enum Effect {
    AgentInvocation {
        role: AgentRole,
        agent: String,
        model: Option<String>,
        prompt: String,
    },

    /// Resolve and materialize the concrete chain bound to a built-in runtime drain.
    ///
    /// The reducer/orchestrator addresses drains directly so handlers never need to
    /// reconstruct chain selection from compatibility role metadata at invocation time.
    InitializeAgentChain {
        drain: AgentDrain,
    },

    /// Prepare the planning prompt for an iteration (single-task).
    PreparePlanningPrompt {
        iteration: u32,
        prompt_mode: PromptMode,
    },

    /// Materialize planning inputs, handling oversize inline vs file references (single-task).
    MaterializePlanningInputs {
        iteration: u32,
    },

    /// Delete specified files from the workspace to ensure fresh agent output (single-task).
    ///
    /// XSD retry exemption: skipped on attempt > 1 so the agent can read the previous
    /// invalid XML (see XSD retry prompts for details).
    CleanupRequiredFiles {
        /// Files to delete (paths relative to workspace root).
        files: Box<[String]>,
    },

    /// Invoke the planning agent for an iteration (single-task).
    InvokePlanningAgent {
        iteration: u32,
    },

    /// Verify that `.agent/tmp/plan.xml` exists and is readable (single-task).
    ExtractPlanningXml {
        iteration: u32,
    },

    /// Validate/parse the XML at `.agent/tmp/plan.xml` and emit a planning validation event (single-task).
    ValidatePlanningXml {
        iteration: u32,
    },

    /// Write `.agent/PLAN.md` from the validated planning XML (single-task).
    WritePlanningMarkdown {
        iteration: u32,
    },

    /// Archive `.agent/tmp/plan.xml` after PLAN.md is written (single-task).
    ArchivePlanningXml {
        iteration: u32,
    },

    /// Emit the appropriate planning outcome event (single-task).
    ApplyPlanningOutcome {
        iteration: u32,
        valid: bool,
    },

    /// Write context artifacts needed for the development prompt (single-task).
    PrepareDevelopmentContext {
        iteration: u32,
    },

    /// Materialize development inputs, handling oversize inline vs file references (single-task).
    MaterializeDevelopmentInputs {
        iteration: u32,
    },

    /// Prepare the development prompt for an iteration (single-task).
    PrepareDevelopmentPrompt {
        iteration: u32,
        prompt_mode: PromptMode,
    },

    /// Invoke the developer agent for an iteration (single-task).
    InvokeDevelopmentAgent {
        iteration: u32,
    },

    /// Invoke the analysis agent to assess git diff against PLAN.md (single-task).
    ///
    /// Produces `development_result.xml`. Does not parse or validate outputs.
    InvokeAnalysisAgent {
        iteration: u32,
    },

    /// Verify that `.agent/tmp/development_result.xml` exists and is readable (single-task).
    ExtractDevelopmentXml {
        iteration: u32,
    },

    /// Validate/parse the XML at `.agent/tmp/development_result.xml` (single-task).
    ValidateDevelopmentXml {
        iteration: u32,
    },

    /// Emit the appropriate development outcome event (single-task).
    ApplyDevelopmentOutcome {
        iteration: u32,
    },

    /// Archive `.agent/tmp/development_result.xml` after validation (single-task).
    ArchiveDevelopmentXml {
        iteration: u32,
    },

    /// Write review inputs (prompt backups, diffs, etc.) for the reviewer agent (single-task).
    PrepareReviewContext {
        pass: u32,
    },

    /// Materialize review inputs, handling oversize inline vs file references (single-task).
    MaterializeReviewInputs {
        pass: u32,
    },

    /// Prepare the review prompt for a pass (single-task).
    PrepareReviewPrompt {
        pass: u32,
        prompt_mode: PromptMode,
    },

    /// Invoke the reviewer agent for a review pass (single-task).
    InvokeReviewAgent {
        pass: u32,
    },

    /// Verify that `.agent/tmp/issues.xml` exists and is readable (single-task).
    ExtractReviewIssuesXml {
        pass: u32,
    },

    /// Validate/parse the XML at `.agent/tmp/issues.xml` (single-task).
    ValidateReviewIssuesXml {
        pass: u32,
    },

    /// Write `.agent/ISSUES.md` from the validated issues XML (single-task).
    WriteIssuesMarkdown {
        pass: u32,
    },

    /// Extract review issue snippets and emit UI output (single-task).
    ExtractReviewIssueSnippets {
        pass: u32,
    },

    /// Archive `.agent/tmp/issues.xml` after ISSUES.md is written (single-task).
    ArchiveReviewIssuesXml {
        pass: u32,
    },

    /// Emit the appropriate review outcome event (single-task).
    ApplyReviewOutcome {
        pass: u32,
        issues_found: bool,
        clean_no_issues: bool,
    },

    /// Prepare the fix prompt for a review pass (single-task).
    PrepareFixPrompt {
        pass: u32,
        prompt_mode: PromptMode,
    },

    /// Invoke the fix agent for a review pass (single-task).
    InvokeFixAgent {
        pass: u32,
    },

    /// Invoke the fix analysis agent to verify fix results (single-task).
    ///
    /// This runs after every fix agent invocation to independently verify
    /// whether the fix addressed the review issues. Uses the same drain
    /// as development analysis (`AgentDrain::Analysis`).
    InvokeFixAnalysisAgent {
        pass: u32,
    },

    /// Verify that `.agent/tmp/fix_result.xml` exists and is readable (single-task).
    ExtractFixResultXml {
        pass: u32,
    },

    /// Validate/parse the XML at `.agent/tmp/fix_result.xml` (single-task).
    ValidateFixResultXml {
        pass: u32,
    },

    /// Emit the appropriate fix outcome event (single-task).
    ApplyFixOutcome {
        pass: u32,
    },

    /// Archive `.agent/tmp/fix_result.xml` after validation (single-task).
    ///
    /// Intentionally sequenced before `ApplyFixOutcome` so the reducer can
    /// archive artifacts while still in the fix chain.
    ArchiveFixResultXml {
        pass: u32,
    },

    RunRebase {
        phase: RebasePhase,
        target_branch: String,
    },

    ResolveRebaseConflicts {
        strategy: ConflictStrategy,
    },

    /// Compute/write the commit diff and emit whether it is empty (single-task).
    CheckCommitDiff,

    /// Render/write the commit prompt for the subsequent commit agent invocation (single-task).
    PrepareCommitPrompt {
        prompt_mode: PromptMode,
    },

    /// Materialize commit inputs, handling model-budget truncation and inline vs reference (single-task).
    MaterializeCommitInputs {
        attempt: u32,
    },

    /// Invoke the commit agent (single-task).
    InvokeCommitAgent,

    /// Verify that `.agent/tmp/commit_message.xml` exists and is readable (single-task).
    ExtractCommitXml,

    /// Validate/parse the XML at `.agent/tmp/commit_message.xml` (single-task).
    ValidateCommitXml,

    /// Emit the appropriate commit outcome event (single-task).
    ApplyCommitMessageOutcome,

    /// Archive `.agent/tmp/commit_message.xml` after validation (single-task).
    ArchiveCommitXml,

    CreateCommit {
        message: String,
        /// Files to selectively stage. Empty means stage all changed files.
        #[serde(default)]
        files: Vec<String>,
        /// Files excluded from this commit with their reasons.
        ///
        /// Audit/observability only — must not change commit execution semantics.
        /// Defaults to empty for backward compatibility with old checkpoints.
        #[serde(default)]
        excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,
    },

    SkipCommit {
        reason: String,
    },

    /// After a selective commit pass, check whether any staged/unstaged files remain.
    ///
    /// Emits `ResidualFilesFound { files, pass }` if the working tree is still dirty,
    /// or `ResidualFilesNone` when it is clean. `pass` identifies which commit pass just
    /// completed (1 = first selective commit, 2 = second/final pass).
    CheckResidualFiles {
        /// Which commit pass just completed (1-indexed).
        pass: u8,
    },

    /// Run `git status --porcelain` before termination; route to commit phase if changes exist (single-task).
    ///
    /// User-initiated Ctrl+C (`interrupted_by_user=true`) skips this check.
    CheckUncommittedChangesBeforeTermination,

    /// Wait for a retry-cycle backoff delay.
    BackoffWait {
        role: AgentRole,
        cycle: u32,
        duration_ms: u64,
    },

    /// Report that the agent chain has exhausted all retry attempts.
    ReportAgentChainExhausted {
        role: AgentRole,
        phase: PipelinePhase,
        cycle: u32,
    },

    ValidateFinalState,

    SaveCheckpoint {
        trigger: CheckpointTrigger,
    },

    /// Check `.gitignore` for required entries and add any missing ones (single-task).
    EnsureGitignoreEntries,

    CleanupContext,

    /// Restore PROMPT.md write permissions after pipeline completion.
    RestorePromptPermissions,

    /// Lock PROMPT.md with read-only permissions at pipeline startup.
    LockPromptPermissions,

    /// Write continuation context file for the next development attempt.
    WriteContinuationContext(ContinuationContextData),

    /// Clean up continuation context file when iteration completes or a fresh iteration starts.
    CleanupContinuationContext,

    /// Preserve prior agent context to a temp file when a timeout occurs without session IDs.
    WriteTimeoutContext {
        /// The role this agent is fulfilling.
        role: AgentRole,
        /// Source logfile path to extract context from.
        logfile_path: String,
        /// Target temp file path for context (e.g., `.agent/tmp/timeout_context_1.txt`).
        context_path: String,
    },

    /// Invoke the dev agent to diagnose and fix a pipeline failure (single-task).
    TriggerDevFixFlow {
        /// The phase where the failure occurred.
        failed_phase: PipelinePhase,
        /// The role of the exhausted agent chain.
        failed_role: AgentRole,
        /// Retry cycle count when exhaustion occurred.
        retry_cycle: u32,
    },

    /// Write a completion marker and transition to Interrupted.
    EmitCompletionMarkerAndTerminate {
        /// Whether the pipeline is terminating due to failure.
        is_failure: bool,
        /// Optional reason for termination.
        reason: Option<String>,
    },

    /// Reset XSD retry state and loop detection counters when a tight loop is detected.
    TriggerLoopRecovery {
        /// String representation of the detected loop (for diagnostics).
        detected_loop: String,
        /// Number of times the loop was repeated.
        loop_count: u32,
    },

    /// Emit recovery reset events to escalate recovery strategy.
    EmitRecoveryReset {
        /// Type of reset to perform.
        reset_type: RecoveryResetType,
        /// Target phase to reset to.
        target_phase: PipelinePhase,
    },

    /// Emit `RecoveryAttempted` event to transition back to the failed phase for retry.
    AttemptRecovery {
        /// The escalation level being attempted.
        level: u32,
        /// The attempt count.
        attempt_count: u32,
    },

    /// Emit `RecoverySucceeded` event to clear recovery state after successful work completion.
    EmitRecoverySuccess {
        /// The escalation level that succeeded.
        level: u32,
        /// Total attempts before success.
        total_attempts: u32,
    },

    // ========================================================================
    // Cloud Mode Effects (INTERNAL USE ONLY)
    // ========================================================================
    //
    // Only emitted when cloud mode is enabled (RALPH_CLOUD_MODE=true).
    // See PROMPT.md for full cloud integration architecture.
    /// Configure git authentication for remote operations (cloud mode only).
    ConfigureGitAuth {
        /// Serialized authentication method for logging/debugging.
        auth_method: String,
    },

    /// Push commits to remote repository immediately after `CreateCommit` (cloud mode only).
    PushToRemote {
        /// Remote name (e.g., "origin")
        remote: String,
        /// Branch to push
        branch: String,
        /// Whether to force push
        force: bool,
        /// The commit SHA being pushed (for reporting)
        commit_sha: String,
    },

    /// Create a pull request on the remote platform during Finalizing phase (cloud mode only).
    CreatePullRequest {
        /// Target branch for the PR
        base_branch: String,
        /// Source branch (the pushed branch)
        head_branch: String,
        /// PR title
        title: String,
        /// PR body/description
        body: String,
    },
}
