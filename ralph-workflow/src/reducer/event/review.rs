// NOTE: split from reducer/event.rs to keep the main file under line limits.
use serde::{Deserialize, Serialize};

/// Review phase events.
///
/// Events related to code review passes and fix attempts. The review phase
/// runs reviewer agents to identify issues and (by default) the same reviewer
/// agent chain to apply any required fixes.
///
/// # State Transitions
///
/// - `PhaseStarted`: Sets phase to Review, resets pass counter
/// - `PassStarted`: Resets agent chain for the pass
/// - `Completed(issues_found=false)`: Advances to next pass or `CommitMessage`
/// - `Completed(issues_found=true)`: Triggers fix attempt
/// - `FixAttemptCompleted`: Transitions to `CommitMessage`
/// - `PhaseCompleted`: Transitions to `CommitMessage`
#[derive(Clone, Serialize, Deserialize, Debug)]
pub enum ReviewEvent {
    /// Review phase has started.
    PhaseStarted,
    /// A review pass has started.
    PassStarted {
        /// The pass number starting.
        pass: u32,
    },

    /// Review context prepared for a pass.
    ///
    /// Emitted after `Effect::PrepareReviewContext` completes.
    ContextPrepared {
        /// The pass number the context was prepared for.
        pass: u32,
    },

    /// Review prompt prepared for a pass.
    ///
    /// Emitted after `Effect::PrepareReviewPrompt` completes.
    PromptPrepared {
        pass: u32,
    },

    /// Reviewer agent was invoked for a pass.
    ///
    /// Emitted after `Effect::InvokeReviewAgent` completes.
    AgentInvoked {
        pass: u32,
    },

    /// Review issues XML exists and was read successfully for the pass.
    ///
    /// Emitted after `Effect::ExtractReviewIssuesXml` completes.
    IssuesXmlExtracted {
        pass: u32,
    },
    /// Review issues XML missing for the pass.
    ///
    /// Emitted after `Effect::ExtractReviewIssuesXml` when the XML was absent.
    IssuesXmlMissing {
        pass: u32,
        /// The invalid output attempt count.
        attempt: u32,
        /// Error detail if file read failed for a reason other than `NotFound`.
        error_detail: Option<String>,
    },

    /// Review issues XML validated for a pass.
    ///
    /// This event is an observation: the XML was valid and the handler determined
    /// whether issues were found and whether this was an explicit clean-no-issues output.
    IssuesXmlValidated {
        pass: u32,
        issues_found: bool,
        clean_no_issues: bool,
        issues: Vec<String>,
        no_issues_found: Option<String>,
    },

    /// ISSUES.md was written for a pass.
    IssuesMarkdownWritten {
        pass: u32,
    },

    /// Review issue snippets were extracted for a pass.
    IssueSnippetsExtracted {
        pass: u32,
    },

    /// Review issues XML archived for a pass.
    IssuesXmlArchived {
        pass: u32,
    },

    /// Review issues XML cleaned before invoking the reviewer agent.
    IssuesXmlCleaned {
        pass: u32,
    },

    /// Fix prompt prepared for a review pass.
    FixPromptPrepared {
        pass: u32,
    },

    /// Fix agent was invoked for a review pass.
    FixAgentInvoked {
        pass: u32,
    },

    /// Fix analysis agent was invoked for a review pass.
    ///
    /// Emitted after `Effect::InvokeFixAnalysisAgent` completes.
    /// This mirrors `DevelopmentEvent::AnalysisAgentInvoked` for the fix phase.
    FixAnalysisAgentInvoked {
        pass: u32,
    },

    /// Fix result XML exists and was read successfully for the pass.
    FixResultXmlExtracted {
        pass: u32,
    },
    /// Fix result XML missing for the pass.
    FixResultXmlMissing {
        pass: u32,
        /// The invalid output attempt count.
        attempt: u32,
        /// Detailed error message from file read failure (if not `NotFound`).
        ///
        /// This field is populated when the file exists but cannot be read
        /// (e.g., permission denied, I/O error). It's None for simple `NotFound`.
        error_detail: Option<String>,
    },

    /// Fix result XML validated for a pass.
    FixResultXmlValidated {
        pass: u32,
        status: crate::reducer::state::FixStatus,
        summary: Option<String>,
        /// Phase 2: typed analysis decision from fix analysis agent.
        ///
        /// Uses `ReviewAnalysisDecision` which routes within the review cycle:
        /// - `NeedsMoreFix` → return to fix agent
        /// - `CycleComplete` → proceed to review_commit
        ///
        /// `None` means no explicit decision — use status-based continuation logic.
        analysis_decision: Option<crate::reducer::state::ReviewAnalysisDecision>,
    },

    /// Fix result XML cleaned before invoking the fix agent.
    FixResultXmlCleaned {
        pass: u32,
    },

    /// Fix outcome applied for a pass.
    FixOutcomeApplied {
        pass: u32,
    },

    FixResultXmlArchived {
        pass: u32,
    },
    /// A review pass completed with results.
    Completed {
        /// The pass number that completed.
        pass: u32,
        /// Whether issues were found requiring fixes.
        issues_found: bool,
    },
    /// A fix attempt for issues has started.
    FixAttemptStarted {
        /// The pass number this fix is for.
        pass: u32,
    },
    /// A fix attempt completed.
    FixAttemptCompleted {
        /// The pass number this fix was for.
        pass: u32,
        /// Whether changes were made.
        changes_made: bool,
    },
    /// Review phase completed, all passes done.
    PhaseCompleted {
        /// Whether the phase exited early (before all passes).
        early_exit: bool,
    },
    /// Review pass found no issues - clean exit.
    ///
    /// Emitted when a review pass completes with no issues found.
    /// This is distinct from `Completed { issues_found: false }` in that
    /// it explicitly signals a clean pass for UI/logging purposes.
    PassCompletedClean {
        /// The pass number that completed.
        pass: u32,
    },
    /// Review output validation failed (XSD/XML parsing error).
    ///
    /// Emitted when review output cannot be parsed. Reducer decides
    /// whether to retry or switch agents.
    ///
    /// The `error_detail` field contains the formatted validation error that will
    /// be shown to the agent in the XSD retry prompt. This error should be
    /// actionable and guide the agent toward producing valid XML.
    OutputValidationFailed {
        /// The pass number.
        pass: u32,
        /// Current invalid output attempt number.
        attempt: u32,
        /// Detailed error message from XSD validation, formatted for AI retry.
        ///
        /// This is the output of `XsdValidationError::format_for_ai_retry()` and
        /// includes:
        /// - Error type and location
        /// - What was expected vs. what was found
        /// - Actionable suggestions for fixing
        /// - Examples of correct format
        error_detail: Option<String>,
    },

    /// Fix attempt completed with incomplete status, needs continuation.
    ///
    /// Emitted when fix output is valid XML but indicates work is not complete
    /// (status is "`issues_remain`"). Triggers a continuation with new session.
    FixContinuationTriggered {
        /// The pass number this fix was for.
        pass: u32,
        /// Status from the agent (typically `IssuesRemain`).
        status: crate::reducer::state::FixStatus,
        /// Summary of what was accomplished.
        summary: Option<String>,
    },

    /// Fix continuation succeeded after multiple attempts.
    ///
    /// Emitted when a fix continuation finally reaches a complete state
    /// (`all_issues_addressed` or `no_issues_found`).
    FixContinuationSucceeded {
        /// The pass number this fix was for.
        pass: u32,
        /// Total number of continuation attempts it took.
        ///
        /// Note: This field is not used by the reducer for state transitions, but
        /// is kept for observability (event logs, checkpoint serialization, debugging).
        total_attempts: u32,
    },

    /// Fix continuation budget exhausted.
    ///
    /// Emitted when fix continuations have been exhausted without reaching
    /// a complete state. Policy decides whether to proceed to commit or abort.
    FixContinuationBudgetExhausted {
        /// The pass number this fix was for.
        pass: u32,
        /// Total number of continuation attempts made.
        total_attempts: u32,
        /// The last status received (typically `IssuesRemain`).
        last_status: crate::reducer::state::FixStatus,
    },

    /// Fix output validation failed (XSD/XML parsing error).
    ///
    /// Emitted when fix output cannot be parsed. Reducer decides
    /// whether to retry or switch agents.
    FixOutputValidationFailed {
        /// The pass number this fix was for.
        pass: u32,
        /// Current invalid output attempt number.
        attempt: u32,
        /// Detailed error message from XSD validation, formatted for AI retry.
        ///
        /// This is the output of `XsdValidationError::format_for_ai_retry()` and includes:
        /// - Error type and location
        /// - What was expected vs. what was found
        /// - Actionable suggestions for fixing
        /// - Examples of correct format
        error_detail: Option<String>,
    },
}
