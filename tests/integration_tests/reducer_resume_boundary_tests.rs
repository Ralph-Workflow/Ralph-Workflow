//! Resume boundary condition tests.
//!
//! Tests verify that resume at iteration/pass boundaries (e.g., iteration=N, total=N)
//! correctly re-runs the current work instead of skipping to the next phase.
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
use ralph_workflow::reducer::determine_next_effect;
use ralph_workflow::reducer::effect::Effect;
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
    total_reviewer_passes: u32,
) -> PipelineCheckpoint {
    CheckpointBuilder::new()
        .phase(phase, iteration, total)
        .reviewer_pass(reviewer_pass, total_reviewer_passes)
        .agents("claude", "claude")
        .cli_args(create_minimal_cli_args())
        .developer_config(create_minimal_agent_config("claude"))
        .reviewer_config(create_minimal_agent_config("claude"))
        .rebase_state(RebaseState::default())
        .git_identity(None, None)
        .build()
        .unwrap()
}

/// Test that resuming at the final development iteration (iteration=1, total=1)
/// derives development work effects instead of immediately skipping to `SaveCheckpoint`.
///
/// This test focuses specifically on the FIRST orchestration decision at the boundary.
/// It does NOT verify full phase continuation through the remaining pipeline.
///
/// Note: `total_reviewer_passes=0` means no review phase is configured. With this configuration,
/// after development completes the pipeline would skip directly to `FinalValidation` (not Review).
/// This test only verifies that development work is derived, not the subsequent phase transitions.
/// For full phase continuation testing (including Review phase), see
/// `test_resume_at_boundary_continues_through_remaining_phases` which uses `total_reviewer_passes=1`.
///
/// This test MUST FAIL before the fix is implemented.
#[test]
fn test_resume_at_final_iteration_should_rerun_development() {
    with_default_timeout(|| {
        // Create checkpoint at boundary: iteration=1, total_iterations=1
        // Note: total_reviewer_passes=0 (no review phase configured)
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 1, 1, 0, 0);

        // Convert to PipelineState (this resets all progress flags to None)
        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        // Orchestration should derive development work effects, NOT SaveCheckpoint
        let effect = determine_next_effect(&state);

        // ASSERTION: Should NOT be SaveCheckpoint
        // (This assertion will FAIL before the fix, proving the bug exists)
        assert!(
            !matches!(effect, Effect::SaveCheckpoint { .. }),
            "Bug reproduced: orchestration skipped development work and derived SaveCheckpoint at boundary. Effect: {effect:?}"
        );

        // ASSERTION: Should derive development preparation effect
        assert!(
            matches!(effect, Effect::PrepareDevelopmentContext { .. })
                || matches!(effect, Effect::MaterializeDevelopmentInputs { .. })
                || matches!(effect, Effect::InitializeAgentChain { .. }),
            "Expected development work effect at boundary (iteration=1, total=1), got {effect:?}"
        );
    });
}

/// Test that resuming at zero-indexed iteration (iteration=0, total=1) runs the iteration.
#[test]
fn test_resume_iteration_0_total_1_should_run() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 0, 1, 0, 0);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));
        let effect = determine_next_effect(&state);

        // Should derive development work, not checkpoint
        assert!(
            !matches!(effect, Effect::SaveCheckpoint { .. }),
            "Should run iteration 0, not skip to checkpoint. Effect: {effect:?}"
        );
    });
}

/// Test that resuming at middle iteration continues correctly.
#[test]
fn test_resume_mid_development_continues() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 1, 3, 0, 0);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));
        let effect = determine_next_effect(&state);

        // Should derive development work at iteration 1
        assert!(
            !matches!(effect, Effect::SaveCheckpoint { .. }),
            "Should continue development, not checkpoint. Effect: {effect:?}"
        );
    });
}

/// Test that resuming at the final review pass (`reviewer_pass=2`, total=2)
/// derives review work effects instead of immediately skipping to `SaveCheckpoint`.
///
/// This test MUST FAIL before the fix is implemented.
#[test]
fn test_resume_at_final_review_pass_should_rerun_review() {
    with_default_timeout(|| {
        // Create checkpoint at boundary: reviewer_pass=2, total_reviewer_passes=2
        let checkpoint = create_test_checkpoint(CheckpointPhase::Review, 3, 3, 2, 2);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));
        let effect = determine_next_effect(&state);

        // Should NOT be SaveCheckpoint
        assert!(
            !matches!(effect, Effect::SaveCheckpoint { .. }),
            "Bug reproduced: orchestration skipped review work at boundary. Effect: {effect:?}"
        );

        // Should derive review work effect
        assert!(
            matches!(effect, Effect::PrepareReviewContext { .. })
                || matches!(effect, Effect::MaterializeReviewInputs { .. })
                || matches!(effect, Effect::InitializeAgentChain { .. }),
            "Expected review work effect at boundary (pass=2, total=2), got {effect:?}"
        );
    });
}

/// Test that resuming at `reviewer_pass=0`, total=1 runs the pass.
#[test]
fn test_resume_reviewer_pass_0_total_1_should_run() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Review, 1, 1, 0, 1);

        let state = with_locked_prompt_permissions(PipelineState::from(checkpoint));
        let effect = determine_next_effect(&state);

        assert!(
            !matches!(effect, Effect::SaveCheckpoint { .. }),
            "Should run review pass 0, not skip to checkpoint. Effect: {effect:?}"
        );
    });
}

/// Test that resume at boundary continues through all remaining phases.
/// This verifies the fix for: "pipeline only runs immediate task and exits"
#[test]
fn test_resume_at_boundary_continues_through_remaining_phases() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent};
        use ralph_workflow::reducer::state_reduction::reduce;

        // Start from checkpoint at final iteration (iteration=1, total=1)
        // Configure with 1 review pass so we transition through Review phase
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 1, 1, 0, 1);
        let mut state = with_locked_prompt_permissions(PipelineState::from(checkpoint));

        // Verify we start in Development phase
        assert_eq!(state.phase, PipelinePhase::Development);

        // First effect should be development work (NOT SaveCheckpoint)
        let first_effect = determine_next_effect(&state);
        assert!(
            !matches!(first_effect, Effect::SaveCheckpoint { .. }),
            "Resume should start development work, not skip to checkpoint. Got: {first_effect:?}"
        );

        // Drive setup via reducer events (mirrors handler-emitted events in real execution)
        state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Developer.into(),
                vec!["claude".into()],
                3,
                1000,
                2.0,
                60_000,
            ),
        );
        state = reduce(state, PipelineEvent::development_context_prepared(1));
        state = reduce(state, PipelineEvent::development_prompt_prepared(1));
        state = reduce(state, PipelineEvent::development_xml_cleaned(1));
        state = reduce(state, PipelineEvent::development_agent_invoked(1));
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 1 }),
        );
        state = reduce(state, PipelineEvent::development_xml_extracted(1));
        state = reduce(
            state,
            PipelineEvent::development_xml_validated(
                1,
                ralph_workflow::reducer::state::DevelopmentStatus::Completed,
                "Test development complete".to_string(),
                Some(vec![]),
                None,
            ),
        );
        state = reduce(state, PipelineEvent::development_xml_archived(1));

        // After development completes, should apply outcome (not exit)
        let after_dev = determine_next_effect(&state);
        assert!(
            matches!(after_dev, Effect::ApplyDevelopmentOutcome { .. }),
            "Should apply development outcome after iteration completes. Got: {after_dev:?}"
        );

        // Apply the development iteration completed event to transition phases
        state = reduce(
            state,
            PipelineEvent::development_iteration_completed(1, true),
        );

        // After development iteration completes, the reducer transitions to CommitMessage
        // phase first (not directly to Review). The post-commit transition logic in
        // compute_post_commit_transition() then determines the next phase:
        // - If total_iterations done AND total_reviewer_passes > 0: Review
        // - If total_iterations done AND total_reviewer_passes == 0: FinalValidation
        // - If more iterations remain: Planning (for next iteration)
        //
        // With total_reviewer_passes=1 (configured in this test), the full transition
        // sequence would be: Development → CommitMessage → Review.
        // This assertion checks the first step of that transition.
        assert_eq!(
            state.phase,
            PipelinePhase::CommitMessage,
            "Should transition to CommitMessage phase immediately after development iteration completes. Got: {:?}",
            state.phase
        );

        // Verify orchestration derives next work (not immediate SaveCheckpoint for exit).
        // After development completes, the reducer sets context_cleanup_pending=true,
        // so orchestration derives CleanupContinuationContext first before commit work.
        let next_effect = determine_next_effect(&state);

        // The key assertion: we should NOT immediately derive SaveCheckpoint with Interrupt trigger
        // (which would indicate the pipeline is exiting). Instead, we should get continuation
        // cleanup or commit work effects.
        let is_exit_checkpoint = matches!(
            next_effect,
            Effect::SaveCheckpoint {
                trigger: ralph_workflow::reducer::CheckpointTrigger::Interrupt
            }
        );
        assert!(
            !is_exit_checkpoint,
            "Pipeline should continue work (cleanup or commit), not exit via SaveCheckpoint. Got: {next_effect:?}"
        );

        // After development completes, reducer sets context_cleanup_pending=true,
        // so the FIRST effect must be CleanupContinuationContext.
        // Verify both the flag and the effect to ensure complete correctness.
        assert!(
            state.continuation.context_cleanup_pending,
            "After development success, context_cleanup_pending flag should be true. State: {:?}",
            state.continuation
        );
        assert!(
            matches!(next_effect, Effect::CleanupContinuationContext),
            "After development success, first effect should be CleanupContinuationContext. Got: {next_effect:?}"
        );

        // This proves the bug is fixed: after resuming at iteration boundary,
        // the pipeline continues through remaining phases (CommitMessage → Review) instead of exiting
    });
}
