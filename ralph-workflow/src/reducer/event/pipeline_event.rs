//! The top-level `PipelineEvent` enum.

use serde::{Deserialize, Serialize};

use super::agent::AgentEvent;
use super::development::DevelopmentEvent;
use super::review::ReviewEvent;
use super::types::{
    AwaitingDevFixEvent, CheckpointTrigger, CommitEvent, LifecycleEvent, PlanningEvent,
    PromptInputEvent, RebaseEvent,
};

/// Pipeline events representing all state transitions.
///
/// Events are organized into logical categories for type-safe routing
/// to category-specific reducers. Each category has a dedicated inner enum.
///
/// # Event Categories
///
/// - `Lifecycle` - Pipeline start/stop/resume
/// - `Planning` - Plan generation events
/// - `Development` - Development iteration and continuation events
/// - `Review` - Review pass and fix attempt events
/// - `Agent` - Agent invocation and chain management events
/// - `Rebase` - Git rebase operation events
/// - `Commit` - Commit generation events
/// - Miscellaneous events (context cleanup, checkpoints, finalization)
///
/// # Example
///
/// ```rust,ignore
/// // Type-safe event construction
/// let event = PipelineEvent::Agent(AgentEvent::InvocationStarted {
///     role: AgentRole::Developer,
///     agent: "claude".to_string(),
///     model: Some("opus".to_string()),
/// });
///
/// // Pattern matching routes to category handlers
/// match event {
///     PipelineEvent::Agent(agent_event) => reduce_agent_event(state, agent_event),
///     // ...
/// }
/// ```
///
/// # ⚠️ FROZEN - DO NOT ADD VARIANTS ⚠️
///
/// This enum is **FROZEN**. Adding new top-level variants is **PROHIBITED**.
///
/// ## Why is this frozen?
///
/// `PipelineEvent` provides category-based event routing to the reducer. The existing
/// categories (Lifecycle, Planning, Development, Review, etc.) cover all pipeline phases.
/// Adding new top-level variants would indicate a missing architectural abstraction or
/// an attempt to bypass phase-specific event handling.
///
/// ## What to do instead
///
/// 1. **Express events through existing categories** - Use the category enums:
///    - `PlanningEvent` for planning phase observations
///    - `DevelopmentEvent` for development phase observations
///    - `ReviewEvent` for review phase observations
///    - `CommitEvent` for commit generation observations
///    - `AgentEvent` for agent invocation observations
///    - `RebaseEvent` for rebase state machine transitions
///
/// 2. **Return errors for unrecoverable failures** - Don't create events for conditions
///    that should terminate the pipeline. Return `Err` from the effect handler instead.
///
/// 3. **Extend category enums if needed** - If you truly need a new event within an
///    existing phase, add it to that phase's category enum (e.g., add a new variant to
///    `ReviewEvent` rather than creating a new top-level category).
///
/// ## Enforcement
///
/// The freeze policy is enforced by the `pipeline_event_is_frozen` test in this module,
/// which will fail to compile if new variants are added. This is intentional.
///
/// See `LifecycleEvent` documentation for additional context on the freeze policy rationale.
#[derive(Clone, Serialize, Deserialize, Debug)]
pub enum PipelineEvent {
    /// Pipeline lifecycle events (start, stop, resume).
    Lifecycle(LifecycleEvent),
    /// Planning phase events.
    Planning(PlanningEvent),
    /// Development phase events.
    Development(DevelopmentEvent),
    /// Review phase events.
    Review(ReviewEvent),
    /// Prompt input materialization events.
    PromptInput(PromptInputEvent),
    /// Agent invocation and chain events.
    Agent(AgentEvent),
    /// Rebase operation events.
    Rebase(RebaseEvent),
    /// Commit generation events.
    Commit(CommitEvent),
    /// `AwaitingDevFix` phase events.
    AwaitingDevFix(AwaitingDevFixEvent),

    // ========================================================================
    // Miscellaneous events that don't fit a category
    // ========================================================================
    /// Context cleanup completed.
    ContextCleaned,
    /// Checkpoint saved.
    CheckpointSaved {
        /// What triggered the checkpoint save.
        trigger: CheckpointTrigger,
    },
    /// Finalization phase started.
    FinalizingStarted,
    /// PROMPT.md permissions restored.
    PromptPermissionsRestored,
    /// Loop recovery triggered (tight loop detected and broken).
    LoopRecoveryTriggered {
        /// String representation of the detected loop.
        detected_loop: String,
        /// Number of times the loop was repeated.
        loop_count: u32,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    /// This test enforces the FROZEN policy on `LifecycleEvent`.
    ///
    /// If you're here because this test failed to compile after adding
    /// a variant, you are violating the freeze policy. See the FROZEN
    /// comment on `LifecycleEvent` for alternatives.
    #[test]
    fn lifecycle_event_is_frozen() {
        fn exhaustive_match(e: &LifecycleEvent) -> &'static str {
            match e {
                LifecycleEvent::Started => "started",
                LifecycleEvent::Resumed { .. } => "resumed",
                LifecycleEvent::Completed => "completed",
                LifecycleEvent::GitignoreEntriesEnsured { .. } => "gitignore_ensured",
                // DO NOT ADD _ WILDCARD - intentionally exhaustive
            }
        }
        // Just needs to compile; actual call proves exhaustiveness
        let _ = exhaustive_match(&LifecycleEvent::Started);
    }

    /// This test enforces the FROZEN policy on `PipelineEvent`.
    ///
    /// If you're here because this test failed to compile after adding
    /// a variant, you are violating the freeze policy. See the FROZEN
    /// comment on `PipelineEvent` for alternatives.
    #[test]
    fn pipeline_event_is_frozen() {
        fn exhaustive_match(e: &PipelineEvent) -> &'static str {
            match e {
                PipelineEvent::Lifecycle(_) => "lifecycle",
                PipelineEvent::Planning(_) => "planning",
                PipelineEvent::Development(_) => "development",
                PipelineEvent::Review(_) => "review",
                PipelineEvent::PromptInput(_) => "prompt_input",
                PipelineEvent::Agent(_) => "agent",
                PipelineEvent::Rebase(_) => "rebase",
                PipelineEvent::Commit(_) => "commit",
                PipelineEvent::AwaitingDevFix(_) => "awaiting_dev_fix",
                PipelineEvent::ContextCleaned => "context_cleaned",
                PipelineEvent::CheckpointSaved { .. } => "checkpoint_saved",
                PipelineEvent::FinalizingStarted => "finalizing_started",
                PipelineEvent::PromptPermissionsRestored => "prompt_permissions_restored",
                PipelineEvent::LoopRecoveryTriggered { .. } => "loop_recovery_triggered",
                // DO NOT ADD _ WILDCARD - intentionally exhaustive
            }
        }
        let _ = exhaustive_match(&PipelineEvent::ContextCleaned);
    }
}
