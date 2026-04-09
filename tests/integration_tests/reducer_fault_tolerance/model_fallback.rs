//! Integration tests for model fallback behavior.
//!
//! Verifies that when agents encounter transient errors (network issues,
//! rate limits, overloaded), the pipeline falls back to alternative models
//! within the same agent before switching agents.
//!
//! Observable behaviors tested:
//! - Network errors trigger model fallback
//! - Rate limit errors trigger model fallback
//! - Overloaded errors trigger model fallback
//! - Model fallback advances to next model in chain
//! - Model exhaustion triggers agent fallback
//!
//! # Integration Test Compliance
//!
//! These tests follow [../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md):
//! - Test observable behavior: model/agent chain transitions
//! - Pure reducer tests require no mocks

use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::event::{
    AgentErrorKind, PipelineEvent, PipelinePhase, TimeoutOutputKind,
};
use ralph_workflow::reducer::state::{AgentChainState, PipelineState};

use super::helpers::create_state_with_agent_chain_in_development;

#[test]
fn test_network_error_triggers_connectivity_check_not_model_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain_in_development();

        let event = PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            "agent1".into(),
            1,
            AgentErrorKind::Network,
            true,
        );

        let new_state = ralph_workflow::reducer::state_reduction::reduce(state, event);

        // Network error should set check_pending for connectivity verification
        assert_eq!(new_state.agent_chain.current_agent_index, 0);
        assert!(
            new_state.connectivity.check_pending,
            "Network error should set check_pending for connectivity verification"
        );
        assert_eq!(
            new_state.agent_chain.current_model_index, 0,
            "Network error should NOT advance model index (offline detection takes priority)"
        );
        assert_eq!(new_state.phase, PipelinePhase::Development);
    });
}

#[test]
fn test_auth_error_triggers_agent_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain_in_development();

        let event = PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            "agent1".into(),
            1,
            AgentErrorKind::Authentication,
            false,
        );

        let new_state = ralph_workflow::reducer::state_reduction::reduce(state, event);

        assert!(new_state.agent_chain.current_agent_index > 0);
        assert_eq!(new_state.phase, PipelinePhase::Development);
    });
}

#[test]
fn test_network_error_preserves_model_and_triggers_connectivity_check() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain_in_development();
        let initial_model_index = state.agent_chain.current_model_index;

        let new_state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Network,
                true,
            ),
        );

        // Network error should NOT advance model or switch agent
        assert_eq!(new_state.agent_chain.current_agent().unwrap(), "agent1");
        assert_eq!(
            new_state.agent_chain.current_model_index, initial_model_index,
            "Network error should NOT advance model (offline detection takes priority)"
        );
        assert!(
            new_state.connectivity.check_pending,
            "Network error should set check_pending for connectivity verification"
        );
        assert_eq!(new_state.phase, PipelinePhase::Development);
    });
}

/// Test that rate limit (429) errors trigger AGENT fallback, not model fallback.
///
/// This verifies the core behavior change: when an agent hits a 429 rate limit,
/// we immediately switch to the next agent in the chain rather than trying
/// the same agent with a different model. This allows work to continue without
/// waiting for rate limits to reset.
#[test]
fn test_rate_limit_429_triggers_agent_fallback_not_model_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain_in_development();
        let initial_agent_index = state.agent_chain.current_agent_index;

        // Simulate 429 rate limit - should switch to next AGENT, not model
        let new_state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_rate_limited(
                AgentRole::Developer,
                "agent1".into(),
                Some("continue this work".to_string()),
            ),
        );

        // Should switch to next agent
        assert!(
            new_state.agent_chain.current_agent_index > initial_agent_index,
            "429 should trigger agent fallback, not model fallback"
        );

        // Model index should reset to 0 (new agent starts fresh)
        assert_eq!(new_state.agent_chain.current_model_index, 0);

        // Prompt context should be preserved for continuation
        assert_eq!(
            new_state.agent_chain.rate_limit_continuation_prompt,
            Some(
                ralph_workflow::reducer::state::RateLimitContinuationPrompt {
                    drain: ralph_workflow::agents::AgentDrain::Development,
                    role: AgentRole::Developer,
                    prompt: "continue this work".to_string(),
                }
            )
        );

        // Phase should remain Development
        assert_eq!(new_state.phase, PipelinePhase::Development);
    });
}

/// Test that auth failure errors (401/403) trigger AGENT fallback, not model fallback.
///
/// Auth failures indicate a credentials problem with the current agent/provider.
/// Like rate limits, we immediately switch to the next agent, but unlike rate
/// limits, we don't preserve prompt context since the issue is credentials.
#[test]
fn test_auth_failure_triggers_agent_fallback_not_model_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain_in_development();
        let initial_agent_index = state.agent_chain.current_agent_index;

        // Simulate auth failure - should switch to next AGENT, not model
        let new_state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_auth_failed(AgentRole::Developer, "agent1".into()),
        );

        // Should switch to next agent
        assert!(
            new_state.agent_chain.current_agent_index > initial_agent_index,
            "Auth failure should trigger agent fallback, not model fallback"
        );

        // Model index should reset to 0 (new agent starts fresh)
        assert_eq!(new_state.agent_chain.current_model_index, 0);

        // Prompt context should NOT be preserved (unlike rate limit)
        assert!(
            new_state
                .agent_chain
                .rate_limit_continuation_prompt
                .is_none(),
            "Auth fallback should NOT preserve prompt context"
        );

        // Phase should remain Development
        assert_eq!(new_state.phase, PipelinePhase::Development);
    });
}

/// Test that network errors trigger connectivity check, not model fallback.
///
/// With offline detection, network errors set check_pending which blocks
/// all budget-consuming effects (including model fallback) until connectivity
/// is verified. This prevents consuming budget while offline.
#[test]
fn test_network_error_does_not_advance_model() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain_in_development();
        let initial_agent_index = state.agent_chain.current_agent_index;
        let initial_model_index = state.agent_chain.current_model_index;

        // Network error should set check_pending (not advance model)
        let new_state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Network,
                true,
            ),
        );

        // Should stay on same agent
        assert_eq!(
            new_state.agent_chain.current_agent_index, initial_agent_index,
            "Network error should not switch agents"
        );

        // Model index should NOT advance - connectivity check takes priority
        assert_eq!(
            new_state.agent_chain.current_model_index, initial_model_index,
            "Network error should NOT advance model (offline detection takes priority)"
        );

        // Connectivity check should be pending
        assert!(
            new_state.connectivity.check_pending,
            "Network error should set check_pending for connectivity verification"
        );

        assert_eq!(new_state.phase, PipelinePhase::Development);
    });
}

/// Test that rate limit continuation prompt is cleared on successful agent execution.
#[test]
fn test_rate_limit_continuation_prompt_cleared_on_success() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::{CommitState, ContinuationState, RebaseState};

        // Create state with a saved continuation prompt
        let mut agent_chain = AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec!["model1".to_string()], vec!["model1".to_string()]],
            AgentRole::Developer,
        );
        agent_chain.rate_limit_continuation_prompt = Some(
            ralph_workflow::reducer::state::RateLimitContinuationPrompt {
                drain: ralph_workflow::agents::AgentDrain::Development,
                role: AgentRole::Developer,
                prompt: "saved prompt".to_string(),
            },
        );
        agent_chain.current_agent_index = 1; // On agent2 after rate limit fallback

        let state = PipelineState {
            agent_chain,
            phase: PipelinePhase::Development,
            previous_phase: None,
            iteration: 1,
            total_iterations: 5,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            review_issues_found: false,
            context_cleaned: false,
            rebase: RebaseState::NotStarted,
            commit: CommitState::NotStarted,
            continuation: ContinuationState::new(),
            checkpoint_saved_count: 0,
            execution_history: ralph_workflow::reducer::state::BoundedExecutionHistory::new(),
            ..PipelineState::initial(5, 2)
        };

        // Agent succeeds
        let new_state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_invocation_succeeded(AgentRole::Developer, "agent2".into()),
        );

        // Continuation prompt should be cleared
        assert!(
            new_state
                .agent_chain
                .rate_limit_continuation_prompt
                .is_none(),
            "Success should clear rate limit continuation prompt"
        );
    });
}

// ============================================================================
// COMMIT AGENT FALLBACK TO REVIEWER CHAIN TESTS
// ============================================================================

// ============================================================================
// TIMEOUT FALLBACK TESTS
// ============================================================================

/// Test that timeout errors retry the same agent first, then fall back to the next agent.
///
/// Timeouts should not trigger model fallback. Instead, the reducer retries the same agent
/// (without switching models) until the configured timeout retry budget is exhausted, and
/// only then switches to the next agent in the chain.
#[test]
fn test_timeout_retries_same_agent_then_agent_fallback_not_model_fallback() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::ContinuationState;

        let state = PipelineState {
            continuation: ContinuationState::with_limits(2, 3, 2),
            ..create_state_with_agent_chain_in_development()
        };
        let initial_agent_index = state.agent_chain.current_agent_index;

        // Simulate idle timeout - should retry same agent first
        let after_first_timeout = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent1".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );

        assert_eq!(
            after_first_timeout.agent_chain.current_agent_index, initial_agent_index,
            "Timeout should retry same agent first (no immediate agent fallback)"
        );
        assert_eq!(
            after_first_timeout.agent_chain.current_model_index, 0,
            "Timeout retry should not advance model"
        );

        // Prompt context should NOT be preserved (unlike rate limit)
        // because timeout may indicate partial progress that's hard to resume
        assert!(
            after_first_timeout
                .agent_chain
                .rate_limit_continuation_prompt
                .is_none(),
            "TimedOut should NOT preserve prompt context"
        );

        // Second timeout exhausts budget => fall back to next agent
        let after_second_timeout = ralph_workflow::reducer::state_reduction::reduce(
            after_first_timeout,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent1".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );

        assert!(
            after_second_timeout.agent_chain.current_agent_index > initial_agent_index,
            "After retry budget exhaustion, timeout should fall back to next agent"
        );

        // Phase should remain Development
        assert_eq!(after_second_timeout.phase, PipelinePhase::Development);
    });
}

/// Test that `NoResult` timeout clears session ID for immediate agent switch.
///
/// When timeout produces no meaningful output (`NoResult`), we switch agents
/// immediately and clear the session ID because the new agent will have its
/// own session context. This is distinct from `PartialResult` timeout which
/// preserves the session for context reuse with the same agent.
#[test]
fn test_timeout_no_output_clears_session_id() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::{CommitState, ContinuationState, RebaseState};

        let mut agent_chain = AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec!["model1".to_string()], vec!["model1".to_string()]],
            AgentRole::Developer,
        );
        agent_chain.last_session_id = Some("session-abc".to_string());

        let state = PipelineState {
            agent_chain,
            phase: PipelinePhase::Development,
            previous_phase: None,
            iteration: 1,
            total_iterations: 5,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            review_issues_found: false,
            context_cleaned: false,
            rebase: RebaseState::NotStarted,
            commit: CommitState::NotStarted,
            continuation: ContinuationState::with_limits(2, 3, 2),
            checkpoint_saved_count: 0,
            execution_history: ralph_workflow::reducer::state::BoundedExecutionHistory::new(),
            ..PipelineState::initial(5, 2)
        };

        // Simulate NoResult timeout - should switch agents immediately
        let new_state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent1".into(),
                TimeoutOutputKind::NoResult,
                None,
                None,
            ),
        );

        // Session ID should be cleared for NoOutput (immediate agent switch)
        assert!(
            new_state.agent_chain.last_session_id.is_none(),
            "NoResult timeout should clear session ID for immediate agent switch"
        );
    });
}

/// Test that timeout followed by successful retry with different agent works.
///
/// This is the end-to-end flow: first agent times out, second agent succeeds.
#[test]
fn test_timeout_followed_by_successful_retry_with_different_agent() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::ContinuationState;

        let mut state = PipelineState {
            continuation: ContinuationState::with_limits(2, 3, 2),
            ..create_state_with_agent_chain_in_development()
        };

        // Verify starting agent
        assert_eq!(
            state.agent_chain.current_agent().map(String::as_str),
            Some("agent1")
        );

        // First timeout retries same agent
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent1".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );

        assert_eq!(
            state.agent_chain.current_agent().map(String::as_str),
            Some("agent1"),
            "First timeout should retry same agent"
        );

        // Second timeout exhausts budget => fall back to second agent
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent1".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );

        assert_eq!(
            state.agent_chain.current_agent().map(String::as_str),
            Some("agent2"),
            "Second timeout should fall back to next agent"
        );

        // Second agent succeeds
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_invocation_succeeded(AgentRole::Developer, "agent2".into()),
        );

        // Should still be on second agent after success
        assert_eq!(
            state.agent_chain.current_agent().map(String::as_str),
            Some("agent2")
        );

        // Chain should not be exhausted
        assert!(!state.agent_chain.is_exhausted());

        // Phase should remain Development
        assert_eq!(state.phase, PipelinePhase::Development);
    });
}

/// Test that multiple consecutive timeouts properly cycle through agents.
///
/// When multiple agents timeout in sequence, the system should keep switching
/// until either an agent succeeds or all agents are exhausted.
#[test]
fn test_multiple_timeouts_cycle_through_agents() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::ContinuationState;

        let mut state = PipelineState {
            continuation: ContinuationState::with_limits(2, 3, 2),
            ..create_state_with_agent_chain_in_development()
        };

        // Agent 1: two timeouts => switch to agent 2
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent1".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent1".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );
        assert_eq!(
            state.agent_chain.current_agent().map(String::as_str),
            Some("agent2")
        );

        // Agent 2: two timeouts => switch to agent 3
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent2".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent2".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );
        assert_eq!(
            state.agent_chain.current_agent().map(String::as_str),
            Some("agent3")
        );

        // Agent 3: two timeouts => wrap to agent1 and increment retry cycle
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent3".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Developer,
                "agent3".into(),
                TimeoutOutputKind::PartialResult,
                Some(".agent/logs/developer_0.log".to_string()),
                None,
            ),
        );
        assert_eq!(
            state.agent_chain.current_agent().map(String::as_str),
            Some("agent1"),
            "Should wrap back to first agent"
        );
        assert_eq!(
            state.agent_chain.retry_cycle, 1,
            "Should increment retry cycle when wrapping"
        );
    });
}

/// Guard: non-retryable failures should not immediately switch agents (except auth/429),
/// but should still be routed through the reducer's same-agent retry path.
#[test]
fn test_network_error_non_retryable_triggers_agent_fallback() {
    with_default_timeout(|| {
        let state = create_state_with_agent_chain_in_development();
        let initial_agent_index = state.agent_chain.current_agent_index;

        let after_first_failure = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Network,
                false,
            ),
        );

        assert_eq!(
            after_first_failure.agent_chain.current_agent_index, initial_agent_index,
            "Non-retryable failures should retry same agent first (except auth/429)"
        );
        assert!(after_first_failure.continuation.same_agent_retry_pending);
        assert_eq!(after_first_failure.agent_chain.current_model_index, 0);
        assert_eq!(after_first_failure.phase, PipelinePhase::Development);

        let after_second_failure = ralph_workflow::reducer::state_reduction::reduce(
            after_first_failure,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Network,
                false,
            ),
        );

        assert!(
            after_second_failure.agent_chain.current_agent_index > initial_agent_index,
            "After exhausting same-agent retry budget, non-retryable failures should fall back"
        );
    });
}
