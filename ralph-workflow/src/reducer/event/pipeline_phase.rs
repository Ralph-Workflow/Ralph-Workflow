//! Pipeline phase definitions.

use serde::{Deserialize, Serialize};

/// Pipeline phases for checkpoint tracking.
///
/// These phases represent the major stages of the Ralph pipeline.
/// Reducers transition between phases based on events.
///
/// # Phase Transitions
///
/// ```text
/// Planning → Development → Review → CommitMessage → FinalValidation → Finalizing → Complete
///              ↓             ↓            ↓
///         AwaitingDevFix → Interrupted
/// ```
///
/// # Phase Descriptions
///
/// - **Planning**: Generate implementation plan for the iteration
/// - **Development**: Execute plan, write code
/// - **Review**: Review code changes, identify issues
/// - **`CommitMessage`**: Generate commit message
/// - **`FinalValidation`**: Final checks before completion
/// - **Finalizing**: Cleanup operations (restore permissions, etc.)
/// - **Complete**: Pipeline completed successfully
/// - **`AwaitingDevFix`**: Terminal failure occurred, dev agent diagnosing
/// - **Interrupted**: Pipeline terminated (success or failure)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PipelinePhase {
    Planning,
    Development,
    Review,
    CommitMessage,
    FinalValidation,
    /// Finalizing phase for cleanup operations before completion.
    ///
    /// This phase handles:
    /// - Restoring PROMPT.md write permissions
    /// - Any other cleanup that must go through the effect system
    Finalizing,
    Complete,
    /// Awaiting development agent to fix pipeline failure.
    ///
    /// This phase occurs when the pipeline encounters a terminal failure condition
    /// (e.g., agent chain exhausted) but before transitioning to Interrupted. It
    /// signals that the development agent should be invoked to diagnose and fix
    /// the failure root cause.
    ///
    /// ## Failure Handling Flow
    ///
    /// 1. `ErrorEvent::AgentChainExhausted` occurs in any phase
    /// 2. Reducer transitions state to `AwaitingDevFix`
    /// 3. Orchestration determines `Effect::TriggerDevFixFlow`
    /// 4. Handler executes `TriggerDevFixFlow`:
    ///    a. Writes completion marker to .`agent/tmp/completion_marker` (failure status)
    ///    b. Emits `DevFixTriggered` event
    ///    c. Dispatches dev-fix agent
    ///    d. Emits `DevFixCompleted` event
    ///    e. Emits `CompletionMarkerEmitted` event
    /// 5. DevFixTriggered/DevFixCompleted events: no state change (stays in `AwaitingDevFix`)
    /// 6. `CompletionMarkerEmitted` event: transitions to Interrupted
    /// 7. Orchestration determines `Effect::SaveCheckpoint` for Interrupted
    /// 8. Handler saves checkpoint, increments `checkpoint_saved_count`
    /// 9. Event loop recognizes `is_complete()` == true and exits successfully
    ///
    /// ## Event Loop Termination Guarantees
    ///
    /// The event loop MUST NOT exit with completed=false when in `AwaitingDevFix` phase.
    /// The failure handling flow is designed to always complete with:
    /// - Completion marker written to filesystem
    /// - State transitioned to Interrupted
    /// - Checkpoint saved (`checkpoint_saved_count` > 0)
    /// - Event loop returning completed=true
    ///
    /// If the event loop exits with completed=false from `AwaitingDevFix`, this indicates
    /// a critical bug (e.g., max iterations reached before checkpoint saved).
    ///
    /// ## Completion Marker Requirement
    ///
    /// The completion marker MUST be written before transitioning to Interrupted.
    /// This ensures external orchestration systems (CI, monitoring) can detect
    /// pipeline termination even if the event loop exits unexpectedly.
    ///
    /// ## Agent Chain Exhaustion Handling
    ///
    /// When in `AwaitingDevFix` phase with an exhausted agent chain, orchestration
    /// falls through to phase-specific logic (`TriggerDevFixFlow`) instead of reporting
    /// exhaustion again. This prevents infinite loops where exhaustion is reported
    /// repeatedly.
    ///
    /// Transitions:
    /// - From: Any phase where `AgentChainExhausted` error occurs
    /// - To: Interrupted (after dev-fix attempt completes or fails)
    AwaitingDevFix,
    Interrupted,
}

impl std::fmt::Display for PipelinePhase {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Planning => write!(f, "Planning"),
            Self::Development => write!(f, "Development"),
            Self::Review => write!(f, "Review"),
            Self::CommitMessage => write!(f, "Commit Message"),
            Self::FinalValidation => write!(f, "Final Validation"),
            Self::Finalizing => write!(f, "Finalizing"),
            Self::Complete => write!(f, "Complete"),
            Self::AwaitingDevFix => write!(f, "Awaiting Dev Fix"),
            Self::Interrupted => write!(f, "Interrupted"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pipeline_phase_display() {
        assert_eq!(format!("{}", PipelinePhase::Planning), "Planning");
        assert_eq!(format!("{}", PipelinePhase::Development), "Development");
        assert_eq!(format!("{}", PipelinePhase::Review), "Review");
        assert_eq!(
            format!("{}", PipelinePhase::CommitMessage),
            "Commit Message"
        );
        assert_eq!(
            format!("{}", PipelinePhase::FinalValidation),
            "Final Validation"
        );
        assert_eq!(format!("{}", PipelinePhase::Finalizing), "Finalizing");
        assert_eq!(format!("{}", PipelinePhase::Complete), "Complete");
        assert_eq!(
            format!("{}", PipelinePhase::AwaitingDevFix),
            "Awaiting Dev Fix"
        );
        assert_eq!(format!("{}", PipelinePhase::Interrupted), "Interrupted");
    }
}
