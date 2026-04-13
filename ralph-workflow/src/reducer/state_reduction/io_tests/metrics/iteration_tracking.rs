//! Iteration counter tests
//!
//! Tests for iteration and attempt counters:
//! - Development iteration started/completed
//! - Review pass started/completed
//! - Agent invocation attempts
//! - Analysis agent invocation counters

use super::*;

#[test]
fn test_dev_iteration_started_increments_counter() {
    let state = PipelineState::initial(3, 0);
    assert_eq!(state.metrics.dev_iterations_started, 0);

    let event = PipelineEvent::development_iteration_started(0);
    let state = reduce(state, event);

    assert_eq!(state.metrics.dev_iterations_started, 1);
    assert_eq!(state.metrics.analysis_attempts_in_current_iteration, 0);
}

#[test]
fn test_dev_agent_invoked_increments_attempts() {
    let mut state = PipelineState::initial(3, 0);
    state = reduce(state, PipelineEvent::development_iteration_started(0));

    let event = PipelineEvent::development_agent_invoked(0);
    let state = reduce(state, event);

    assert_eq!(state.metrics.dev_attempts_total, 1);
}

#[test]
fn test_analysis_agent_invoked_increments_both_counters() {
    let mut state = PipelineState::initial(3, 0);
    state = reduce(state, PipelineEvent::development_iteration_started(0));

    let event = PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 });
    let state = reduce(state, event);

    assert_eq!(state.metrics.analysis_attempts_total, 1);
    assert_eq!(state.metrics.analysis_attempts_in_current_iteration, 1);

    // Next iteration resets per-iteration counter but not total
    let state = reduce(state, PipelineEvent::development_iteration_started(1));
    assert_eq!(state.metrics.analysis_attempts_total, 1);
    assert_eq!(state.metrics.analysis_attempts_in_current_iteration, 0);
}

#[test]
fn test_iteration_completed_increments_completed_counter() {
    let mut state = PipelineState::initial(3, 0);
    state = reduce(state, PipelineEvent::development_iteration_started(0));

    let event = PipelineEvent::development_iteration_completed(0, true);
    let state = reduce(state, event);

    assert_eq!(state.metrics.dev_iterations_completed, 1);
}

#[test]
fn test_continuation_does_not_increment_iterations_started() {
    let mut state = PipelineState::initial(3, 0);
    state = reduce(state, PipelineEvent::development_iteration_started(0));
    assert_eq!(state.metrics.dev_iterations_started, 1);

    // Trigger continuation
    let event = PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
        iteration: 0,
        status: DevelopmentStatus::Partial,
        summary: "some work done".to_string(),
        files_changed: None,
        next_steps: None,
    });
    let state = reduce(state, event);

    // Iterations started should not increment on continuation
    assert_eq!(state.metrics.dev_iterations_started, 1);

    // But dev attempts should increment when continuation runs
    let event = PipelineEvent::development_agent_invoked(0);
    let state = reduce(state, event);
    assert_eq!(state.metrics.dev_attempts_total, 1);
}

#[test]
fn test_review_pass_started_increments_counter_for_pass_0() {
    // Start with initial state (reviewer_pass = 0)
    let state = PipelineState::initial(0, 3);
    assert_eq!(state.reviewer_pass, 0);
    assert_eq!(state.metrics.review_passes_started, 0);

    // Starting pass 0 should count as starting the first pass.
    let event = PipelineEvent::review_pass_started(0);
    let state = reduce(state, event);

    assert_eq!(state.metrics.review_passes_started, 1);
    assert_eq!(state.reviewer_pass, 0);
}

#[test]
fn test_review_pass_started_increments_counter_for_pass_1() {
    // Start with initial state (reviewer_pass = 0)
    let state = PipelineState::initial(0, 3);
    assert_eq!(state.reviewer_pass, 0);
    assert_eq!(state.metrics.review_passes_started, 0);

    // Starting pass 1 should increment (0 != 1)
    let event = PipelineEvent::review_pass_started(1);
    let state = reduce(state, event);

    assert_eq!(state.metrics.review_passes_started, 1);
    assert_eq!(state.reviewer_pass, 1);
}

#[test]
fn test_review_agent_invoked_increments_runs() {
    let mut state = PipelineState::initial(0, 3);
    state = reduce(state, PipelineEvent::review_pass_started(0));

    let event = PipelineEvent::review_agent_invoked(0);
    let state = reduce(state, event);

    assert_eq!(state.metrics.review_runs_total, 1);
}

#[test]
fn test_fix_agent_invoked_increments_fix_runs() {
    let state = PipelineState::initial(0, 3);
    let event = PipelineEvent::Review(ReviewEvent::FixAgentInvoked { pass: 0 });
    let state = reduce(state, event);

    assert_eq!(state.metrics.fix_runs_total, 1);
}

#[test]
fn test_agent_fallback_increments_counter() {
    let state = PipelineState::initial(3, 0);
    let event = PipelineEvent::agent_fallback_triggered(
        AgentRole::Developer,
        AgentName::from("claude"),
        AgentName::from("gpt4"),
    );
    let state = reduce(state, event);

    assert_eq!(state.metrics.agent_fallbacks_total, 1);
}

#[test]
fn test_commit_created_increments_counter() {
    let state = PipelineState::initial(1, 0);
    let event = PipelineEvent::commit_created("abc123".to_string(), "test commit".to_string());
    let state = reduce(state, event);

    assert_eq!(state.metrics.commits_created_total, 1);
}

