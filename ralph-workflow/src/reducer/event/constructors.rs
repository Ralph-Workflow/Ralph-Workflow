// NOTE: split from reducer/event.rs to keep the facade small.
// Constructors are further split by event category to keep files under 500 lines.
use super::{
    AgentErrorKind, AgentEvent, AgentRole, CheckpointTrigger, CommitEvent, DevelopmentEvent,
    DevelopmentStatus, MaterializedPromptInput, PathBuf, PipelineEvent, PipelinePhase,
    PlanningEvent, PromptInputEvent, PromptInputKind, RebaseEvent, RebasePhase, ReviewEvent,
    TimeoutOutputKind,
};
use crate::agents::AgentDrain;
type ChildProcessInfo = crate::executor::ChildProcessInfo;

// Include constructor implementations split by category
include!("constructors_prompt_input.rs");
include!("constructors_development.rs");
include!("constructors_review.rs");
include!("constructors_agent.rs");
include!("constructors_commit.rs");

// ============================================================================
// Miscellaneous event constructors
// ============================================================================

impl PipelineEvent {
    /// Construct a `LoopRecoveryTriggered` event.
    #[must_use]
    pub const fn loop_recovery_triggered(detected_loop: String, loop_count: u32) -> Self {
        Self::LoopRecoveryTriggered {
            detected_loop,
            loop_count,
        }
    }

    /// Create a `GitignoreEntriesEnsured` event.
    #[must_use]
    pub const fn gitignore_entries_ensured(
        entries_added: Vec<String>,
        already_present: Vec<String>,
        file_created: bool,
    ) -> Self {
        Self::PromptInput(PromptInputEvent::GitignoreEntriesEnsured {
            added: entries_added,
            existing: already_present,
            created: file_created,
        })
    }

    /// Create a `PlanningEvent::PromptPrepared` event.
    #[must_use]
    pub const fn planning_prompt_prepared(iteration: u32) -> Self {
        Self::Planning(PlanningEvent::PromptPrepared { iteration })
    }

    /// Create a `PlanningEvent::AgentInvoked` event.
    #[must_use]
    pub const fn planning_agent_invoked(iteration: u32) -> Self {
        Self::Planning(PlanningEvent::AgentInvoked { iteration })
    }

    /// Create a `PlanningEvent::PhaseStarted` event.
    #[must_use]
    pub const fn planning_phase_started() -> Self {
        Self::Planning(PlanningEvent::PhaseStarted)
    }

    /// Create a `PlanningEvent::PhaseCompleted` event.
    #[must_use]
    pub const fn planning_phase_completed() -> Self {
        Self::Planning(PlanningEvent::PhaseCompleted)
    }

    /// Create a `PlanningEvent::GenerationCompleted` event.
    #[must_use]
    pub const fn plan_generation_completed(iteration: u32, valid: bool) -> Self {
        Self::Planning(PlanningEvent::GenerationCompleted { iteration, valid })
    }

    /// Create a `PlanningEvent::PlanXmlExtracted` event.
    #[must_use]
    pub const fn planning_xml_extracted(iteration: u32) -> Self {
        Self::Planning(PlanningEvent::PlanXmlExtracted { iteration })
    }

    /// Create a `PlanningEvent::PlanXmlMissing` event.
    #[must_use]
    pub const fn planning_xml_missing(iteration: u32, attempt: u32) -> Self {
        Self::Planning(PlanningEvent::PlanXmlMissing { iteration, attempt })
    }

    /// Create a `PlanningEvent::PlanXmlValidated` event.
    #[must_use]
    pub const fn planning_xml_validated(
        iteration: u32,
        valid: bool,
        markdown: Option<String>,
    ) -> Self {
        Self::Planning(PlanningEvent::PlanXmlValidated {
            iteration,
            valid,
            markdown,
        })
    }

    /// Create a `PlanningEvent::PlanMarkdownWritten` event.
    #[must_use]
    pub const fn planning_markdown_written(iteration: u32) -> Self {
        Self::Planning(PlanningEvent::PlanMarkdownWritten { iteration })
    }

    /// Create a `PlanningEvent::PlanXmlArchived` event.
    #[must_use]
    pub const fn planning_xml_archived(iteration: u32) -> Self {
        Self::Planning(PlanningEvent::PlanXmlArchived { iteration })
    }

    /// Create a `PlanningEvent::PlanXmlCleaned` event.
    #[must_use]
    pub const fn planning_xml_cleaned(iteration: u32) -> Self {
        Self::Planning(PlanningEvent::PlanXmlCleaned { iteration })
    }

    /// Create a `PlanningEvent::OutputValidationFailed` event.
    #[must_use]
    pub const fn planning_output_validation_failed(iteration: u32, attempt: u32) -> Self {
        Self::Planning(PlanningEvent::OutputValidationFailed { iteration, attempt })
    }
}
