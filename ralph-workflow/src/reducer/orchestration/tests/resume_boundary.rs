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
) -> PipelineState {
    PipelineState {
        phase,
        previous_phase: None,
        iteration,
        total_iterations,
        reviewer_pass,
        total_reviewer_passes,
        review_issues_found: false,
        interrupted_by_user: false,
        termination_resume_phase: None,
        pre_termination_commit_checked: false,
        // All progress flags reset to None (simulating resume from checkpoint)
        planning_prompt_prepared_iteration: None,
        planning_required_files_cleaned_iteration: None,
        planning_agent_invoked_iteration: None,
        planning_xml_extracted_iteration: None,
        planning_validated_outcome: None,
        planning_markdown_written_iteration: None,
        planning_xml_archived_iteration: None,
        development_context_prepared_iteration: None,
        development_prompt_prepared_iteration: None,
        development_required_files_cleaned_iteration: None,
        development_agent_invoked_iteration: None,
        analysis_agent_invoked_iteration: None,
        development_xml_extracted_iteration: None,
        development_validated_outcome: None,
        development_xml_archived_iteration: None,
        review_context_prepared_pass: None,
        review_prompt_prepared_pass: None,
        review_required_files_cleaned_pass: None,
        review_agent_invoked_pass: None,
        review_issues_xml_extracted_pass: None,
        review_validated_outcome: None,
        review_issues_markdown_written_pass: None,
        review_issue_snippets_extracted_pass: None,
        review_issues_xml_archived_pass: None,
        fix_prompt_prepared_pass: None,
        fix_required_files_cleaned_pass: None,
        fix_agent_invoked_pass: None,
        fix_result_xml_extracted_pass: None,
        fix_validated_outcome: None,
        fix_result_xml_archived_pass: None,
        commit_prompt_prepared: false,
        commit_diff_prepared: false,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: None,
        commit_agent_invoked: false,
        commit_required_files_cleaned: false,
        commit_xml_extracted: false,
        commit_validated_outcome: None,
        commit_xml_archived: false,
        context_cleaned: false,
        agent_chain: AgentChainState::initial(),
        rebase: crate::reducer::state::RebaseState::NotStarted,
        commit: CommitState::NotStarted,
        execution_history: crate::reducer::state::BoundedExecutionHistory::new(),
        checkpoint_saved_count: 0,
        continuation: crate::reducer::state::ContinuationState::new(),
        dev_fix_triggered: false,
        dev_fix_attempt_count: 0,
        recovery_epoch: 0,
        recovery_escalation_level: 0,
        failed_phase_for_recovery: None,
        completion_marker_pending: false,
        completion_marker_is_failure: false,
        completion_marker_reason: None,
        gitignore_entries_ensured: false,
        prompt_inputs: crate::reducer::state::PromptInputsState::default(),
        prompt_history: std::collections::HashMap::new(),
        // Simulate that permissions were locked at original startup (resume scenario)
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        last_substitution_log: None,
        template_validation_failed: false,
        template_validation_unsubstituted: Vec::new(),
        metrics: crate::reducer::state::RunMetrics {
            max_dev_iterations: total_iterations,
            max_review_passes: total_reviewer_passes,
            ..Default::default()
        },
        cloud: crate::config::CloudStateConfig::disabled(),
        pending_push_commit: None,
        git_auth_configured: false,
        pr_created: false,
        pr_url: None,
        push_count: 0,
        push_retry_count: 0,
        last_push_error: None,
        unpushed_commits: Vec::new(),
        last_pushed_commit: None,
        pr_number: None,
    }
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
            role: AgentRole::Developer
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
            role: AgentRole::Reviewer
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
            role: AgentRole::Developer
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
            role: AgentRole::Developer
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
