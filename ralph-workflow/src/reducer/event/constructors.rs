// NOTE: split from reducer/event.rs to keep the facade small.
// Constructors are further split by event category to keep files under 500 lines.
use super::{
    AgentErrorKind, AgentEvent, AgentRole, CheckpointTrigger, CommitEvent, DevelopmentEvent,
    DevelopmentStatus, LifecycleEvent, MaterializedPromptInput, PathBuf, PipelineEvent,
    PipelinePhase, PlanningEvent, PromptInputEvent, PromptInputKind, RebaseEvent, RebasePhase,
    ReviewEvent, TimeoutOutputKind,
};
use crate::agents::AgentDrain;
type ChildProcessInfo = crate::executor::ChildProcessInfo;

// Include constructor implementations split by category
include!("constructors_lifecycle.rs");
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
        Self::Lifecycle(LifecycleEvent::GitignoreEntriesEnsured {
            added: entries_added,
            existing: already_present,
            created: file_created,
        })
    }
}
