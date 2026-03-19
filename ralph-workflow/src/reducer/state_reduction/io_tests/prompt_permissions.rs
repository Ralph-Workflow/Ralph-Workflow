//! Tests for prompt permission state transitions.

use crate::reducer::create_test_state;
use crate::reducer::event::{PipelineEvent, PipelinePhase, PromptInputEvent};
use crate::reducer::state::PipelineState;
use crate::reducer::state::PromptPermissionsState;
use crate::reducer::state_reduction::reduce;

#[test]
fn test_reduce_prompt_permissions_locked_sets_flags() {
    // Given: Initial state with no permission tracking
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        ..PipelineState::initial(5, 2)
    };

    // When: PromptPermissionsLocked event is reduced
    let event =
        PipelineEvent::PromptInput(PromptInputEvent::PromptPermissionsLocked { warning: None });
    let new_state = reduce(state, event);

    // Then: State flags should be set
    assert!(
        new_state.prompt_permissions.locked,
        "locked flag should be true"
    );
    assert!(
        new_state.prompt_permissions.restore_needed,
        "restore_needed should be true"
    );
    assert!(
        !new_state.prompt_permissions.restored,
        "restored should still be false"
    );
    assert_eq!(new_state.prompt_permissions.last_warning, None);
}

#[test]
fn test_reduce_prompt_permissions_locked_with_warning() {
    let state = create_test_state();
    let warning_msg = "Failed to set readonly: permission denied".to_string();

    let event = PipelineEvent::PromptInput(PromptInputEvent::PromptPermissionsLocked {
        warning: Some(warning_msg.clone()),
    });
    let new_state = reduce(state, event);

    assert!(
        !new_state.prompt_permissions.locked,
        "locked should be false when lock attempt failed"
    );
    assert!(
        !new_state.prompt_permissions.restore_needed,
        "restore_needed should be false when no lock was applied"
    );
    assert_eq!(new_state.prompt_permissions.last_warning, Some(warning_msg));
}

#[test]
fn test_reduce_prompt_permissions_restored_finalizing_to_complete() {
    // Given: State in Finalizing phase (success path)
    let state = PipelineState {
        phase: PipelinePhase::Finalizing,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..create_test_state()
    };

    // When: PromptPermissionsRestored event is reduced
    let new_state = reduce(state, PipelineEvent::prompt_permissions_restored());

    // Then: Should transition to Complete
    assert_eq!(new_state.phase, PipelinePhase::Complete);
    assert!(new_state.prompt_permissions.restored);
}

#[test]
fn test_reduce_prompt_permissions_restored_preserves_interrupted_phase() {
    // Given: State in Interrupted phase (failure path after AwaitingDevFix)
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        previous_phase: Some(PipelinePhase::AwaitingDevFix),
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..create_test_state()
    };

    // When: PromptPermissionsRestored event is reduced
    let new_state = reduce(state, PipelineEvent::prompt_permissions_restored());

    // Then: Should stay in Interrupted, NOT transition to Complete
    assert_eq!(new_state.phase, PipelinePhase::Interrupted);
    assert!(new_state.prompt_permissions.restored);
}

#[test]
fn test_reduce_prompt_permissions_restored_clears_lock_flags() {
    // Given: State with permissions locked and restoration pending
    let state = PipelineState {
        phase: PipelinePhase::Finalizing,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..create_test_state()
    };

    // When: PromptPermissionsRestored event is reduced
    let new_state = reduce(state, PipelineEvent::prompt_permissions_restored());

    // Then: Lock flags should be cleared after restoration
    assert!(!new_state.prompt_permissions.locked);
    assert!(!new_state.prompt_permissions.restore_needed);
    assert!(new_state.prompt_permissions.restored);
}

#[test]
fn test_reduce_prompt_permissions_locked_idempotent() {
    // Given: State already locked
    let state = PipelineState {
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..create_test_state()
    };

    // When: Apply PromptPermissionsLocked again
    let event =
        PipelineEvent::PromptInput(PromptInputEvent::PromptPermissionsLocked { warning: None });
    let new_state = reduce(state, event);

    // Then: State should remain consistent (idempotent)
    assert!(new_state.prompt_permissions.locked);
    assert!(new_state.prompt_permissions.restore_needed);
    assert!(!new_state.prompt_permissions.restored);
}

#[test]
fn test_reduce_prompt_permissions_locked_clears_restored_flag() {
    // Given: State marked restored from a prior run
    let state = PipelineState {
        prompt_permissions: PromptPermissionsState {
            locked: false,
            restore_needed: false,
            restored: true,
            last_warning: None,
        },
        ..create_test_state()
    };

    // When: PromptPermissionsLocked event is reduced
    let event =
        PipelineEvent::PromptInput(PromptInputEvent::PromptPermissionsLocked { warning: None });
    let new_state = reduce(state, event);

    // Then: restored should be cleared to ensure restoration runs at exit
    assert!(!new_state.prompt_permissions.restored);
    assert!(new_state.prompt_permissions.locked);
    assert!(new_state.prompt_permissions.restore_needed);
}

#[test]
fn test_reduce_prompt_permissions_restored_idempotent() {
    // Given: State already restored
    let state = PipelineState {
        phase: PipelinePhase::Finalizing,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: true,
            last_warning: None,
        },
        ..create_test_state()
    };

    // When: Apply PromptPermissionsRestored again
    let new_state = reduce(state, PipelineEvent::prompt_permissions_restored());

    // Then: Should transition to Complete (or stay Complete if already there)
    assert_eq!(new_state.phase, PipelinePhase::Complete);
    assert!(new_state.prompt_permissions.restored);
}

#[test]
fn test_reduce_prompt_permissions_restore_warning_updates_last_warning() {
    let state = PipelineState {
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..create_test_state()
    };
    let warning_msg = "Failed to restore write permissions: permission denied".to_string();

    let event = PipelineEvent::PromptInput(PromptInputEvent::PromptPermissionsRestoreWarning {
        warning: warning_msg.clone(),
    });
    let new_state = reduce(state, event);

    assert_eq!(new_state.prompt_permissions.last_warning, Some(warning_msg));
    assert!(new_state.prompt_permissions.locked);
    assert!(new_state.prompt_permissions.restore_needed);
    assert!(!new_state.prompt_permissions.restored);
}
