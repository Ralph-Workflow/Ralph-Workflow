// Tests for resume behavior at iteration and review pass boundaries.
//
// These tests verify the fix for the checkpoint resume bug where the pipeline
// would skip work at boundaries (e.g., iteration == total_iterations).

use super::*;
use crate::reducer::event::CheckpointTrigger;

/// Helper to create a minimal `PipelineState` for testing resume scenarios.
fn create_resume_state(
    phase: PipelinePhase,
    iteration: u32,
    total_iterations: u32,
    reviewer_pass: u32,
    total_reviewer_passes: u32,
) -> Box<PipelineState> {
    // Use initial() to avoid constructing the full struct literal on the stack
    // (which would exceed the large_stack_frames threshold).
    let mut state = Box::new(PipelineState::initial(
        total_iterations,
        total_reviewer_passes,
    ));
    state.phase = phase;
    state.iteration = iteration;
    state.reviewer_pass = reviewer_pass;
    // Simulate that permissions were locked at original startup (resume scenario)
    state.prompt_permissions = crate::reducer::state::PromptPermissionsState {
        locked: true,
        restore_needed: true,
        restored: false,
        last_warning: None,
    };
    state
}

#[test]
fn test_resume_at_final_iteration_runs_development_work() {
    // Given: Resume from checkpoint at final iteration boundary
    // iteration=1, total_iterations=1, all progress flags are None
    let state = create_resume_state(PipelinePhase::Development, 1, 1, 0, 0);

    // When: Determine next effect
    let effect = determine_next_effect(&state);

    // Then: Should derive development work, NOT SaveCheckpoint
    // The bug was: orchestration would check 1 < 1 = false, skip to SaveCheckpoint
    // The fix is: check (1 < 1) || (1 == 1 && 1 > 0) = true, run development
    assert!(
        !matches!(effect, Effect::SaveCheckpoint { .. }),
        "Bug: Orchestration incorrectly derived SaveCheckpoint at iteration boundary. \
         Expected development work to be executed. Got: {effect:?}"
    );

    // Should be a development-related effect
    let is_dev_effect = matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Development,
            ..
        } | Effect::PrepareDevelopmentContext { .. }
    );
    assert!(
        is_dev_effect,
        "Expected development effect at iteration=1, total=1. Got: {effect:?}"
    );
}

#[test]
fn test_resume_at_final_review_pass_runs_review_work() {
    // Given: Resume from checkpoint at final review pass boundary
    // reviewer_pass=2, total_reviewer_passes=2, all progress flags are None
    let state = create_resume_state(PipelinePhase::Review, 3, 3, 2, 2);

    // When: Determine next effect
    let effect = determine_next_effect(&state);

    // Then: Should derive review work, NOT SaveCheckpoint
    assert!(
        !matches!(effect, Effect::SaveCheckpoint { .. }),
        "Bug: Orchestration incorrectly derived SaveCheckpoint at review pass boundary. \
         Expected review work to be executed. Got: {effect:?}"
    );

    // Should be a review-related effect
    let is_review_effect = matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Review,
            ..
        } | Effect::PrepareReviewContext { .. }
    );
    assert!(
        is_review_effect,
        "Expected review effect at reviewer_pass=2, total=2. Got: {effect:?}"
    );
}

#[test]
fn test_resume_with_zero_indexed_iteration() {
    // Given: Resume at iteration=0, total_iterations=1 (first and only iteration)
    let state = create_resume_state(PipelinePhase::Development, 0, 1, 0, 0);

    // When: Determine next effect
    let effect = determine_next_effect(&state);

    // Then: Should run iteration 0 (0 < 1 is true, so this should work regardless)
    let is_dev_effect = matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Development,
            ..
        } | Effect::PrepareDevelopmentContext { .. }
    );
    assert!(
        is_dev_effect,
        "Expected development work for iteration=0, total=1. Got: {effect:?}"
    );
}

#[test]
fn test_resume_mid_pipeline_continues_normally() {
    // Given: Resume mid-pipeline (iteration=2, total_iterations=5)
    let state = create_resume_state(PipelinePhase::Development, 2, 5, 0, 2);

    // When: Determine next effect
    let effect = determine_next_effect(&state);

    // Then: Should derive development work (2 < 5 is clearly true)
    let is_dev_effect = matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Development,
            ..
        } | Effect::PrepareDevelopmentContext { .. }
    );
    assert!(
        is_dev_effect,
        "Mid-pipeline resume should derive development effects. Got: {effect:?}"
    );
}

#[test]
fn test_resume_at_boundary_with_zero_total_iterations() {
    // Given: Edge case - total_iterations=0 in Development phase
    // This is an abnormal state (should start at CommitMessage phase), but we handle it gracefully
    let mut state = create_resume_state(PipelinePhase::Development, 0, 0, 0, 0);

    // Initialize agent chain to get past the chain initialization check
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );

    // When: Determine next effect
    let effect = determine_next_effect(&state);

    // Then: Should transition to next phase (SaveCheckpoint with PhaseTransition)
    // With total_iterations=0, iteration_needs_work = (0 < 0) || (0 == 0 && 0 > 0) = false
    // So we derive SaveCheckpoint to trigger phase transition
    //
    // The trigger must be PhaseTransition (not Interrupt) to indicate normal
    // progression rather than pipeline termination.
    assert!(
        matches!(
            effect,
            Effect::SaveCheckpoint {
                trigger: CheckpointTrigger::PhaseTransition
            }
        ),
        "With total_iterations=0 in Development phase (abnormal state), \
         should transition to next phase via PhaseTransition. Got: {effect:?}"
    );
}

#[test]
fn test_resume_iteration_exceeds_total() {
    // Given: Abnormal state - iteration > total_iterations (should not happen)
    // Note: With empty agent chain, InitializeAgentChain is returned first
    let mut state = create_resume_state(PipelinePhase::Development, 5, 3, 0, 0);

    // Initialize agent chain to get past the chain initialization check
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );

    // When: Determine next effect
    let effect = determine_next_effect(&state);

    // Then: Should transition (5 < 3 is false, 5 == 3 is false)
    // iteration_needs_work is false, so should derive SaveCheckpoint
    assert!(
        matches!(effect, Effect::SaveCheckpoint { .. }),
        "When iteration exceeds total (abnormal state), should transition. Got: {effect:?}"
    );
}
