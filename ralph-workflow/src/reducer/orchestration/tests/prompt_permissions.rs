//! Tests for prompt permission orchestration.

use super::*;
use crate::reducer::state::PromptPermissionsState;

#[test]
fn test_orchestration_derives_lock_permissions_before_planning() {
    // Given: Initial state in Planning phase with permissions not locked
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        prompt_permissions: PromptPermissionsState {
            locked: false,
            restore_needed: false,
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(1, 0)
    };

    // When: Determining next effect
    let effect = determine_next_effect(&state);

    // Then: Should derive LockPromptPermissions before planning work
    assert!(
        matches!(effect, Effect::LockPromptPermissions),
        "Expected LockPromptPermissions, got {effect:?}"
    );
}

#[test]
fn test_orchestration_skips_lock_when_already_locked() {
    use crate::agents::AgentRole;
    use crate::reducer::state::AgentChainState;

    // Given: State with permissions already locked
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        gitignore_entries_ensured: true, // Skip gitignore effect
        context_cleaned: true,           // Skip context cleanup effect
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec!["model1".to_string()]],
                AgentRole::Developer,
            )
            .with_drain(crate::agents::AgentDrain::Planning),
        ..PipelineState::initial(1, 0)
    };

    // When: Determining next effect
    let effect = determine_next_effect(&state);

    // Then: Should proceed to phase-specific work, not re-lock
    // (After lock, first planning effect could be MaterializePlanningInputs or PreparePlanningPrompt)
    assert!(
        matches!(
            effect,
            Effect::MaterializePlanningInputs { .. } | Effect::PreparePlanningPrompt { .. }
        ),
        "Expected planning work, got {effect:?}"
    );
}

#[test]
fn test_orchestration_derives_restore_permissions_on_interrupted() {
    // Given: State in Interrupted phase with restoration pending
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        previous_phase: Some(PipelinePhase::AwaitingDevFix),
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        checkpoint_saved_count: 0,
        ..PipelineState::initial(1, 0)
    };

    // When: Determining next effect
    let effect = determine_next_effect(&state);

    // Then: Should derive RestorePromptPermissions before SaveCheckpoint
    assert!(
        matches!(effect, Effect::RestorePromptPermissions),
        "Expected RestorePromptPermissions, got {effect:?}"
    );
}

#[test]
fn test_orchestration_saves_checkpoint_after_restore_on_interrupted() {
    // Given: State in Interrupted with permissions already restored
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        previous_phase: Some(PipelinePhase::AwaitingDevFix),
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: true,
            last_warning: None,
        },
        checkpoint_saved_count: 0,
        ..PipelineState::initial(1, 0)
    };

    // When: Determining next effect
    let effect = determine_next_effect(&state);

    // Then: Must still run the pre-termination commit safety check before checkpointing.
    assert!(
        matches!(effect, Effect::CheckUncommittedChangesBeforeTermination),
        "Expected CheckUncommittedChangesBeforeTermination, got {effect:?}"
    );
}

#[test]
fn test_orchestration_saves_checkpoint_after_restore_and_safety_check_on_interrupted() {
    // Given: State in Interrupted with permissions restored and safety check already passed
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        previous_phase: Some(PipelinePhase::AwaitingDevFix),
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: true,
            last_warning: None,
        },
        checkpoint_saved_count: 0,
        interrupted_by_user: false,
        pre_termination_commit_checked: true,
        ..PipelineState::initial(1, 0)
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::SaveCheckpoint { .. }),
        "Expected SaveCheckpoint, got {effect:?}"
    );
}

#[test]
fn test_finalizing_always_derives_restore_permissions() {
    // Given: State in Finalizing phase with restoration pending
    let state = PipelineState {
        phase: PipelinePhase::Finalizing,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(1, 0)
    };

    // When: Determine next effect
    let effect = determine_next_effect(&state);

    // Then: Should always derive RestorePromptPermissions
    assert!(
        matches!(effect, Effect::RestorePromptPermissions),
        "Finalizing must derive RestorePromptPermissions, got {effect:?}"
    );
}

/// Test that `RestorePromptPermissions` is emitted on user interrupt even when `restore_needed=false`.
///
/// This covers Gap 1: early Ctrl+C before `LockPromptPermissions` executed.
/// Even if this run didn't lock PROMPT.md, a prior crashed run may have left it read-only.
#[test]
fn test_interrupted_phase_restores_prompt_md_when_restore_not_needed() {
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        interrupted_by_user: true,
        // Key: restore_needed=false simulates early interrupt before lock
        prompt_permissions: PromptPermissionsState {
            locked: false,
            restore_needed: false, // NOT needed by this run
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(1, 0)
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::RestorePromptPermissions),
        "Expected RestorePromptPermissions even when restore_needed=false, got {effect:?}. \
         User interrupts should ALWAYS attempt restoration for safety."
    );
}

/// Test that non-user interrupt runs the pre-termination safety check before cleanup.
///
/// Programmatic interrupts must not terminate with uncommitted work; they must run
/// `CheckUncommittedChangesBeforeTermination` first.
#[test]
fn test_programmatic_interrupt_requires_pre_termination_safety_check() {
    let mut state = PipelineState {
        phase: PipelinePhase::Interrupted,
        interrupted_by_user: false,
        pre_termination_commit_checked: false, // Safety check NOT passed
        prompt_permissions: PromptPermissionsState {
            locked: false,
            restore_needed: false,
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(1, 0)
    };
    state.previous_phase = Some(PipelinePhase::AwaitingDevFix);

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::CheckUncommittedChangesBeforeTermination),
        "Expected CheckUncommittedChangesBeforeTermination for programmatic interrupt, got {effect:?}"
    );
}
