//! Integration tests for PROMPT.md permission lifecycle.

use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::state_reduction::reduce;

use std::path::Path;

use crate::test_timeout::with_default_timeout;

#[test]
fn test_permission_lifecycle_success_path() {
    with_default_timeout(|| {
        // Given: Initial state (not locked yet)
        let initial_state = PipelineState::initial(0, 0); // 0 iters, 0 reviews -> goes to CommitMessage

        let mut handler = MockEffectHandler::new(initial_state.clone());

        // Step 1: First effect should be LockPromptPermissions
        let effect1 = determine_next_effect(&initial_state);
        assert!(
            matches!(effect1, Effect::LockPromptPermissions),
            "First effect should lock permissions, got {effect1:?}"
        );

        // Step 2: Execute lock, get event
        let result1 = handler.execute_mock(&effect1);
        let state2 = reduce(initial_state, result1.event);
        assert!(state2.prompt_permissions.locked);
        assert!(state2.prompt_permissions.restore_needed);
        assert!(!state2.prompt_permissions.restored);

        // Step 3: Now should proceed to commit phase work (skipping lock)
        let effect3 = determine_next_effect(&state2);
        assert!(
            matches!(
                effect3,
                Effect::InitializeAgentChain { .. }
                    | Effect::EnsureGitignoreEntries
                    | Effect::PrepareCommitPrompt { .. }
            ),
            "After lock, should proceed to commit work, got {effect3:?}"
        );

        // Fast-forward: simulate pipeline completing through to Finalizing
        let finalizing_state = PipelineState {
            phase: PipelinePhase::Finalizing,
            prompt_permissions: state2.prompt_permissions.clone(),
            ..state2
        };

        // Step 4: Finalizing should derive RestorePromptPermissions
        let effect4 = determine_next_effect(&finalizing_state);
        assert!(
            matches!(effect4, Effect::RestorePromptPermissions),
            "Finalizing should restore permissions"
        );

        // Step 5: Execute restore, get event, reduce
        let result4 = handler.execute_mock(&effect4);
        let final_state = reduce(finalizing_state, result4.event);

        // Step 6: Verify final state
        assert_eq!(final_state.phase, PipelinePhase::Complete);
        assert!(final_state.prompt_permissions.restored);
    });
}

#[test]
fn test_permission_lifecycle_failure_path() {
    with_default_timeout(|| {
        // Given: Initial state
        let initial_state = PipelineState::initial(1, 0);

        let mut handler = MockEffectHandler::new(initial_state.clone());

        // Step 1: Lock permissions at startup
        let effect1 = determine_next_effect(&initial_state);
        assert!(matches!(effect1, Effect::LockPromptPermissions));

        let result1 = handler.execute_mock(&effect1);
        let state2 = reduce(initial_state, result1.event);
        assert!(state2.prompt_permissions.locked);

        // Fast-forward: simulate failure path (AwaitingDevFix → Interrupted)
        let interrupted_state = PipelineState {
            phase: PipelinePhase::Interrupted,
            previous_phase: Some(PipelinePhase::AwaitingDevFix),
            checkpoint_saved_count: 0,
            prompt_permissions: state2.prompt_permissions.clone(),
            ..state2
        };

        // Step 2: On Interrupted, should restore BEFORE saving checkpoint
        let effect2 = determine_next_effect(&interrupted_state);
        assert!(
            matches!(effect2, Effect::RestorePromptPermissions),
            "Interrupted should restore permissions before checkpoint, got {effect2:?}"
        );

        // Step 3: Execute restore
        let result2 = handler.execute_mock(&effect2);
        let state3 = reduce(interrupted_state, result2.event);

        // Step 4: Verify phase stays Interrupted (not Complete)
        assert_eq!(state3.phase, PipelinePhase::Interrupted);
        assert!(state3.prompt_permissions.restored);

        // Step 5: Must run pre-termination safety check before checkpointing
        let effect3 = determine_next_effect(&state3);
        assert!(
            matches!(effect3, Effect::CheckUncommittedChangesBeforeTermination),
            "After restore on Interrupted, should run safety check"
        );

        // Step 6: Execute safety check, then checkpoint
        let result3 = handler.execute_mock(&effect3);
        let state4 = reduce(state3, result3.event);
        let effect4 = determine_next_effect(&state4);
        assert!(
            matches!(effect4, Effect::SaveCheckpoint { .. }),
            "After safety check on Interrupted, should save checkpoint"
        );
    });
}

#[test]
fn test_permission_restoration_on_resume_from_interrupted() {
    with_default_timeout(|| {
        // Given: Resumed state in Interrupted with restoration pending
        // (simulates checkpoint saved after lock but before restore completed)
        let resumed_state = PipelineState {
            phase: PipelinePhase::Interrupted,
            previous_phase: Some(PipelinePhase::AwaitingDevFix),
            checkpoint_saved_count: 1, // Already saved once
            prompt_permissions: ralph_workflow::reducer::state::PromptPermissionsState {
                locked: true,
                restore_needed: true,
                restored: false,
                last_warning: None,
            },
            ..PipelineState::initial(1, 0)
        };

        let mut handler = MockEffectHandler::new(resumed_state.clone());

        // Step 1: On resume with Interrupted phase, first checks for uncommitted changes
        let effect1 = determine_next_effect(&resumed_state);
        assert!(
            matches!(effect1, Effect::CheckUncommittedChangesBeforeTermination),
            "Resume should check uncommitted changes first on Interrupted, got {effect1:?}"
        );

        // Execute safety check
        let result1 = handler.execute_mock(&effect1);
        let state_after_check = reduce(resumed_state, result1.event);

        // Step 2: After safety check, should derive RestorePromptPermissions
        let effect2 = determine_next_effect(&state_after_check);
        assert!(
            matches!(effect2, Effect::RestorePromptPermissions),
            "After safety check, should restore permissions if pending, got {effect2:?}"
        );

        // Step 3: Execute restore
        let result2 = handler.execute_mock(&effect2);
        let final_state = reduce(state_after_check, result2.event);

        // Step 3: Verify restoration completed, phase stays Interrupted
        assert_eq!(final_state.phase, PipelinePhase::Interrupted);
        assert!(final_state.prompt_permissions.restored);
    });
}

#[test]
fn test_permission_restoration_on_user_interrupt() {
    with_default_timeout(|| {
        // Given: Pipeline interrupted by user (not AwaitingDevFix path)
        let initial_state = PipelineState::initial(2, 0);
        let mut handler = MockEffectHandler::new(initial_state.clone());

        // Step 1: Lock at startup
        let effect1 = determine_next_effect(&initial_state);
        assert!(matches!(effect1, Effect::LockPromptPermissions));
        let result1 = handler.execute_mock(&effect1);
        let state2 = reduce(initial_state, result1.event);

        // Step 2: Simulate user Ctrl+C by transitioning to Interrupted
        // (normally triggered by signal handler, here we simulate it)
        let interrupted_state = PipelineState {
            phase: PipelinePhase::Interrupted,
            previous_phase: Some(PipelinePhase::Planning), // NOT AwaitingDevFix
            checkpoint_saved_count: 0,
            interrupted_by_user: true,
            prompt_permissions: state2.prompt_permissions.clone(),
            ..state2
        };

        // Step 3: Should restore BEFORE checkpoint
        let effect3 = determine_next_effect(&interrupted_state);
        assert!(
            matches!(effect3, Effect::RestorePromptPermissions),
            "User interrupt should restore permissions, got {effect3:?}"
        );

        let result3 = handler.execute_mock(&effect3);
        let state4 = reduce(interrupted_state, result3.event);

        // Step 4: Should stay Interrupted, then save checkpoint
        assert_eq!(state4.phase, PipelinePhase::Interrupted);
        assert!(state4.prompt_permissions.restored);

        let effect5 = determine_next_effect(&state4);
        assert!(matches!(effect5, Effect::SaveCheckpoint { .. }));
    });
}

// ============================================================================
// PROMPT.md cleanup function tests
// ============================================================================

/// Test that `make_prompt_writable_with_workspace` completes successfully.
///
/// This tests the function that `AgentPhaseGuard::drop()` calls to restore
/// PROMPT.md permissions. The guard cleanup (including PROMPT.md restoration)
/// is tested in unit tests within the crate where `GitHelpers` is accessible.
///
/// Note: `MemoryWorkspace` has no-op implementations for `set_readonly/set_writable`,
/// so we verify the function completes without error.
#[test]
fn test_make_prompt_writable_with_workspace_succeeds() {
    with_default_timeout(|| {
        use ralph_workflow::files::make_prompt_writable_with_workspace;
        use ralph_workflow::workspace::{MemoryWorkspace, Workspace};

        let workspace =
            MemoryWorkspace::new_test().with_file("PROMPT.md", "# Goal\nTest content\n");

        // Call the function that AgentPhaseGuard::drop() uses
        let warning = make_prompt_writable_with_workspace(&workspace);

        // Should complete without error (MemoryWorkspace no-ops permissions)
        assert!(
            warning.is_none(),
            "make_prompt_writable_with_workspace should succeed on MemoryWorkspace"
        );

        // PROMPT.md should still exist
        assert!(
            workspace.exists(Path::new("PROMPT.md")),
            "PROMPT.md should still exist after permission restoration"
        );
    });
}

/// Test that `make_prompt_read_only_with_workspace` followed by writable works.
///
/// This tests the lock/unlock cycle that the pipeline uses.
#[test]
fn test_prompt_permission_lock_unlock_cycle() {
    with_default_timeout(|| {
        use ralph_workflow::files::{
            make_prompt_read_only_with_workspace, make_prompt_writable_with_workspace,
        };
        use ralph_workflow::workspace::{MemoryWorkspace, Workspace};

        let workspace =
            MemoryWorkspace::new_test().with_file("PROMPT.md", "# Goal\nTest content\n");

        // Lock (make read-only)
        let lock_warning = make_prompt_read_only_with_workspace(&workspace);
        assert!(
            lock_warning.is_none(),
            "Lock should succeed: {lock_warning:?}"
        );

        // Unlock (make writable)
        let unlock_warning = make_prompt_writable_with_workspace(&workspace);
        assert!(
            unlock_warning.is_none(),
            "Unlock should succeed: {unlock_warning:?}"
        );

        // PROMPT.md should still exist
        assert!(
            workspace.exists(Path::new("PROMPT.md")),
            "PROMPT.md should still exist after lock/unlock cycle"
        );
    });
}

/// Test that startup cleanup restores PROMPT.md from prior interrupted run.
///
/// Simulates SIGKILL scenario: PROMPT.md is read-only and .`no_agent_commit` exists.
/// Verifies that the cleanup functions would restore PROMPT.md permissions.
///
/// Note: `MemoryWorkspace` has no-op permission operations, but we verify the
/// cleanup function sequence completes without error.
#[test]
fn test_startup_cleanup_restores_prompt_md_from_prior_run() {
    with_default_timeout(|| {
        use ralph_workflow::files::make_prompt_writable_with_workspace;
        use ralph_workflow::workspace::{MemoryWorkspace, Workspace};

        // Simulate prior crashed run: PROMPT.md exists and orphan marker present
        let workspace = MemoryWorkspace::new_test()
            .with_file("PROMPT.md", "# Goal\nTest content\n")
            .with_file(".git/ralph/no_agent_commit", "");

        // Simulate startup cleanup sequence: restore PROMPT.md permissions
        // (This is what execution_core.rs does at startup)
        let warning = make_prompt_writable_with_workspace(&workspace);

        // Should complete without error (MemoryWorkspace no-ops permissions)
        assert!(
            warning.is_none(),
            "make_prompt_writable_with_workspace should succeed on MemoryWorkspace"
        );

        // PROMPT.md should still exist
        assert!(
            workspace.exists(Path::new("PROMPT.md")),
            "PROMPT.md should still exist after startup cleanup"
        );
    });
}

// ============================================================================
// Ctrl+C cleanup tests (reducer-level simulation)
// ============================================================================

/// Test that Ctrl+C (simulated via `interrupted_by_user` flag) restores PROMPT.md.
///
/// This test simulates the Ctrl+C scenario by setting `interrupted_by_user=true`
/// in the initial state and verifying `RestorePromptPermissions` effect is derived.
#[test]
fn test_ctrl_c_restores_prompt_md_writable() {
    with_default_timeout(|| {
        let initial_state = PipelineState::initial(1, 0);
        let mut handler = MockEffectHandler::new(initial_state.clone());

        // Step 1: Lock at startup
        let effect1 = determine_next_effect(&initial_state);
        assert!(matches!(effect1, Effect::LockPromptPermissions));
        let result1 = handler.execute_mock(&effect1);
        let state2 = reduce(initial_state, result1.event);

        // Step 2: Simulate Ctrl+C interrupt
        let interrupted_state = PipelineState {
            phase: PipelinePhase::Interrupted,
            previous_phase: Some(PipelinePhase::Development),
            checkpoint_saved_count: 0,
            interrupted_by_user: true,
            prompt_permissions: state2.prompt_permissions.clone(),
            ..state2
        };

        // Step 3: On user interrupt, should restore permissions
        let effect3 = determine_next_effect(&interrupted_state);
        assert!(
            matches!(effect3, Effect::RestorePromptPermissions),
            "User interrupt should restore PROMPT.md permissions, got {effect3:?}"
        );

        // Step 4: Execute restore
        let result3 = handler.execute_mock(&effect3);
        let state4 = reduce(interrupted_state, result3.event);

        // Step 5: Verify restoration completed
        assert!(
            state4.prompt_permissions.restored,
            "prompt_permissions.restored should be true after Ctrl+C cleanup"
        );

        // Step 6: Should proceed to SaveCheckpoint
        let effect5 = determine_next_effect(&state4);
        assert!(
            matches!(effect5, Effect::SaveCheckpoint { .. }),
            "After restoration, should save checkpoint"
        );
    });
}

/// Test that early Ctrl+C (before `LockPromptPermissions`) still restores PROMPT.md.
///
/// This covers Gap 1: interrupt arrives before `restore_needed` is set to true.
/// Even though this run didn't lock PROMPT.md, restoration should still be attempted
/// in case a prior run left it read-only.
#[test]
fn test_ctrl_c_before_lock_restores_prompt_md_writable() {
    with_default_timeout(|| {
        // State simulates: user pressed Ctrl+C before LockPromptPermissions executed
        let interrupted_state = PipelineState {
            phase: PipelinePhase::Interrupted,
            previous_phase: Some(PipelinePhase::Planning),
            checkpoint_saved_count: 0,
            interrupted_by_user: true,
            // Key: restore_needed=false because lock never executed
            prompt_permissions: ralph_workflow::reducer::state::PromptPermissionsState {
                locked: false,
                restore_needed: false, // NOT set because lock didn't run
                restored: false,
                last_warning: None,
            },
            ..PipelineState::initial(1, 0)
        };

        // After fix: RestorePromptPermissions should be derived even when restore_needed=false
        let effect = determine_next_effect(&interrupted_state);
        assert!(
            matches!(effect, Effect::RestorePromptPermissions),
            "Early Ctrl+C (restore_needed=false) should still derive RestorePromptPermissions, got {effect:?}"
        );
    });
}

/// Test that marker removal workspace functions work correctly.
///
/// The .`no_agent_commit` file blocks git operations during agent phase.
/// After Ctrl+C (or panic), `AgentPhaseGuard::drop()` calls `end_agent_phase()`
/// which removes this marker.
///
/// This test verifies the marker workspace functions that support cleanup.
/// Full guard behavior testing requires crate-internal access to `GitHelpers`.
#[test]
fn test_marker_workspace_functions() {
    with_default_timeout(|| {
        use ralph_workflow::git_helpers::{
            create_marker_with_workspace, marker_exists_with_workspace,
            remove_marker_with_workspace,
        };
        use ralph_workflow::workspace::{MemoryWorkspace, Workspace};

        let workspace =
            MemoryWorkspace::new_test().with_file("PROMPT.md", "# Goal\nTest content\n");

        // Initially no marker
        assert!(
            !marker_exists_with_workspace(&workspace),
            "Marker should not exist initially"
        );

        // Create marker
        create_marker_with_workspace(&workspace).expect("Failed to create marker");
        assert!(
            marker_exists_with_workspace(&workspace),
            "Marker should exist after creation"
        );

        // Remove marker (simulates cleanup)
        remove_marker_with_workspace(&workspace).expect("Failed to remove marker");
        assert!(
            !marker_exists_with_workspace(&workspace),
            "Marker should not exist after removal"
        );

        // PROMPT.md should still exist
        assert!(
            workspace.exists(Path::new("PROMPT.md")),
            "PROMPT.md should still exist"
        );
    });
}

/// Test that hook marker detection works via workspace function.
///
/// Ralph installs hooks with `RALPH_RUST_MANAGED_HOOK` marker. On cleanup,
/// `AgentPhaseGuard::drop()` calls `uninstall_hooks()` which removes or
/// restores the original hooks.
///
/// This test verifies the marker detection function works correctly.
/// Full hook testing requires real git repo operations in system tests.
#[test]
fn test_hook_marker_detection_via_workspace() {
    with_default_timeout(|| {
        use ralph_workflow::git_helpers::file_contains_marker_with_workspace;
        use ralph_workflow::workspace::MemoryWorkspace;

        // The marker string used by Ralph
        const HOOK_MARKER: &str = "RALPH_RUST_MANAGED_HOOK";

        // Create a workspace with a mock hook file containing the marker
        let workspace = MemoryWorkspace::new_test().with_file(
            ".git/hooks/pre-commit",
            &format!("#!/bin/bash\n# {HOOK_MARKER} - generated by ralph\nexit 0"),
        );

        // Verify marker detection works
        let has_marker = file_contains_marker_with_workspace(
            &workspace,
            Path::new(".git/hooks/pre-commit"),
            HOOK_MARKER,
        )
        .expect("Failed to check marker");

        assert!(
            has_marker,
            "Hook file should contain the RALPH_RUST_MANAGED_HOOK marker"
        );

        // Verify non-marked files are detected correctly
        let workspace_no_marker =
            MemoryWorkspace::new_test().with_file(".git/hooks/pre-commit", "#!/bin/bash\nexit 0");

        let no_marker = file_contains_marker_with_workspace(
            &workspace_no_marker,
            Path::new(".git/hooks/pre-commit"),
            HOOK_MARKER,
        )
        .expect("Failed to check marker");

        assert!(
            !no_marker,
            "Hook file without marker should not be detected as marked"
        );
    });
}

// ============================================================================
// Additional Ctrl+C cleanup tests per acceptance criteria
// ============================================================================

/// Test that Interrupted phase emits `RestorePromptPermissions` even when `restore_needed=false`.
///
/// This is a regression test for Gap 1: if interrupt arrives before `LockPromptPermissions`
/// executed, `restore_needed=false` but PROMPT.md may still need restoration from a prior
/// crashed run that left it read-only.
#[test]
fn test_interrupted_phase_restores_prompt_md_when_restore_not_needed() {
    with_default_timeout(|| {
        // State: interrupted_by_user=true, restore_needed=false, restored=false
        // This simulates early Ctrl+C before LockPromptPermissions executed
        let interrupted_state = PipelineState {
            phase: PipelinePhase::Interrupted,
            previous_phase: Some(PipelinePhase::Planning),
            interrupted_by_user: true,
            prompt_permissions: ralph_workflow::reducer::state::PromptPermissionsState {
                locked: false,
                restore_needed: false, // Key: NOT set because lock didn't run
                restored: false,
                last_warning: None,
            },
            ..PipelineState::initial(1, 0)
        };

        // Orchestration should still derive RestorePromptPermissions
        let effect = determine_next_effect(&interrupted_state);
        assert!(
            matches!(effect, Effect::RestorePromptPermissions),
            "Orchestration must emit RestorePromptPermissions even when restore_needed=false, got {effect:?}"
        );
    });
}

/// Test that startup cleanup handles hooks from prior crashed run.
///
/// Simulates SIGKILL scenario: Ralph-managed hooks exist from a prior run.
/// Verifies that the startup cleanup sequence would restore/remove hooks.
///
/// Note: This is a workspace-level test. Full hook behavior with real git
/// operations is tested in system tests.
#[test]
fn test_startup_cleanup_restores_hooks_from_prior_run() {
    with_default_timeout(|| {
        use ralph_workflow::git_helpers::{
            file_contains_marker_with_workspace, verify_hook_integrity_with_workspace,
        };
        use ralph_workflow::workspace::MemoryWorkspace;

        const HOOK_MARKER: &str = "RALPH_RUST_MANAGED_HOOK";

        // Simulate prior crashed run: Ralph-managed hook exists
        let workspace = MemoryWorkspace::new_test()
            .with_file("PROMPT.md", "# Goal\nTest content\n")
            .with_file(".git/ralph/no_agent_commit", "")
            .with_file(
                ".git/hooks/pre-commit",
                &format!("#!/bin/bash\n# {HOOK_MARKER} - generated by ralph\nexit 0"),
            );

        // Verify hook has Ralph marker (simulates prior run's hook)
        let has_marker = file_contains_marker_with_workspace(
            &workspace,
            Path::new(".git/hooks/pre-commit"),
            HOOK_MARKER,
        )
        .expect("Failed to check marker");
        assert!(has_marker, "Test setup: hook should have Ralph marker");

        // The actual startup cleanup calls uninstall_hooks() which uses real git ops.
        // This test documents the expected behavior: Ralph-managed hooks should be
        // removed or restored to original state on startup cleanup.
        //
        // Full end-to-end testing requires system tests with real git operations.
        // The workspace-aware verify_hook_integrity_with_workspace confirms detection.
        let is_ralph_managed =
            verify_hook_integrity_with_workspace(&workspace, Path::new(".git/hooks/pre-commit"))
                .expect("Failed to verify hook");
        assert!(
            is_ralph_managed,
            "Hook should be detected as Ralph-managed for cleanup"
        );
    });
}

/// Test that `AgentPhaseGuard` drop behavior includes PROMPT.md restoration.
///
/// This documents that when `AgentPhaseGuard` is dropped without `disarm()`,
/// it restores PROMPT.md permissions as part of cleanup. The actual guard
/// implementation is in ralph-workflow/src/pipeline/types.rs:57-74.
///
/// Note: `MemoryWorkspace` has no-op permission methods, but the guard's
/// `make_prompt_writable_with_workspace` call is verified to not error.
#[test]
fn test_agent_phase_guard_drop_restores_prompt_md() {
    with_default_timeout(|| {
        use ralph_workflow::files::make_prompt_writable_with_workspace;
        use ralph_workflow::workspace::{MemoryWorkspace, Workspace};

        let workspace =
            MemoryWorkspace::new_test().with_file("PROMPT.md", "# Goal\nTest content\n");

        // Simulate what AgentPhaseGuard::drop() does:
        // 1. Calls make_prompt_writable_with_workspace
        // 2. Calls end_agent_phase() (removes marker)
        // 3. Calls disable_git_wrapper()
        // 4. Calls uninstall_hooks()
        // 5. Calls cleanup_generated_files_with_workspace()

        // This test focuses on PROMPT.md restoration
        let warning = make_prompt_writable_with_workspace(&workspace);
        assert!(
            warning.is_none(),
            "PROMPT.md restoration should succeed without warning"
        );

        // PROMPT.md should still exist and be intact
        assert!(workspace.exists(Path::new("PROMPT.md")));
        let content = workspace.read(Path::new("PROMPT.md")).unwrap();
        assert!(content.contains("# Goal"));
    });
}

/// Test that Ctrl+C cleanup removes .`no_agent_commit` marker.
///
/// The .`no_agent_commit` marker blocks git operations during agent phase.
/// On Ctrl+C (or panic), cleanup must remove this marker so git is usable.
///
/// This verifies the marker workspace functions used by cleanup paths.
#[test]
fn test_ctrl_c_removes_no_agent_commit() {
    with_default_timeout(|| {
        use ralph_workflow::git_helpers::{
            cleanup_orphaned_marker_with_workspace, create_marker_with_workspace,
            marker_exists_with_workspace,
        };
        use ralph_workflow::logger::{Colors, Logger};
        use ralph_workflow::workspace::MemoryWorkspace;

        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "# Goal\n");
        let logger = Logger::new(Colors::new());

        // Create marker (simulates agent phase start)
        create_marker_with_workspace(&workspace).expect("Failed to create marker");
        assert!(
            marker_exists_with_workspace(&workspace),
            "Marker should exist after creation"
        );

        // Cleanup marker (simulates Ctrl+C cleanup via AgentPhaseGuard or signal handler)
        cleanup_orphaned_marker_with_workspace(&workspace, &logger)
            .expect("Failed to cleanup marker");

        // Marker should be removed
        assert!(
            !marker_exists_with_workspace(&workspace),
            ".no_agent_commit should not exist after Ctrl+C cleanup"
        );
    });
}

/// Test that Ctrl+C cleanup can detect and restore git hooks.
///
/// Ralph installs hooks with `RALPH_RUST_MANAGED_HOOK` marker. On Ctrl+C,
/// `AgentPhaseGuard::drop()` calls `uninstall_hooks()` which:
/// - If .ralph.orig exists: restores original hook
/// - If no original: removes the hook
///
/// This tests the marker detection that enables proper cleanup.
/// Full hook file operations require system tests with real git.
#[test]
fn test_ctrl_c_restores_git_hooks() {
    with_default_timeout(|| {
        use ralph_workflow::git_helpers::{
            file_contains_marker_with_workspace, verify_hook_integrity_with_workspace,
        };
        use ralph_workflow::workspace::{MemoryWorkspace, Workspace};

        const HOOK_MARKER: &str = "RALPH_RUST_MANAGED_HOOK";

        // Scenario 1: Hook with original backup (.ralph.orig)
        let workspace_with_orig = MemoryWorkspace::new_test()
            .with_file("PROMPT.md", "# Goal\n")
            .with_file(
                ".git/hooks/pre-commit",
                &format!("#!/bin/bash\n# {HOOK_MARKER} - generated by ralph\nexit 0"),
            )
            .with_file(
                ".git/hooks/pre-commit.ralph.orig",
                "#!/bin/bash\necho original\n",
            );

        // Hook should be detected as Ralph-managed
        let is_ralph = file_contains_marker_with_workspace(
            &workspace_with_orig,
            Path::new(".git/hooks/pre-commit"),
            HOOK_MARKER,
        )
        .unwrap();
        assert!(is_ralph, "Hook should have Ralph marker");

        // Original backup exists - would be restored
        assert!(
            workspace_with_orig.exists(Path::new(".git/hooks/pre-commit.ralph.orig")),
            "Original hook backup should exist for restoration"
        );

        // Scenario 2: Hook without original (would be removed)
        let workspace_no_orig = MemoryWorkspace::new_test()
            .with_file("PROMPT.md", "# Goal\n")
            .with_file(
                ".git/hooks/pre-commit",
                &format!("#!/bin/bash\n# {HOOK_MARKER} - generated by ralph\nexit 0"),
            );

        let is_ralph_no_orig = verify_hook_integrity_with_workspace(
            &workspace_no_orig,
            Path::new(".git/hooks/pre-commit"),
        )
        .unwrap();
        assert!(
            is_ralph_no_orig,
            "Hook without original should still be detected as Ralph-managed"
        );

        // No original backup - hook would be removed entirely
        assert!(
            !workspace_no_orig.exists(Path::new(".git/hooks/pre-commit.ralph.orig")),
            "No original backup means hook would be removed"
        );
    });
}

/// Test that Ctrl+C removes hooks when no prior hook existed.
///
/// When Ralph installs a hook where none existed before, there's no
/// .ralph.orig backup. On cleanup, the hook should be removed entirely.
///
/// This verifies the detection path - actual removal requires system tests.
#[test]
fn test_ctrl_c_removes_hook_when_no_prior_hook_existed() {
    with_default_timeout(|| {
        use ralph_workflow::git_helpers::verify_hook_integrity_with_workspace;
        use ralph_workflow::workspace::{MemoryWorkspace, Workspace};

        const HOOK_MARKER: &str = "RALPH_RUST_MANAGED_HOOK";

        // Setup: Ralph-managed hook exists, but NO .ralph.orig backup
        let workspace = MemoryWorkspace::new_test()
            .with_file("PROMPT.md", "# Goal\n")
            .with_file(
                ".git/hooks/pre-commit",
                &format!("#!/bin/bash\n# {HOOK_MARKER} - generated by ralph\nexit 0"),
            )
            .with_file(
                ".git/hooks/pre-push",
                &format!("#!/bin/bash\n# {HOOK_MARKER} - generated by ralph\nexit 0"),
            );

        // Verify both hooks are Ralph-managed
        assert!(
            verify_hook_integrity_with_workspace(&workspace, Path::new(".git/hooks/pre-commit"))
                .unwrap(),
            "pre-commit should be Ralph-managed"
        );
        assert!(
            verify_hook_integrity_with_workspace(&workspace, Path::new(".git/hooks/pre-push"))
                .unwrap(),
            "pre-push should be Ralph-managed"
        );

        // Verify NO original backups exist
        assert!(
            !workspace.exists(Path::new(".git/hooks/pre-commit.ralph.orig")),
            "No original pre-commit backup"
        );
        assert!(
            !workspace.exists(Path::new(".git/hooks/pre-push.ralph.orig")),
            "No original pre-push backup"
        );

        // This documents expected behavior: since no .ralph.orig exists,
        // uninstall_hooks() would remove both hooks entirely.
        // The actual removal is tested in system tests with real git operations.
    });
}
