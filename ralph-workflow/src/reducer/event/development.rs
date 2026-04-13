//! Development events.

use crate::reducer::state::DevelopmentStatus;
use serde::{Deserialize, Serialize};

/// Development phase events.
///
/// Events related to development iterations, including continuation handling
/// for partial/failed completion states. Development iterations involve
/// invoking developer agents to make code changes.
///
/// # State Transitions
///
/// - `PhaseStarted`: Sets phase to Development
/// - `IterationStarted`: Resets agent chain, clears continuation state
/// - `IterationCompleted(output_valid=true)`: Transitions to `CommitMessage`
/// - `IterationCompleted(output_valid=false)`: Stays in Development for retry
/// - `ContinuationTriggered`: Saves context for continuation attempt
/// - `ContinuationSucceeded`: Clears continuation, proceeds to `CommitMessage`
/// - `PhaseCompleted`: Transitions to Review
#[derive(Clone, Serialize, Deserialize, Debug)]
pub enum DevelopmentEvent {
    /// Development phase has started.
    PhaseStarted,
    /// A development iteration has started.
    IterationStarted {
        /// The iteration number starting.
        iteration: u32,
    },

    /// Development context prepared for an iteration.
    ///
    /// Emitted after `Effect::PrepareDevelopmentContext` completes.
    ContextPrepared {
        /// The iteration number the context was prepared for.
        iteration: u32,
    },

    /// Development prompt prepared for an iteration.
    ///
    /// Emitted after `Effect::PrepareDevelopmentPrompt` completes.
    PromptPrepared {
        /// The iteration number the prompt was prepared for.
        iteration: u32,
    },

    /// Developer agent was invoked for an iteration.
    ///
    /// Emitted after `Effect::InvokeDevelopmentAgent` completes.
    AgentInvoked {
        /// The iteration number the agent was invoked for.
        iteration: u32,
    },

    /// Analysis agent was invoked to verify development results for an iteration.
    ///
    /// Emitted after `Effect::InvokeAnalysisAgent` completes. The analysis agent
    /// produces `development_result.xml` by comparing git diff against PLAN.md.
    AnalysisAgentInvoked {
        /// The iteration number the analysis agent was invoked for.
        iteration: u32,
    },

    /// Development result XML exists and was read successfully for the iteration.
    ///
    /// Emitted after `Effect::ExtractDevelopmentXml` completes.
    XmlExtracted {
        /// The iteration number the XML was extracted for.
        iteration: u32,
    },
    /// Development result XML missing for an iteration.
    ///
    /// Emitted after `Effect::ExtractDevelopmentXml` when the XML was absent.
    XmlMissing {
        /// The iteration number the XML was extracted for.
        iteration: u32,
        /// The invalid output attempt count.
        attempt: u32,
    },

    /// Development result XML validated for an iteration.
    ///
    /// This event captures the parsed development outcome.
    XmlValidated {
        /// The iteration number the XML was validated for.
        iteration: u32,
        /// The parsed development status.
        status: DevelopmentStatus,
        /// Explicit routing decision from the artifact's `decision` field.
        ///
        /// When `Some`, the reducer uses this decision directly instead of
        /// deriving one from `status`. `None` means fall back to status-derived routing.
        analysis_decision: Option<crate::reducer::state::AnalysisDecision>,
        /// Summary of what was accomplished.
        summary: String,
        /// Files changed in this attempt.
        files_changed: Option<Vec<String>>,
        /// Agent's recommended next steps.
        next_steps: Option<String>,
    },

    /// Development outcome applied for an iteration.
    OutcomeApplied {
        /// The iteration number the outcome was applied for.
        iteration: u32,
    },

    /// Development result XML archived for an iteration.
    ///
    /// Emitted after `Effect::ArchiveDevelopmentXml` completes.
    XmlArchived {
        /// The iteration number the XML was archived for.
        iteration: u32,
    },
    /// Development result XML cleaned before invoking the developer agent.
    XmlCleaned {
        /// The iteration number the XML was cleaned for.
        iteration: u32,
    },
    /// A development iteration completed with validation result.
    IterationCompleted {
        /// The iteration number that completed.
        iteration: u32,
        /// Whether the output passed validation.
        output_valid: bool,
    },
    /// Development phase completed, all iterations done.
    PhaseCompleted,
    /// Continuation triggered due to partial/failed status.
    ///
    /// Emitted only when development output is valid (parseable) but
    /// status is not "completed" (i.e., "partial" or "failed").
    ContinuationTriggered {
        /// Current iteration number.
        iteration: u32,
        /// Status from the agent ("partial" or "failed").
        status: DevelopmentStatus,
        /// Summary of what was accomplished.
        summary: String,
        /// Files changed in this attempt.
        files_changed: Option<Vec<String>>,
        /// Agent's recommended next steps.
        next_steps: Option<String>,
    },
    /// Continuation attempt succeeded with status "completed".
    ContinuationSucceeded {
        /// Current iteration number.
        iteration: u32,
        /// Number of continuation attempts it took.
        total_continuation_attempts: u32,
    },
    /// Output validation failed (XSD/XML parsing error).
    ///
    /// Emitted when development output cannot be parsed or fails XSD validation.
    /// The reducer decides whether to retry (same agent) or switch agents based
    /// on the attempt count in state.
    OutputValidationFailed {
        /// Current iteration number.
        iteration: u32,
        /// Current invalid output attempt number.
        attempt: u32,
    },
    /// Continuation attempts exhausted without reaching completed status.
    ///
    /// Emitted when development iteration has used all allowed continuation
    /// attempts but still hasn't reached status="completed".
    ContinuationBudgetExhausted {
        /// Current iteration number.
        iteration: u32,
        /// Total continuation attempts made.
        total_attempts: u32,
        /// Last status received (Partial or Failed).
        last_status: DevelopmentStatus,
    },
    /// Continuation context file was written successfully.
    ///
    /// Emitted after `WriteContinuationContext` effect completes. The reducer
    /// clears the `needs_context_write` flag on this event.
    ContinuationContextWritten {
        /// Current iteration number.
        iteration: u32,
        /// Current continuation attempt number.
        attempt: u32,
    },
    /// Continuation context file was cleaned up.
    ///
    /// Emitted after `CleanupContinuationContext` effect completes.
    ContinuationContextCleaned,
}
