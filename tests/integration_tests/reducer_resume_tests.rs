//! Resume integration tests.
//!
//! Tests verify that resume functionality works correctly with reducer state machine.
//! Tests cover resume at all pipeline phases (planning, development, review, commit).
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::checkpoint::state::{AgentConfigSnapshot, CliArgsSnapshot, RebaseState};
use ralph_workflow::checkpoint::{
    CheckpointBuilder, PipelineCheckpoint, PipelinePhase as CheckpointPhase,
};
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::state::PipelineState;

fn create_minimal_agent_config(name: &str) -> AgentConfigSnapshot {
    AgentConfigSnapshot {
        name: name.to_string(),
        cmd: name.to_string(),
        output_flag: "-o".to_string(),
        yolo_flag: None,
        can_commit: true,
        model_override: None,
        provider_override: None,
        context_level: 1,
    }
}

const fn create_minimal_cli_args() -> CliArgsSnapshot {
    CliArgsSnapshot {
        developer_iters: 1,
        reviewer_reviews: 1,
        review_depth: None,
        isolation_mode: true,
        verbosity: 2,
        show_streaming_metrics: false,
        reviewer_json_parser: None,
    }
}

fn create_test_checkpoint(
    phase: CheckpointPhase,
    iteration: u32,
    total: u32,
    reviewer_pass: u32,
) -> PipelineCheckpoint {
    CheckpointBuilder::new()
        .phase(phase, iteration, total)
        .reviewer_pass(reviewer_pass, 2)
        .agents("claude", "claude")
        .cli_args(create_minimal_cli_args())
        .developer_config(create_minimal_agent_config("claude"))
        .reviewer_config(create_minimal_agent_config("claude"))
        .rebase_state(RebaseState::default())
        .git_identity(None, None)
        .build()
        .unwrap()
}

#[test]
fn test_pipeline_state_from_checkpoint_at_planning() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Planning, 0, 5, 0);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.phase, PipelinePhase::Planning);
        assert_eq!(state.iteration, 0);
    });
}

#[test]
fn test_pipeline_state_from_checkpoint_at_development() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 3, 5, 0);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.phase, PipelinePhase::Development);
        assert_eq!(state.iteration, 3);
    });
}

#[test]
fn test_pipeline_state_from_checkpoint_at_review() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Review, 5, 5, 1);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.phase, PipelinePhase::Review);
        assert_eq!(state.reviewer_pass, 1);
        assert_eq!(state.iteration, 5);
    });
}

#[test]
fn test_pipeline_state_from_checkpoint_at_commit() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::CommitMessage, 5, 5, 2);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.phase, PipelinePhase::CommitMessage);
        assert_eq!(state.reviewer_pass, 2);
        assert_eq!(state.iteration, 5);
    });
}

#[test]
fn test_pipeline_state_from_complete_checkpoint() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Complete, 5, 5, 2);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.phase, PipelinePhase::Complete);
        assert_eq!(state.reviewer_pass, 2);
        assert_eq!(state.iteration, 5);
    });
}

#[test]
fn test_resume_continues_from_correct_iteration() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 2, 5, 0);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.iteration, 2);
        assert_eq!(state.total_iterations, 5);
    });
}

#[test]
fn test_resume_continues_from_correct_reviewer_pass() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Review, 5, 5, 1);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.reviewer_pass, 1);
        assert_eq!(state.total_reviewer_passes, 2);
    });
}

#[test]
fn test_agent_chain_initialized_across_resume() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::effect::Effect;
        use ralph_workflow::reducer::orchestration::determine_next_effect;

        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 2, 5, 0);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        // Behavioral check: agent chain should be in initial state after resume,
        // requiring re-initialization via orchestration.
        assert!(
            state.agent_chain.current_agent().is_none(),
            "Agent chain should be empty after resume (requires re-initialization)"
        );

        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    role: AgentRole::Developer,
                    ..
                }
            ),
            "Resumed state should require agent chain initialization, got {effect:?}"
        );
    });
}

// ============================================================================
// Metrics Preservation Tests
// ============================================================================

#[test]
fn test_metrics_preserved_in_checkpoint_serialization() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent};
        use ralph_workflow::reducer::state_reduction::reduce;

        // Build state with non-zero metrics
        let mut state = with_locked_prompt_permissions(PipelineState::initial(5, 2));
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );

        // Serialize
        let json = serde_json::to_string(&state).unwrap();

        // Deserialize
        let restored: PipelineState = serde_json::from_str(&json).unwrap();

        // Verify metrics preserved
        assert_eq!(restored.metrics.dev_iterations_started, 1);
        assert_eq!(restored.metrics.dev_attempts_total, 1);
        assert_eq!(restored.metrics.analysis_attempts_total, 1);
        assert_eq!(restored.metrics.analysis_attempts_in_current_iteration, 1);
    });
}

#[test]
fn test_metrics_default_on_old_checkpoint_without_metrics() {
    with_default_timeout(|| {
        // Create a state, serialize it, remove the metrics field, and deserialize
        let state = with_locked_prompt_permissions(PipelineState::initial(5, 2));
        let mut json: serde_json::Value = serde_json::to_value(&state).unwrap();

        // Remove the metrics field to simulate an old checkpoint
        json.as_object_mut().unwrap().remove("metrics");

        // Should deserialize with default metrics
        let restored: PipelineState = serde_json::from_value(json).unwrap();

        // Metrics should be defaulted (all zeros)
        assert_eq!(restored.metrics.dev_iterations_started, 0);
        assert_eq!(restored.metrics.dev_attempts_total, 0);
        assert_eq!(restored.metrics.max_dev_iterations, 0);
        assert_eq!(restored.metrics.max_review_passes, 0);
    });
}

#[test]
fn test_metrics_config_fields_preserved() {
    with_default_timeout(|| {
        let state = with_locked_prompt_permissions(PipelineState::initial(10, 3));

        assert_eq!(state.metrics.max_dev_iterations, 10);
        assert_eq!(state.metrics.max_review_passes, 3);

        // Serialize and restore
        let json = serde_json::to_string(&state).unwrap();
        let restored: PipelineState = serde_json::from_str(&json).unwrap();

        assert_eq!(restored.metrics.max_dev_iterations, 10);
        assert_eq!(restored.metrics.max_review_passes, 3);
    });
}

// ============================================================================
// Step 16: Checkpoint resume test for metrics consistency
// ============================================================================

/// Test that metrics survive checkpoint serialization and resume with correct values.
///
/// CRITICAL: All metrics must be preserved across checkpoint/resume to ensure
/// the final summary is accurate even after interruption.
#[test]
fn test_metrics_survive_checkpoint_resume() {
    with_default_timeout(|| {
        use ralph_workflow::checkpoint::RunContext;
        use ralph_workflow::reducer::state::PipelineState;

        // Given: checkpoint payload with explicit completed-run counters.
        let mut checkpoint = create_test_checkpoint(CheckpointPhase::Review, 4, 5, 2);
        checkpoint.actual_developer_runs = 3;
        checkpoint.actual_reviewer_runs = 2;

        // And a run context that preserves those values on restore.
        let run_context = RunContext::from_checkpoint(&checkpoint);
        assert_eq!(run_context.actual_developer_runs, 3);
        assert_eq!(run_context.actual_reviewer_runs, 2);

        // When: resume through the checkpoint conversion path.
        let restored = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        // Then: reducer metrics come from checkpoint completed-run counters.
        assert_eq!(restored.metrics.dev_iterations_completed, 3);
        assert_eq!(restored.metrics.review_passes_completed, 2);
        assert_eq!(restored.metrics.max_dev_iterations, 5);
        assert_eq!(restored.metrics.max_review_passes, 2);
    });
}

/// Integration test: Resume at final dev iteration should complete full pipeline
///
/// This test verifies the bug fix: when resuming from a checkpoint at the final
/// iteration boundary (iteration=N, total=N), the pipeline should:
/// 1. Re-run the current iteration (because progress flags are None)
/// 2. Continue through Review phase
/// 3. Continue through Commit and `FinalValidation`
/// 4. Reach Complete phase
#[test]
fn test_resume_at_final_iteration_completes_full_pipeline() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::effect::Effect;
        use ralph_workflow::reducer::orchestration::determine_next_effect;

        // Given: Checkpoint at final iteration with all progress flags reset
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 1, 1, 0);
        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        // Verify initial state
        assert_eq!(state.iteration, 1);
        assert_eq!(state.total_iterations, 1);
        assert_eq!(state.phase, PipelinePhase::Development);
        assert!(state.development_agent_invoked_iteration.is_none());

        // When: Determine next effect (simulating orchestration)
        let effect = determine_next_effect(&state);

        // Then: Should start development work, NOT skip to phase transition
        assert!(
            !matches!(effect, Effect::SaveCheckpoint { .. }),
            "Bug: Orchestration skipped to SaveCheckpoint instead of running development work"
        );

        // Verify it's a development effect (could be InitializeAgentChain or PrepareDevelopmentContext)
        let is_development_effect = matches!(
            effect,
            Effect::InitializeAgentChain {
                role: AgentRole::Developer,
                ..
            } | Effect::PrepareDevelopmentContext { .. }
        );
        assert!(
            is_development_effect,
            "Expected development effect, got {effect:?}"
        );
    });
}

/// Regression test: Mid-pipeline resume should still work correctly
///
/// Verifies that the boundary check fix doesn't break normal mid-pipeline resumes
/// where iteration < `total_iterations`.
#[test]
fn test_resume_mid_pipeline_continues_normally() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::effect::Effect;
        use ralph_workflow::reducer::orchestration::determine_next_effect;

        // Given: Checkpoint at iteration 2 of 5
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 2, 5, 0);
        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        // Verify initial state
        assert_eq!(state.iteration, 2);
        assert_eq!(state.total_iterations, 5);

        // When: Determine next effect
        let effect = determine_next_effect(&state);

        // Then: Should derive development work (2 < 5 is true)
        let is_development_effect = matches!(
            effect,
            Effect::InitializeAgentChain {
                role: AgentRole::Developer,
                ..
            } | Effect::PrepareDevelopmentContext { .. }
        );
        assert!(
            is_development_effect,
            "Mid-pipeline resume should still derive development effects, got {effect:?}"
        );
    });
}

// ============================================================================
// Resume Boundary Condition Tests (Bug Fix Verification)
// ============================================================================

/// Verify that resume at final iteration boundary executes development work.
///
/// Bug: Previously, resuming at iteration == `total_iterations` would skip to
/// `SaveCheckpoint` instead of running the iteration. The fix adds a boundary
/// check: `iteration_needs_work` when (iteration < total) OR (iteration == total && total > 0).
#[test]
fn test_resume_at_final_iteration_boundary_runs_development() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::effect::Effect;
        use ralph_workflow::reducer::orchestration::determine_next_effect;

        // Given: Checkpoint at iteration=1, total_iterations=1 (final boundary)
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 1, 1, 0);
        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.iteration, 1);
        assert_eq!(state.total_iterations, 1);
        assert_eq!(state.phase, PipelinePhase::Development);

        // When: Determine next effect
        let effect = determine_next_effect(&state);

        // Then: Should derive development work, NOT SaveCheckpoint
        assert!(
            !matches!(effect, Effect::SaveCheckpoint { .. }),
            "Bug: At iteration boundary, should run development work, not skip to SaveCheckpoint. Got: {effect:?}"
        );

        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    role: AgentRole::Developer,
                    ..
                } | Effect::PrepareDevelopmentContext { .. }
            ),
            "Expected development effect at iteration=1, total=1. Got: {effect:?}"
        );
    });
}

/// Verify that resume at final review pass boundary executes review work.
///
/// Same bug as development: previously would skip to `SaveCheckpoint`.
/// The fix adds: `review_pass_needs_work` when (pass < total) OR (pass == total && total > 0).
#[test]
fn test_resume_at_final_review_pass_boundary_runs_review() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::effect::Effect;
        use ralph_workflow::reducer::orchestration::determine_next_effect;

        // Given: Checkpoint at reviewer_pass=2, total_reviewer_passes=2 (final boundary)
        // create_test_checkpoint sets total_reviewer_passes=2, and the conversion preserves it.
        let checkpoint = create_test_checkpoint(CheckpointPhase::Review, 3, 3, 2);
        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.reviewer_pass, 2);
        assert_eq!(state.total_reviewer_passes, 2);
        assert_eq!(state.phase, PipelinePhase::Review);

        // When: Determine next effect
        let effect = determine_next_effect(&state);

        // Then: Should derive review work, NOT SaveCheckpoint
        assert!(
            !matches!(effect, Effect::SaveCheckpoint { .. }),
            "Bug: At review pass boundary, should run review work, not skip to SaveCheckpoint. Got: {effect:?}"
        );

        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    role: AgentRole::Reviewer,
                    ..
                } | Effect::PrepareReviewContext { .. }
            ),
            "Expected review effect at reviewer_pass=2, total=2. Got: {effect:?}"
        );
    });
}

/// Verify that zero-indexed iteration (iteration=0, total=1) works correctly.
///
/// This is a boundary case that should work regardless of the fix, but good to verify.
#[test]
fn test_resume_zero_indexed_iteration_boundary() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::effect::Effect;
        use ralph_workflow::reducer::orchestration::determine_next_effect;

        // Given: Checkpoint at iteration=0, total_iterations=1
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 0, 1, 0);
        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        assert_eq!(state.iteration, 0);
        assert_eq!(state.total_iterations, 1);

        // When: Determine next effect
        let effect = determine_next_effect(&state);

        // Then: Should derive development work (0 < 1 is true, so works without fix too)
        let is_dev_effect = matches!(
            effect,
            Effect::InitializeAgentChain {
                role: AgentRole::Developer,
                ..
            } | Effect::PrepareDevelopmentContext { .. }
        );
        assert!(
            is_dev_effect,
            "Expected development work at iteration=0, total=1. Got: {effect:?}"
        );
    });
}
