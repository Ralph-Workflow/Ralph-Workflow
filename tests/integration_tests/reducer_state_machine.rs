//! Reducer state machine integration tests.
//!
//! These tests verify that reducer state machine handles all transitions correctly
//! in real pipeline execution. Tests verify actual state changes through event
//! emission and `reduce()` function, not just unit tests of individual transitions.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (state machine transitions)
//! - Tests are deterministic and isolated
//! - Tests verify that reducer produces correct state for each event

use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::determine_next_effect;
use ralph_workflow::reducer::event::{AgentErrorKind, PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::state::{AgentChainState, PipelineState};

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;

fn create_initial_state() -> PipelineState {
    PipelineState::initial(5, 2)
}

fn create_state_with_agent_chain() -> PipelineState {
    PipelineState {
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![
                vec!["model1".to_string(), "model2".to_string()],
                vec!["model1".to_string(), "model2".to_string()],
            ],
            AgentRole::Developer,
        ),
        ..PipelineState::initial(5, 2)
    }
}

fn reduce(state: PipelineState, event: PipelineEvent) -> PipelineState {
    ralph_workflow::reducer::state_reduction::reduce(state, event)
}

#[test]
fn test_planning_to_development_transition() {
    with_default_timeout(|| {
        let state = create_initial_state();
        let new_state = reduce(state, PipelineEvent::planning_phase_completed());
        assert_eq!(new_state.phase, PipelinePhase::Development);
    });
}

#[test]
fn test_development_phase_starts() {
    with_default_timeout(|| {
        let state = create_initial_state();
        let new_state = reduce(state, PipelineEvent::development_phase_started());
        assert_eq!(new_state.phase, PipelinePhase::Development);
    });
}

#[test]
fn test_development_to_review_transition() {
    with_default_timeout(|| {
        let state = create_initial_state();
        let new_state = reduce(state, PipelineEvent::development_phase_completed());
        assert_eq!(new_state.phase, PipelinePhase::Review);
    });
}

#[test]
fn test_review_phase_starts() {
    with_default_timeout(|| {
        let state = create_initial_state();
        let new_state = reduce(state, PipelineEvent::review_phase_started());
        assert_eq!(new_state.phase, PipelinePhase::Review);
        assert_eq!(new_state.reviewer_pass, 0);
    });
}

#[test]
fn test_review_to_commit_message_transition() {
    with_default_timeout(|| {
        let state = create_initial_state();
        let new_state = reduce(state, PipelineEvent::review_phase_completed(false));
        assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    });
}

#[test]
fn test_commit_message_to_final_validation_on_commit() {
    with_default_timeout(|| {
        let state = create_initial_state();
        let new_state = reduce(
            state,
            PipelineEvent::commit_created("abc123".to_string(), "test commit".to_string()),
        );
        assert_eq!(new_state.phase, PipelinePhase::FinalValidation);
    });
}

#[test]
fn test_commit_message_to_final_validation_on_skip() {
    with_default_timeout(|| {
        let state = create_initial_state();
        let new_state = reduce(
            state,
            PipelineEvent::commit_skipped("no changes".to_string()),
        );
        assert_eq!(new_state.phase, PipelinePhase::FinalValidation);
    });
}

#[test]
fn test_pipeline_complete_transition() {
    with_default_timeout(|| {
        // Pipeline must go through FinalValidation -> Finalizing -> Complete
        let state = PipelineState {
            phase: PipelinePhase::FinalValidation,
            ..create_initial_state()
        };
        let state = reduce(state, PipelineEvent::final_state_validation_completed());
        assert_eq!(state.phase, PipelinePhase::Finalizing);
        let new_state = reduce(state, PipelineEvent::prompt_permissions_restored());
        assert_eq!(new_state.phase, PipelinePhase::Complete);
    });
}

#[test]
fn test_development_iteration_increments() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 2,
            total_iterations: 5,
            ..create_initial_state()
        };
        // DevelopmentIterationCompleted transitions to CommitMessage phase
        // The iteration stays the same; increment happens after CommitCreated
        let new_state = reduce(
            state,
            PipelineEvent::development_iteration_completed(2, true),
        );
        assert_eq!(new_state.iteration, 2);
        assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
        assert_eq!(new_state.previous_phase, Some(PipelinePhase::Development));
    });
}

#[test]
fn test_development_iteration_complete_moves_to_review() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 5,
            total_iterations: 5,
            ..create_initial_state()
        };
        // DevelopmentIterationCompleted goes to CommitMessage first
        // Transition to Review happens after CommitCreated when iteration >= total_iterations
        let new_state = reduce(
            state,
            PipelineEvent::development_iteration_completed(5, true),
        );
        assert_eq!(new_state.iteration, 5);
        assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
        assert_eq!(new_state.previous_phase, Some(PipelinePhase::Development));
    });
}

#[test]
fn test_development_iteration_started_resets_agent_chain() {
    with_default_timeout(|| {
        let mut state = create_state_with_agent_chain();
        state = reduce(
            state,
            PipelineEvent::agent_fallback_triggered(
                AgentRole::Developer,
                "agent1".into(),
                "agent2".into(),
            ),
        );
        assert_eq!(state.agent_chain.current_agent().unwrap(), "agent2");
        let new_state = reduce(state, PipelineEvent::development_iteration_started(2));
        assert_eq!(new_state.iteration, 2);
        assert_eq!(new_state.agent_chain.current_agent().unwrap(), "agent1");
    });
}

#[test]
fn test_review_pass_increments() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 1,
            total_reviewer_passes: 2,
            ..create_initial_state()
        };
        // FixAttemptCompleted transitions to CommitMessage phase
        // The reviewer_pass stays the same; increment happens after CommitCreated
        let new_state = reduce(state, PipelineEvent::fix_attempt_completed(1, true));
        assert_eq!(new_state.reviewer_pass, 1);
        assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
        assert_eq!(new_state.previous_phase, Some(PipelinePhase::Review));
    });
}

#[test]
fn test_review_pass_complete_moves_to_commit_message() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 2,
            total_reviewer_passes: 2,
            ..create_initial_state()
        };
        // FixAttemptCompleted goes to CommitMessage phase
        // reviewer_pass stays the same; increment happens after CommitCreated
        let new_state = reduce(state, PipelineEvent::fix_attempt_completed(2, true));
        assert_eq!(new_state.reviewer_pass, 2);
        assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
        assert_eq!(new_state.previous_phase, Some(PipelinePhase::Review));
    });
}

#[test]
fn test_agent_chain_resets_on_new_iteration() {
    with_default_timeout(|| {
        let mut state = create_state_with_agent_chain();
        state = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Network,
                true,
            ),
        );
        assert_eq!(
            state.agent_chain.current_model().unwrap(),
            "model2",
            "Network error should fallback to model2"
        );
        let new_state = reduce(state, PipelineEvent::development_iteration_started(2));
        assert_eq!(new_state.agent_chain.current_agent().unwrap(), "agent1");
        assert_eq!(
            new_state.agent_chain.current_model().unwrap(),
            "model1",
            "New iteration should reset to first model"
        );
    });
}

#[test]
fn test_agent_chain_advances_on_model_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain();
        let new_state = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Network,
                true,
            ),
        );
        // Should advance to model2 (next model for same agent)
        assert_eq!(
            new_state.agent_chain.current_model().unwrap(),
            "model2",
            "Network error should trigger model fallback"
        );
    });
}

#[test]
fn test_agent_fallback_on_auth_error() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain();

        // Simulate non-retriable error (auth) - should switch to next agent
        let new_state = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Authentication,
                false,
            ),
        );

        // Should switch to next agent (agent2) with first model
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            "agent2",
            "Auth error should switch to next agent"
        );
        assert_eq!(
            new_state.agent_chain.current_model().unwrap(),
            "model1",
            "New agent should start at first model"
        );
    });
}

#[test]
fn test_agent_chain_exhausted_triggers_retry_cycle() {
    with_default_timeout(|| {
        let state = PipelineState {
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["agent1".to_string()],
                    vec![vec!["model1".to_string()]],
                    AgentRole::Developer,
                )
                .with_max_cycles(3),
            ..create_initial_state()
        };

        // Simulate chain exhausted event
        let new_state = reduce(
            state,
            PipelineEvent::agent_chain_exhausted(AgentRole::Developer),
        );

        // Should reset selection to the first configured agent/model and enter retry backoff.
        assert_eq!(
            new_state.agent_chain.current_agent().map(String::as_str),
            Some("agent1")
        );
        assert_eq!(
            new_state.agent_chain.current_model().map(String::as_str),
            Some("model1")
        );

        // Behavioral check: after chain exhaustion, orchestration should schedule retry backoff.
        let effect = determine_next_effect(&with_locked_prompt_permissions(new_state));
        assert!(
            matches!(
                effect,
                ralph_workflow::reducer::effect::Effect::BackoffWait {
                    role: AgentRole::Developer,
                    ..
                }
            ),
            "Expected backoff wait effect after chain exhaustion, got {effect:?}"
        );
    });
}

#[test]
fn test_sigsegv_causes_agent_fallback() {
    with_default_timeout(|| {
        let state = PipelineState {
            continuation: ralph_workflow::reducer::state::ContinuationState::with_limits(2, 3, 2),
            ..create_state_with_agent_chain()
        };
        let initial_agent = state.agent_chain.current_agent().cloned();

        // Simulate SIGSEGV (exit code 139, internal error):
        // retry same agent first, then fall back after budget exhaustion.
        let after_first = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                139,
                AgentErrorKind::InternalError,
                false,
            ),
        );

        // First internal error should retry same agent (not fall back yet)
        assert_eq!(
            after_first.agent_chain.current_agent(),
            initial_agent.as_ref()
        );
        // Verify behavioral outcome: orchestration should produce a same-agent retry effect
        let effect = determine_next_effect(&with_locked_prompt_permissions(after_first.clone()));
        assert!(
            effect.is_same_agent_retry(),
            "First internal error should produce same-agent retry effect, got: {effect:?}"
        );

        let after_second = reduce(
            after_first,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                139,
                AgentErrorKind::InternalError,
                false,
            ),
        );

        // After exhausting same-agent retry budget, should fall back to next agent
        assert_ne!(
            after_second.agent_chain.current_agent(),
            initial_agent.as_ref()
        );
    });
}

#[test]
fn test_pipeline_continues_after_agent_failure() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![
                    vec!["model1".to_string(), "model2".to_string()],
                    vec!["model1".to_string()],
                ],
                AgentRole::Developer,
            ),
            ..create_initial_state()
        };
        let initial_agent = state.agent_chain.current_agent().cloned();

        // Agent fails with retriable error
        let new_state = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Network,
                true,
            ),
        );

        // Pipeline should continue with same agent, different model
        assert_eq!(new_state.phase, PipelinePhase::Development);
        assert_eq!(
            new_state.agent_chain.current_agent(),
            initial_agent.as_ref()
        );
    });
}

#[test]
fn test_network_error_triggers_model_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain();

        // Simulate network error (retriable) - should trigger model fallback event
        let new_state = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Network,
                true,
            ),
        );

        // Should advance to next model, not agent
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            "agent1",
            "Network error should stay on same agent"
        );
        assert_eq!(
            new_state.agent_chain.current_model().unwrap(),
            "model2",
            "Network error should fallback to next model"
        );
    });
}

#[test]
fn test_filesystem_error_triggers_agent_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain();
        let initial_agent = state.agent_chain.current_agent().cloned();

        // Simulate filesystem error (non-retriable) - should retry same agent first (except auth/429)
        let after_first_failure = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::FileSystem,
                false,
            ),
        );

        assert_eq!(
            after_first_failure.agent_chain.current_agent(),
            initial_agent.as_ref(),
            "Non-auth, non-rate-limit failures should retry same agent first"
        );
        // Verify behavioral outcome: orchestration should produce a same-agent retry effect
        let effect =
            determine_next_effect(&with_locked_prompt_permissions(after_first_failure.clone()));
        assert!(
            effect.is_same_agent_retry(),
            "Filesystem error should produce same-agent retry effect, got: {effect:?}"
        );

        let after_second_failure = reduce(
            after_first_failure,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::FileSystem,
                false,
            ),
        );

        // After exhausting retry budget, should switch to next agent
        assert_ne!(
            after_second_failure.agent_chain.current_agent(),
            initial_agent.as_ref()
        );
    });
}

/// Test that rate limit (429) triggers agent fallback via the fact event
/// `PipelineEvent::agent_rate_limited`.
///
/// Rate limit errors now trigger immediate agent fallback (not model fallback) to
/// allow work to continue without waiting for rate limits to reset. The reducer
/// preserves prompt context for continuation when switching agents.
#[test]
fn test_rate_limit_error_triggers_agent_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain();

        // Simulate a rate-limit fact event - should trigger agent fallback.
        let new_state = reduce(
            state,
            PipelineEvent::agent_rate_limited(
                AgentRole::Developer,
                "agent1".into(),
                Some("continue work".to_string()),
            ),
        );

        // Should switch to next agent
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            "agent2",
            "Rate limit should trigger agent fallback to agent2"
        );
        // New agent starts at first model
        assert_eq!(
            new_state.agent_chain.current_model().unwrap(),
            "model1",
            "New agent should start at first model"
        );
        // Prompt context should be preserved for continuation
        let prompt = new_state
            .agent_chain
            .rate_limit_continuation_prompt
            .as_ref()
            .expect("Rate limit fallback should preserve prompt context");
        assert_eq!(prompt.role, AgentRole::Developer);
        assert_eq!(prompt.prompt, "continue work");
    });
}

#[test]
fn test_authentication_error_triggers_agent_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain();
        let initial_agent = state.agent_chain.current_agent().cloned();

        // Simulate auth error (non-retriable) - should trigger agent fallback
        let new_state = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Authentication,
                false,
            ),
        );

        // Should switch to next agent
        assert_ne!(
            new_state.agent_chain.current_agent(),
            initial_agent.as_ref()
        );
    });
}

/// Test that agent fallback occurs after internal error retry budget is exhausted.
///
/// When an agent encounters internal errors repeatedly, the pipeline should:
/// 1. First retry the same agent up to `continuation.same_agent_retry_limit` times
/// 2. Then fall back to the next agent after retry budget is exhausted
///
/// This tests the observable behavior of agent selection after errors,
/// not internal retry logic implementation.
#[test]
fn test_agent_fallback_after_internal_error_retry_exhaustion() {
    with_default_timeout(|| {
        let state = PipelineState {
            continuation: ralph_workflow::reducer::state::ContinuationState::with_limits(2, 3, 2),
            ..create_state_with_agent_chain()
        };
        let initial_agent = state.agent_chain.current_agent().cloned();

        // Simulate internal error: retry same agent first, then fall back after budget exhaustion.
        let after_first = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::InternalError,
                false,
            ),
        );

        // Observable behavior: First internal error should retry same agent (not fall back yet)
        assert_eq!(
            after_first.agent_chain.current_agent(),
            initial_agent.as_ref(),
            "Should retry same agent on first internal error"
        );
        // Verify behavioral outcome: orchestration should produce a same-agent retry effect
        let effect = determine_next_effect(&with_locked_prompt_permissions(after_first.clone()));
        assert!(
            effect.is_same_agent_retry(),
            "Internal error should produce same-agent retry effect, got: {effect:?}"
        );

        let after_second = reduce(
            after_first,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::InternalError,
                false,
            ),
        );

        // Observable behavior: After exhausting retry budget, pipeline falls back to next agent
        assert_ne!(
            after_second.agent_chain.current_agent(),
            initial_agent.as_ref(),
            "Should fall back to next agent after retry budget exhausted"
        );
    });
}

#[test]
fn test_event_replay_reproduces_final_state() {
    with_default_timeout(|| {
        let initial_state = create_initial_state();

        let events = vec![
            PipelineEvent::development_phase_started(),
            PipelineEvent::development_iteration_started(1),
            PipelineEvent::development_iteration_completed(1, true),
            PipelineEvent::development_iteration_started(2),
            PipelineEvent::development_iteration_completed(2, true),
            PipelineEvent::development_iteration_completed(3, true),
            // Finalization path: FinalValidation -> Finalizing -> Complete
            PipelineEvent::final_state_validation_completed(),
            PipelineEvent::prompt_permissions_restored(),
        ];

        let final_state = events.into_iter().fold(initial_state, reduce);

        assert_eq!(final_state.phase, PipelinePhase::Complete);
        // The last DevelopmentIterationCompleted sets iteration to 3
        assert_eq!(final_state.iteration, 3);
    });
}

// ============================================================================
// CommitSkipped with previous_phase context tests
// ============================================================================
// These integration tests verify that CommitSkipped respects previous_phase
// for proper phase transitions, matching the behavior of CommitCreated.

/// Test that `CommitSkipped` after development iteration goes to Planning for next iteration.
///
/// When commit is skipped after a development iteration (not the last one),
/// the pipeline should go back to Planning for the next iteration, not `FinalValidation`.
#[test]
fn test_commit_skipped_respects_previous_phase_from_development() {
    with_default_timeout(|| {
        // Setup state as if we just completed a development iteration
        let state = PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Development),
            iteration: 0,
            total_iterations: 3,
            ..create_initial_state()
        };

        // Simulate commit being skipped (empty diff)
        let new_state = reduce(
            state,
            PipelineEvent::commit_skipped("No changes to commit (empty diff)".to_string()),
        );

        // Should go back to Planning for next iteration, NOT FinalValidation
        assert_eq!(new_state.phase, PipelinePhase::Planning);
        assert_eq!(new_state.iteration, 1);
        assert!(new_state.previous_phase.is_none());
    });
}

/// Test that `CommitSkipped` after last development iteration goes to Review.
///
/// When commit is skipped after the last development iteration,
/// the pipeline should go to Review, not `FinalValidation`.
#[test]
fn test_commit_skipped_after_last_dev_iteration_goes_to_review() {
    with_default_timeout(|| {
        // Setup state as if we just completed the last development iteration
        let state = PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Development),
            iteration: 2, // 0-indexed, this is the 3rd of 3 iterations
            total_iterations: 3,
            ..create_initial_state()
        };

        // Simulate commit being skipped
        let new_state = reduce(
            state,
            PipelineEvent::commit_skipped("No changes to commit".to_string()),
        );

        // Should go to Review after all dev iterations done
        assert_eq!(new_state.phase, PipelinePhase::Review);
        assert_eq!(new_state.iteration, 3);
    });
}

/// Test that `CommitSkipped` after fix attempt stays in Review for next pass.
///
/// When commit is skipped after a fix attempt (not the last review pass),
/// the pipeline should stay in Review for the next pass.
#[test]
fn test_commit_skipped_respects_previous_phase_from_review() {
    with_default_timeout(|| {
        // Setup state as if we just completed a fix attempt
        let state = PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Review),
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            ..create_initial_state()
        };

        // Simulate commit being skipped
        let new_state = reduce(
            state,
            PipelineEvent::commit_skipped("No changes to commit".to_string()),
        );

        // Should stay in Review for next pass
        assert_eq!(new_state.phase, PipelinePhase::Review);
        assert_eq!(new_state.reviewer_pass, 1);
    });
}

/// Test that `CommitSkipped` after last review pass goes to `FinalValidation`.
///
/// When commit is skipped after the last review pass,
/// the pipeline should go to `FinalValidation`.
#[test]
fn test_commit_skipped_after_last_review_goes_to_final_validation() {
    with_default_timeout(|| {
        // Setup state as if we just completed the last review pass
        let state = PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Review),
            reviewer_pass: 1, // 0-indexed, this is the 2nd of 2 passes
            total_reviewer_passes: 2,
            ..create_initial_state()
        };

        // Simulate commit being skipped
        let new_state = reduce(
            state,
            PipelineEvent::commit_skipped("No changes to commit".to_string()),
        );

        // Should go to FinalValidation after all review passes done
        assert_eq!(new_state.phase, PipelinePhase::FinalValidation);
    });
}
