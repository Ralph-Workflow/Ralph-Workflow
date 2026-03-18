//! Tests for event loop iteration control with permission restoration.

use super::iteration::{should_exit_after_effect, should_exit_before_effect};
use crate::reducer::event::PipelinePhase;
use crate::reducer::state::{PipelineState, PromptPermissionsState};

#[test]
fn test_should_not_exit_when_restoration_pending_on_interrupted() {
    // Given: Interrupted state with restoration pending
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        previous_phase: Some(PipelinePhase::AwaitingDevFix),
        checkpoint_saved_count: 0,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(1, 0)
    };

    // When/Then: Should NOT exit yet (restoration must happen)
    assert!(
        !should_exit_before_effect(&state),
        "Should not exit before restoration"
    );
    assert!(
        !should_exit_after_effect(&state),
        "Should not exit before restoration"
    );
}

#[test]
fn test_should_exit_after_restoration_complete_on_interrupted() {
    // Given: Interrupted state with restoration complete and checkpoint saved
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        previous_phase: Some(PipelinePhase::AwaitingDevFix),
        checkpoint_saved_count: 1,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: true,
            last_warning: None,
        },
        ..PipelineState::initial(1, 0)
    };

    // When/Then: Should exit now (restoration complete)
    assert!(
        should_exit_before_effect(&state),
        "Should exit after restoration complete"
    );
}

#[test]
fn test_should_not_exit_when_restoration_pending_on_complete() {
    // Given: Complete state but restoration pending (shouldn't happen, but defensive)
    let state = PipelineState {
        phase: PipelinePhase::Complete,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(1, 0)
    };

    // When/Then: Should NOT exit yet (restoration must happen)
    assert!(
        !should_exit_before_effect(&state),
        "Should not exit before restoration"
    );
}
