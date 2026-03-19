//! Reducer function for state transitions.
//!
//! Implements pure state reduction - no side effects, exhaustive pattern matching.
//!
//! # Architecture
//!
//! The main `reduce` function routes events to category-specific handlers based
//! on event type, providing type-safe dispatch:
//!
//! | Category     | Handler                    | Responsibility                    |
//! |--------------|----------------------------|-----------------------------------|
//! | Lifecycle    | `reduce_lifecycle_event`     | Pipeline start/resume/complete    |
//! | Planning     | `reduce_planning_event`      | Plan generation                   |
//! | Development  | `reduce_development_event`   | Dev iterations, continuation      |
//! | Review       | `reduce_review_event`        | Review passes, fix attempts       |
//! | Agent        | `reduce_agent_event`         | Agent chain, fallback, retries    |
//! | Rebase       | `reduce_rebase_event`        | Rebase state machine              |
//! | Commit       | `reduce_commit_event`        | Commit message generation         |
//!
//! Each handler is a pure function that takes state and its specific event type,
//! enabling compile-time verification of exhaustive matching within each category.

use super::event::PipelineEvent;
use super::state::PipelineState;

/// Pure reducer - no side effects, exhaustive match.
///
/// Computes new state by applying an event to current state.
/// This function has zero side effects - all state mutations are explicit.
///
/// # Event Routing
///
/// Events are routed to category-specific reducers based on their type:
///
/// | Category     | Handler                    | Responsibility                    |
/// |--------------|----------------------------|-----------------------------------|
/// | Lifecycle    | `reduce_lifecycle_event`     | Pipeline start/resume/complete    |
/// | Planning     | `reduce_planning_event`      | Plan generation                   |
/// | Development  | `reduce_development_event`   | Dev iterations, continuation      |
/// | Review       | `reduce_review_event`        | Review passes, fix attempts       |
/// | Agent        | `reduce_agent_event`         | Agent chain, fallback, retries    |
/// | Rebase       | `reduce_rebase_event`        | Rebase state machine              |
/// | Commit       | `reduce_commit_event`        | Commit message generation         |
///
/// Miscellaneous events are handled directly in this function.
#[must_use]
pub fn reduce(state: PipelineState, event: PipelineEvent) -> PipelineState {
    match event {
        // Route to category-specific reducers
        PipelineEvent::Lifecycle(ref e) => lifecycle::reduce_lifecycle_event(state, e),
        PipelineEvent::Planning(e) => planning::reduce_planning_event(state, e),
        PipelineEvent::Development(e) => development::reduce_development_event(state, e),
        PipelineEvent::Review(e) => review::reduce_review_event(state, e),
        PipelineEvent::PromptInput(e) => prompt_input::reduce_prompt_input_event(state, e),
        PipelineEvent::Agent(e) => agent::reduce_agent_event(state, e),
        PipelineEvent::Rebase(e) => rebase::reduce_rebase_event(state, e),
        PipelineEvent::Commit(e) => commit::reduce_commit_event(state, e),
        PipelineEvent::AwaitingDevFix(e) => {
            awaiting_dev_fix::reduce_awaiting_dev_fix_event(state, e)
        }

        // Handle miscellaneous events directly
        PipelineEvent::ContextCleaned => PipelineState {
            context_cleaned: true,
            ..state
        },
        PipelineEvent::CheckpointSaved { .. } => {
            let checkpoint_saved_count = state.checkpoint_saved_count.saturating_add(1);
            PipelineState {
                checkpoint_saved_count,
                ..state
            }
        }
        PipelineEvent::FinalizingStarted => PipelineState {
            phase: super::event::PipelinePhase::Finalizing,
            ..state
        },
        PipelineEvent::PromptPermissionsRestored => {
            // Phase-aware transition:
            // - Finalizing → Complete (success path)
            // - Interrupted → Interrupted (failure path, keep phase)
            // - Other phases → unchanged (shouldn't happen, defensive)
            let new_phase = match state.phase {
                super::event::PipelinePhase::Finalizing => super::event::PipelinePhase::Complete,
                _ => state.phase, // Preserve phase (especially Interrupted)
            };

            PipelineState {
                phase: new_phase,
                prompt_permissions: crate::reducer::state::PromptPermissionsState {
                    locked: false,
                    restore_needed: false,
                    restored: true,
                    last_warning: state.prompt_permissions.last_warning,
                },
                ..state
            }
        }
        PipelineEvent::LoopRecoveryTriggered { .. } => {
            // Reset all retry and loop state to break the loop
            let artifact = state
                .continuation
                .current_artifact
                .unwrap_or(super::state::ArtifactType::Plan);

            // Note: iteration and reviewer_pass counters are preserved via ..state spread.
            // This is intentional - loop recovery breaks the tight loop but allows the
            // pipeline to continue from the same iteration/pass that was in progress.
            PipelineState {
                continuation: state.continuation.reset().with_artifact(artifact),
                // Clear agent session to force fresh invocation.
                agent_chain: state.agent_chain.clear_session_id(),
                ..state
            }
        }
    }
}

// ============================================================================
// Category-specific reducers (split into modules)
// ============================================================================

#[path = "state_reduction/agent.rs"]
mod agent;
#[path = "state_reduction/awaiting_dev_fix.rs"]
mod awaiting_dev_fix;
#[path = "state_reduction/commit/mod.rs"]
mod commit;
#[path = "state_reduction/development/mod.rs"]
mod development;
#[path = "state_reduction/error.rs"]
mod error;
#[path = "state_reduction/lifecycle.rs"]
mod lifecycle;
#[path = "state_reduction/planning.rs"]
mod planning;
#[path = "state_reduction/prompt_input.rs"]
mod prompt_input;
#[path = "state_reduction/rebase.rs"]
mod rebase;
#[path = "state_reduction/review/mod.rs"]
mod review;

#[cfg(test)]
#[path = "state_reduction/io_tests/mod.rs"]
mod tests;
