//! Event loop iteration control logic.
//!
//! This module contains functions that determine when the event loop should
//! exit based on the current pipeline state. The iteration control ensures:
//! - Terminal states cause loop exit
//! - Special cases (checkpoints, dev-fix, permission restoration) get their required iterations
//! - Defensive completion markers are written before exit

use crate::reducer::event::PipelinePhase;
use crate::reducer::state::PipelineState;

/// Determine if we should exit the loop BEFORE executing the next effect.
///
/// Returns true if the state is already complete, with exceptions for:
/// - Interrupted from `AwaitingDevFix` without checkpoint (need `SaveCheckpoint`)
/// - `AwaitingDevFix` without `dev_fix_triggered` (need `TriggerDevFixFlow`)
/// - Restoration pending (need `RestorePromptPermissions`)
///
/// # Rationale
///
/// When resuming from an Interrupted checkpoint, the state is already complete
/// but we still need to allow one iteration to execute any pending `SaveCheckpoint`
/// effect. Similarly, when entering `AwaitingDevFix`, we must execute `TriggerDevFixFlow`
/// to write the completion marker before exiting.
///
/// # Example
///
/// ```ignore
/// if should_exit_before_effect(&state) {
///     break; // State is terminal and no pending work
/// }
/// // Otherwise, execute the next effect
/// ```
pub(super) const fn should_exit_before_effect(state: &PipelineState) -> bool {
    if !state.is_complete() {
        return false;
    }

    let should_allow_checkpoint_save = matches!(state.phase, PipelinePhase::Interrupted)
        && matches!(state.previous_phase, Some(PipelinePhase::AwaitingDevFix))
        && state.checkpoint_saved_count == 0;

    let is_awaiting_dev_fix_not_triggered =
        matches!(state.phase, PipelinePhase::AwaitingDevFix) && !state.dev_fix_triggered;

    // Allow one more iteration if restoration is pending (keep loop running)
    let should_allow_restoration =
        state.prompt_permissions.restore_needed && !state.prompt_permissions.restored;

    !should_allow_checkpoint_save && !is_awaiting_dev_fix_not_triggered && !should_allow_restoration
}

/// Determine if we should exit the loop AFTER executing an effect.
///
/// Similar logic to `should_exit_before_effect`, but checks after state transitions.
/// This ensures that transitions to terminal phases (e.g., Interrupted) have a
/// chance to save their checkpoint before the loop exits.
///
/// # Example
///
/// ```ignore
/// let new_state = reduce(state, event);
/// if should_exit_after_effect(&new_state) {
///     break; // State became terminal after this effect
/// }
/// ```
pub(super) const fn should_exit_after_effect(state: &PipelineState) -> bool {
    should_exit_before_effect(state)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_should_exit_before_effect_allows_restoration() {
        // Given: Terminal state (Interrupted) but restoration pending
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Interrupted;
        state.checkpoint_saved_count = 1; // Already saved
        state.prompt_permissions.locked = true;
        state.prompt_permissions.restore_needed = true;
        state.prompt_permissions.restored = false; // Restoration pending

        // When: Check if should exit
        let should_exit = should_exit_before_effect(&state);

        // Then: Should NOT exit, must allow restoration to complete
        assert!(
            !should_exit,
            "should_exit_before_effect must return false when restoration pending"
        );

        // After restoration completes, should allow exit
        state.prompt_permissions.restored = true;
        let should_exit_after = should_exit_before_effect(&state);
        assert!(
            should_exit_after,
            "should_exit_before_effect should return true after restoration"
        );
    }

    #[test]
    fn test_should_exit_before_effect_complete_phase_with_restoration_pending() {
        // Given: Complete phase but restoration pending (edge case, shouldn't happen)
        let mut state = PipelineState::initial(0, 0);
        state.phase = PipelinePhase::Complete;
        state.prompt_permissions.locked = true;
        state.prompt_permissions.restore_needed = true;
        state.prompt_permissions.restored = false;

        // When: Check if should exit
        let should_exit = should_exit_before_effect(&state);

        // Then: Should NOT exit until restoration completes
        assert!(
            !should_exit,
            "Even in Complete phase, must allow restoration if pending"
        );
    }

    #[test]
    fn test_should_exit_after_effect_delegates_to_before() {
        // Given: Any state
        let state = PipelineState::initial(1, 0);

        // When/Then: should_exit_after_effect should have same behavior as should_exit_before_effect
        assert_eq!(
            should_exit_before_effect(&state),
            should_exit_after_effect(&state),
            "should_exit_after_effect should delegate to should_exit_before_effect"
        );
    }
}
