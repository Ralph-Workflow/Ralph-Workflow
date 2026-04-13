//! Integration tests for unified `CleanupRequiredFiles` effect.
//!
//! These tests verify that the unified cleanup mechanism correctly:
//! - Cleans required files before first agent invocation on each iteration/pass
//! - Skips cleanup on XSD retry attempts (commit and development analysis phases)
//! - Actually deletes files from the workspace
//! - Maintains correct checkpoint/resume behavior
//!
//! # Acceptance Criteria
//!
//! See original request for full acceptance criteria. Key tests:
//! - Cleanup fires before first agent invocation (all phases)
//! - XSD retry does not re-clean (commit and development analysis)
//! - Files are actually deleted from workspace
//! - Resume from checkpoint respects cleanup state

use std::path::Path;

use ralph_workflow::agents::{AgentDrain, AgentRole};
use ralph_workflow::reducer::effect::{Effect, EffectHandler};
use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase, ReviewEvent};
use ralph_workflow::reducer::handler::MainEffectHandler;
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::reduce;
use ralph_workflow::reducer::state::{
    CommitState, PipelineState, PromptPermissionsState, RebaseState,
};
use ralph_workflow::workspace::{MemoryWorkspace, Workspace};

use crate::common::IntegrationFixture;
use crate::test_timeout::with_default_timeout;

/// Helper to create a base test state with prompt permissions locked.
fn create_test_state() -> PipelineState {
    PipelineState {
        // Set locked=true so tests don't need to deal with LockPromptPermissions effect
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(5, 2)
    }
}

/// Helper to create a state ready for planning cleanup.
fn create_planning_state_ready_for_cleanup(iteration: u32) -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Planning,
        iteration,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        planning_prompt_prepared_iteration: Some(iteration),
        rebase: RebaseState::Skipped,
        checkpoint_saved_count: 1, // Skip initial checkpoint
        agent_chain: create_test_state()
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Planning),
        ..create_test_state()
    }
}

/// Helper to create a state ready for development cleanup.
fn create_development_state_ready_for_cleanup(iteration: u32) -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Development,
        iteration,
        total_iterations: 3,
        gitignore_entries_ensured: true,
        context_cleaned: true,
        development_context_prepared_iteration: Some(iteration),
        development_prompt_prepared_iteration: Some(iteration),
        agent_chain: create_test_state()
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(ralph_workflow::agents::AgentDrain::Development),
        ..create_test_state()
    }
}

/// Helper to create a state ready for review cleanup.
fn create_review_state_ready_for_cleanup(pass: u32) -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: pass,
        total_reviewer_passes: 2,
        review_context_prepared_pass: Some(pass),
        review_prompt_prepared_pass: Some(pass),
        agent_chain: create_test_state()
            .agent_chain
            .with_agents(vec!["codex".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(ralph_workflow::agents::AgentDrain::Review),
        ..create_test_state()
    }
}

/// Helper to create a state ready for fix cleanup.
fn create_fix_state_ready_for_cleanup(pass: u32) -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: pass,
        total_reviewer_passes: 2,
        review_issues_found: true,
        fix_prompt_prepared_pass: Some(pass),
        agent_chain: create_test_state()
            .agent_chain
            .with_agents(vec!["codex".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(ralph_workflow::agents::AgentDrain::Fix),
        ..create_test_state()
    }
}

/// Helper to create a state ready for commit cleanup.
fn create_commit_state_ready_for_cleanup() -> PipelineState {
    PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_prompt_prepared: true,
        commit_diff_prepared: true,
        commit_diff_content_id_sha256: Some("abc123".to_string()),
        agent_chain: create_test_state().agent_chain.with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    }
}

// ============================================================================
// Cleanup Before First Invocation Tests
// ============================================================================

/// Test that `CleanupRequiredFiles` is emitted before `InvokePlanningAgent` on iteration 0.
#[test]
fn test_planning_cleanup_before_invoke_on_iteration_0() {
    with_default_timeout(|| {
        let state = create_planning_state_ready_for_cleanup(0);

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                &effect,
                Effect::CleanupRequiredFiles { files }
                if files.contains(&".agent/tmp/plan.xml".to_string())
            ),
            "Expected CleanupRequiredFiles for plan.xml, got {effect:?}"
        );
    });
}

/// Test that `CleanupRequiredFiles` is emitted before `InvokePlanningAgent` on iteration 1.
#[test]
fn test_planning_cleanup_before_invoke_on_iteration_1() {
    with_default_timeout(|| {
        let state = create_planning_state_ready_for_cleanup(1);

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                &effect,
                Effect::CleanupRequiredFiles { files }
                if files.contains(&".agent/tmp/plan.xml".to_string())
            ),
            "Expected CleanupRequiredFiles for plan.xml on iteration 1, got {effect:?}"
        );
    });
}

/// Test that `CleanupRequiredFiles` is emitted before `InvokeDevelopmentAgent` on each iteration.
#[test]
fn test_development_cleanup_before_invoke() {
    with_default_timeout(|| {
        let state = create_development_state_ready_for_cleanup(0);

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                &effect,
                Effect::CleanupRequiredFiles { files }
                if files.contains(&".agent/tmp/development_result.xml".to_string())
            ),
            "Expected CleanupRequiredFiles for development_result.xml, got {effect:?}"
        );
    });
}

/// Test that `CleanupRequiredFiles` is emitted before `InvokeReviewAgent` on each pass.
#[test]
fn test_review_cleanup_before_invoke() {
    with_default_timeout(|| {
        let state = create_review_state_ready_for_cleanup(0);

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                &effect,
                Effect::CleanupRequiredFiles { files }
                if files.contains(&".agent/tmp/issues.xml".to_string())
            ),
            "Expected CleanupRequiredFiles for issues.xml, got {effect:?}"
        );
    });
}

/// Test that `CleanupRequiredFiles` is emitted before `InvokeFixAgent` on each pass.
#[test]
fn test_fix_cleanup_before_invoke() {
    with_default_timeout(|| {
        let state = create_fix_state_ready_for_cleanup(0);

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                &effect,
                Effect::CleanupRequiredFiles { files }
                if files.contains(&".agent/tmp/fix_result.xml".to_string())
            ),
            "Expected CleanupRequiredFiles for fix_result.xml, got {effect:?}"
        );
    });
}

/// Test that production cleanup emits the fix cleanup event from the explicit fix drain.
#[test]
fn test_fix_cleanup_handler_uses_fix_drain_without_review_issue_flag() {
    with_default_timeout(|| {
        let mut fixture = IntegrationFixture::new();
        let files: Box<[String]> = vec![".agent/tmp/fix_result.xml".to_string()].into();
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 1,
            review_issues_found: false,
            agent_chain: create_test_state()
                .agent_chain
                .with_agents(vec!["codex".to_string()], vec![vec![]], AgentRole::Reviewer)
                .with_drain(AgentDrain::Fix),
            ..create_test_state()
        };
        let mut handler = MainEffectHandler::new(state);
        let mut ctx = fixture.ctx(None);

        let result = handler
            .execute(Effect::CleanupRequiredFiles { files }, &mut ctx)
            .expect("cleanup effect should execute successfully");

        assert!(matches!(
            result.event,
            PipelineEvent::Review(ReviewEvent::FixResultXmlCleaned { pass: 1 })
        ));
    });
}

/// Test that mock cleanup emits the fix cleanup event from the explicit fix drain.
#[test]
fn test_fix_cleanup_mock_uses_fix_drain_without_review_issue_flag() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 1,
            review_issues_found: false,
            agent_chain: create_test_state()
                .agent_chain
                .with_agents(vec!["codex".to_string()], vec![vec![]], AgentRole::Reviewer)
                .with_drain(AgentDrain::Fix),
            ..create_test_state()
        };
        let mut handler = MockEffectHandler::new(state);

        let result = handler.execute_mock(&Effect::CleanupRequiredFiles {
            files: vec![".agent/tmp/fix_result.xml".to_string()].into(),
        });

        assert!(matches!(
            result.event,
            PipelineEvent::Review(ReviewEvent::FixResultXmlCleaned { pass: 1 })
        ));
    });
}

/// Test that `CleanupRequiredFiles` is emitted before `InvokeCommitAgent` on attempt 1.
#[test]
fn test_commit_cleanup_before_invoke_attempt_1() {
    with_default_timeout(|| {
        let state = create_commit_state_ready_for_cleanup();

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                &effect,
                Effect::CleanupRequiredFiles { files }
                if files.contains(&".agent/tmp/commit_message.xml".to_string())
            ),
            "Expected CleanupRequiredFiles for commit_message.xml on attempt 1, got {effect:?}"
        );
    });
}

// ============================================================================
// XSD Retry Exemption Tests
// ============================================================================

/// Test that `CleanupRequiredFiles` is NOT emitted on commit XSD retry (attempt > 1).
#[test]
fn test_commit_no_cleanup_on_xsd_retry() {
    with_default_timeout(|| {
        let mut state = create_commit_state_ready_for_cleanup();
        // Simulate XSD retry: attempt 2
        state.commit = CommitState::Generating {
            attempt: 2,
            max_attempts: 3,
        };

        let effect = determine_next_effect(&state);

        // Should NOT emit `CleanupRequiredFiles` on retry
        if let Effect::CleanupRequiredFiles { files } = &effect {
            panic!(
                "Expected NO `CleanupRequiredFiles` on XSD retry (attempt 2), got cleanup for {files:?}"
            );
        }
        // Should emit `InvokeCommitAgent` directly
        assert!(
            matches!(effect, Effect::InvokeCommitAgent),
            "Expected `InvokeCommitAgent` on XSD retry, got {effect:?}"
        );
    });
}

// ============================================================================
// File Deletion Tests
// ============================================================================

/// Test that stale XML is removed from workspace after cleanup.
#[test]
fn test_stale_planning_xml_removed_from_workspace() {
    with_default_timeout(|| {
        let workspace =
            MemoryWorkspace::new_test().with_file(".agent/tmp/plan.xml", "<stale><plan/></stale>");

        // Verify file exists before cleanup
        assert!(
            workspace.exists(Path::new(".agent/tmp/plan.xml")),
            "Stale XML should exist before cleanup"
        );

        // Simulate cleanup effect
        let result = workspace.remove_if_exists(Path::new(".agent/tmp/plan.xml"));

        assert!(result.is_ok(), "Cleanup should succeed");
        assert!(
            !workspace.exists(Path::new(".agent/tmp/plan.xml")),
            "Stale XML should be removed after cleanup"
        );
    });
}

/// Test that stale commit XML is removed from workspace on attempt 1.
#[test]
fn test_stale_commit_xml_removed_from_workspace() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test()
            .with_file(".agent/tmp/commit_message.xml", "<stale><message/></stale>");

        // Verify file exists before cleanup
        assert!(
            workspace.exists(Path::new(".agent/tmp/commit_message.xml")),
            "Stale commit XML should exist before cleanup"
        );

        // Simulate cleanup effect
        let result = workspace.remove_if_exists(Path::new(".agent/tmp/commit_message.xml"));

        assert!(result.is_ok(), "Cleanup should succeed");
        assert!(
            !workspace.exists(Path::new(".agent/tmp/commit_message.xml")),
            "Stale commit XML should be removed after cleanup"
        );
    });
}

/// Test that `CleanupRequiredFiles` with non-existent file succeeds without error.
#[test]
fn test_cleanup_nonexistent_file_succeeds() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        // Verify file doesn't exist
        assert!(
            !workspace.exists(Path::new(".agent/tmp/plan.xml")),
            "File should not exist"
        );

        // Cleanup of non-existent file should succeed
        let result = workspace.remove_if_exists(Path::new(".agent/tmp/plan.xml"));

        assert!(
            result.is_ok(),
            "Cleanup of non-existent file should succeed"
        );
    });
}

// ============================================================================
// Checkpoint/Resume Compatibility Tests
// ============================================================================

/// Test that resume from checkpoint after cleanup does not re-emit cleanup.
#[test]
fn test_resume_after_cleanup_does_not_reclean() {
    with_default_timeout(|| {
        // Create state as if checkpoint was saved AFTER cleanup
        let mut state = create_planning_state_ready_for_cleanup(0);
        state.planning_required_files_cleaned_iteration = Some(0);

        let effect = determine_next_effect(&state);

        // Should NOT emit `CleanupRequiredFiles` again
        if let Effect::CleanupRequiredFiles { files } = &effect {
            panic!(
                "Expected NO `CleanupRequiredFiles` after cleanup already done, got cleanup for {files:?}"
            );
        }

        // Should emit `InvokePlanningAgent` directly
        assert!(
            matches!(effect, Effect::InvokePlanningAgent { .. }),
            "Expected `InvokePlanningAgent` after cleanup done, got {effect:?}"
        );
    });
}

/// Test that resume from checkpoint before cleanup emits cleanup before agent.
#[test]
fn test_resume_before_cleanup_emits_cleanup() {
    with_default_timeout(|| {
        // Create state as if checkpoint was saved BEFORE cleanup
        let state = create_planning_state_ready_for_cleanup(0);
        // planning_required_files_cleaned_iteration is None, indicating cleanup not done

        let effect = determine_next_effect(&state);

        // Should emit `CleanupRequiredFiles`
        assert!(
            matches!(
                &effect,
                Effect::CleanupRequiredFiles { files }
                if files.contains(&".agent/tmp/plan.xml".to_string())
            ),
            "Expected `CleanupRequiredFiles` for plan.xml on resume before cleanup, got {effect:?}"
        );
    });
}

/// Test that new iteration resets cleanup tracking and emits cleanup again.
#[test]
fn test_new_iteration_resets_cleanup_tracking() {
    with_default_timeout(|| {
        // Create state where iteration 0 cleanup was done
        let mut state = create_planning_state_ready_for_cleanup(0);
        state.planning_required_files_cleaned_iteration = Some(0);
        state.planning_agent_invoked_iteration = Some(0);

        // Advance to iteration 1 with prompt prepared
        state.iteration = 1;
        state.planning_prompt_prepared_iteration = Some(1);
        // Note: planning_required_files_cleaned_iteration is still Some(0), not Some(1)

        let effect = determine_next_effect(&state);

        // Should emit `CleanupRequiredFiles` for new iteration
        assert!(
            matches!(
                &effect,
                Effect::CleanupRequiredFiles { files }
                if files.contains(&".agent/tmp/plan.xml".to_string())
            ),
            "Expected `CleanupRequiredFiles` for iteration 1, got {effect:?}"
        );
    });
}

/// Test that reducer correctly updates cleanup tracking field.
#[test]
fn test_reducer_updates_cleanup_tracking() {
    with_default_timeout(|| {
        let state = create_planning_state_ready_for_cleanup(0);

        // Verify initial state
        assert_eq!(
            state.planning_required_files_cleaned_iteration, None,
            "Cleanup tracking should be None initially"
        );

        // Apply cleanup event
        let event = PipelineEvent::planning_xml_cleaned(0);
        let new_state = reduce(state, event);

        // Verify tracking field updated
        assert_eq!(
            new_state.planning_required_files_cleaned_iteration,
            Some(0),
            "Cleanup tracking should be Some(0) after event"
        );
    });
}
