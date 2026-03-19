//! Unit tests for state reduction.
//!
//! Tests are organized by phase to keep files manageable and enable parallel test execution.
//! Each module tests a specific aspect of the reducer's event handling.

// Re-export common types for test modules
pub use crate::reducer::event::{PipelineEvent, PipelinePhase};
pub use crate::reducer::state::{CommitState, PipelineState, RebaseState};
pub use crate::reducer::state_reduction::reduce;

// Test modules organized by phase
mod agent_chain;
mod commit_phase;
mod continuation;
mod development_phase;
mod phase_transitions;
mod pipeline_lifecycle;
mod planning_phase;
mod rebase;
mod review_phase;

// ============================================================================
// Shared Test Helpers
// ============================================================================

/// Creates a default test state with 5 iterations and 2 reviewer passes.
/// Permissions are set to locked (simulating mid-pipeline scenario after startup).
#[must_use]
pub fn create_test_state() -> PipelineState {
    let mut state = PipelineState::initial(5, 2);
    // Tests in this module typically simulate mid-pipeline scenarios
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;
    state
}

/// Creates a test state in a specific phase.
#[must_use]
pub fn create_state_in_phase(phase: PipelinePhase) -> PipelineState {
    PipelineState {
        phase,
        ..create_test_state()
    }
}
