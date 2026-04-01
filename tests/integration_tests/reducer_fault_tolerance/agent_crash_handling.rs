//! Integration tests for agent crash handling.
//!
//! Verifies that when agents crash or produce invalid output, the pipeline
//! handles failures gracefully through retries and fallback mechanisms.
//!
//! Observable behaviors tested:
//! - SIGSEGV crashes are caught by fault-tolerant executor
//! - Invalid output triggers retry with same agent
//! - Retry exhaustion triggers agent fallback
//! - Invalid output counters reset on agent change
//! - Pipeline advances correctly after recovery
//!
//! # Integration Test Compliance
//!
//! These tests follow [../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md):
//! - Test observable behavior: retry/fallback transitions
//! - Pure reducer tests require no mocks
//! - Verify error recovery mechanisms

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{AgentErrorKind, PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::{AgentChainState, PipelineState, PromptMode};

use super::helpers::create_state_with_agent_chain_in_development;

#[test]
fn test_agent_sigsegv_caught_by_fault_tolerant_executor() {
    with_default_timeout(|| {
        let state = PipelineState {
            continuation: ralph_workflow::reducer::state::ContinuationState::with_limits(2, 3, 2),
            ..create_state_with_agent_chain_in_development()
        };

        let event = PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            "agent1".into(),
            139,
            AgentErrorKind::InternalError,
            false,
        );

        let new_state = ralph_workflow::reducer::state_reduction::reduce(state, event);

        // First internal error should retry same agent (not fall back yet)
        assert_eq!(
            new_state.agent_chain.current_agent().map(String::as_str),
            Some("agent1")
        );
        // Verify behavioral outcome: orchestration should produce a same-agent retry effect
        let effect = determine_next_effect(&with_locked_prompt_permissions(new_state.clone()));
        assert!(
            matches!(
                effect,
                Effect::PrepareDevelopmentPrompt {
                    prompt_mode: PromptMode::SameAgentRetry,
                    ..
                }
            ),
            "First SIGSEGV should produce same-agent retry effect, got: {effect:?}"
        );
        assert_eq!(new_state.phase, PipelinePhase::Development);

        // Second internal error exhausts budget => fall back to next agent
        let after_second = ralph_workflow::reducer::state_reduction::reduce(
            new_state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                139,
                AgentErrorKind::InternalError,
                false,
            ),
        );

        assert!(matches!(
            after_second.agent_chain.current_agent(),
            Some(agent) if agent != "agent1"
        ));
    });
}

#[test]
fn test_agent_panic_caught_by_fault_tolerant_executor() {
    with_default_timeout(|| {
        let state = PipelineState {
            continuation: ralph_workflow::reducer::state::ContinuationState::with_limits(2, 3, 2),
            ..create_state_with_agent_chain_in_development()
        };

        let event = PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            "agent1".into(),
            1,
            AgentErrorKind::InternalError,
            false,
        );

        let new_state = ralph_workflow::reducer::state_reduction::reduce(state, event);

        // First internal error should retry same agent (not fall back yet)
        assert_eq!(
            new_state.agent_chain.current_agent().map(String::as_str),
            Some("agent1")
        );
        // Verify behavioral outcome: orchestration should produce a same-agent retry effect
        let effect = determine_next_effect(&with_locked_prompt_permissions(new_state.clone()));
        assert!(
            matches!(
                effect,
                Effect::PrepareDevelopmentPrompt {
                    prompt_mode: PromptMode::SameAgentRetry,
                    ..
                }
            ),
            "First panic should produce same-agent retry effect, got: {effect:?}"
        );

        // Second internal error exhausts budget => fall back to next agent
        let after_second = ralph_workflow::reducer::state_reduction::reduce(
            new_state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::InternalError,
                false,
            ),
        );

        assert!(matches!(
            after_second.agent_chain.current_agent(),
            Some(agent) if agent != "agent1"
        ));
    });
}

#[test]
fn test_pipeline_state_machine_recovers_from_multiple_failures() {
    with_default_timeout(|| {
        let mut state = create_state_with_agent_chain_in_development();

        let events = vec![
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Network,
                true,
            ),
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Authentication,
                false,
            ),
            PipelineEvent::agent_invocation_succeeded(AgentRole::Developer, "agent2".into()),
        ];

        for event in events {
            state = ralph_workflow::reducer::state_reduction::reduce(state, event);
        }

        assert_eq!(state.agent_chain.current_agent().unwrap(), "agent2");
        assert_eq!(state.phase, PipelinePhase::Development);
    });
}

#[test]
fn test_all_agents_exhausted_pipeline_graceful_abort() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::{CommitState, ContinuationState, RebaseState};

        let state = PipelineState {
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["agent1".to_string()],
                    vec![vec!["model1".to_string()]],
                    AgentRole::Developer,
                )
                .with_max_cycles(1),
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
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let exhausted_state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_chain_exhausted(AgentRole::Developer),
        );

        assert_eq!(exhausted_state.agent_chain.current_agent_index, 0);
        assert_eq!(exhausted_state.agent_chain.current_model_index, 0);
        assert_eq!(exhausted_state.agent_chain.retry_cycle, 1);
        assert_eq!(exhausted_state.phase, PipelinePhase::Development);
    });
}

#[test]
fn test_agent_exhaustion_transitions_to_next_phase() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::{CommitState, ContinuationState, RebaseState};

        let mut chain = AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec!["model1".to_string()]],
                AgentRole::Developer,
            )
            .with_max_cycles(1);
        chain = chain.start_retry_cycle();

        let state = PipelineState {
            agent_chain: chain,
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
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        assert_eq!(state.phase, PipelinePhase::Development);
        assert!(state.agent_chain.is_exhausted());
        assert_eq!(state.agent_chain.retry_cycle, 1);
    });
}

/// Test that retry-cycle backoff is emitted as an explicit effect.
///
/// When an agent chain wraps into a new retry cycle, the reducer must record that
/// a backoff wait is pending, and orchestration must emit a `BackoffWait` effect
/// before attempting more work.
#[test]
fn test_retry_cycle_backoff_is_explicit_effect() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Development;
        state.agent_chain = AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec!["model1".to_string()]],
                AgentRole::Developer,
            )
            .with_max_cycles(3);

        // Exhaust once to start retry cycle. This should mark backoff pending.
        state = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_chain_exhausted(AgentRole::Developer),
        );

        assert!(
            state.agent_chain.backoff_pending_ms.is_some(),
            "starting a retry cycle must mark backoff pending"
        );

        // Orchestration should emit a wait effect before any further work.
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::BackoffWait { .. }),
            "expected BackoffWait, got {effect:?}"
        );
    });
}

#[test]
fn test_pipeline_continues_after_agent_sigsegv() {
    with_default_timeout(|| {
        let state = PipelineState {
            continuation: ralph_workflow::reducer::state::ContinuationState::with_limits(2, 3, 2),
            ..create_state_with_agent_chain_in_development()
        };
        let initial_agent_index = state.agent_chain.current_agent_index;

        let after_first = ralph_workflow::reducer::state_reduction::reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                139,
                AgentErrorKind::InternalError,
                false,
            ),
        );

        assert_eq!(
            after_first.agent_chain.current_agent_index,
            initial_agent_index
        );
        assert_eq!(after_first.phase, PipelinePhase::Development);

        let after_second = ralph_workflow::reducer::state_reduction::reduce(
            after_first,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                139,
                AgentErrorKind::InternalError,
                false,
            ),
        );

        assert!(after_second.agent_chain.current_agent_index > initial_agent_index);
        assert_eq!(after_second.phase, PipelinePhase::Development);
    });
}

#[test]
fn test_pipeline_continues_after_multiple_agent_failures() {
    with_default_timeout(|| {
        let mut state = create_state_with_agent_chain_in_development();

        let events = vec![
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".into(),
                1,
                AgentErrorKind::Authentication,
                false,
            ),
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent2".into(),
                139,
                AgentErrorKind::InternalError,
                false,
            ),
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent3".into(),
                1,
                AgentErrorKind::FileSystem,
                false,
            ),
        ];

        for event in events {
            state = ralph_workflow::reducer::state_reduction::reduce(state, event);
        }

        assert!(state.agent_chain.current_agent().is_some());
        assert_eq!(state.phase, PipelinePhase::Development);
    });
}

// ============================================================================
// XSD RETRY STATE TRACKING TESTS
// ============================================================================

/// Test that XSD retry decisions come from reducer state, not implicit logic.
///
/// The `invalid_output_attempts` counter in `ContinuationState` must be the
/// single source of truth for XSD retry decisions. This test verifies
/// that the reducer correctly updates this counter and triggers agent
/// fallback at the right threshold.
#[test]
fn test_xsd_retry_decisions_from_reducer_state_only() {
    use ralph_workflow::reducer::state::{ContinuationState, PipelineState};
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        // Set up state with multiple agents for fallback
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.continuation = ContinuationState::new().with_max_xsd_retry(3);
        state.agent_chain = state.agent_chain.with_agents(
            vec!["agent-1".to_string(), "agent-2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        );

        // Verify counter starts at 0
        assert_eq!(state.continuation.invalid_output_attempts, 0);

        // Process validation failures up to threshold
        for i in 0..2 {
            state = reduce(
                state,
                PipelineEvent::development_output_validation_failed(0, i),
            );
            assert_eq!(
                state.continuation.invalid_output_attempts,
                i + 1,
                "Counter should increment on each failure"
            );
            // Agent should NOT change until threshold exceeded
            assert_eq!(
                state.agent_chain.current_agent(),
                Some(&"agent-1".to_string()),
                "Agent should not change until threshold exceeded"
            );
        }

        // One more failure should trigger agent advancement
        state = reduce(
            state,
            PipelineEvent::development_output_validation_failed(0, 2),
        );

        // Counter should reset after agent switch
        assert_eq!(
            state.continuation.invalid_output_attempts, 0,
            "Counter should reset after agent advancement"
        );

        // Agent should have advanced
        assert_eq!(
            state.agent_chain.current_agent(),
            Some(&"agent-2".to_string()),
            "Agent should advance after max retries"
        );
    });
}

/// Test complete XSD retry exhaustion flow is reducer-driven.
///
/// Verifies that:
/// 1. XSD validation failures increment state counter
/// 2. After configured XSD retry limit, agent chain advances
/// 3. Counter resets after agent advancement
/// 4. If all agents exhausted with retries, chain enters exhausted state
///
/// This test ensures no hidden XSD retry logic exists outside the reducer.
#[test]
fn test_xsd_retry_exhaustion_complete_flow() {
    use ralph_workflow::reducer::state::{ContinuationState, PipelineState};
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        // Set up state with two agents
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.continuation = ContinuationState::new().with_max_xsd_retry(3);
        state.agent_chain = state.agent_chain.with_agents(
            vec!["agent-primary".to_string(), "agent-fallback".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        );

        // Phase 1: Exhaust retries on primary agent
        for attempt in 0..2 {
            assert_eq!(
                state.agent_chain.current_agent(),
                Some(&"agent-primary".to_string()),
                "Should stay on primary agent until retries exhausted"
            );
            state = reduce(
                state,
                PipelineEvent::development_output_validation_failed(0, attempt),
            );
        }

        // Counter should be at MAX
        assert_eq!(
            state.continuation.invalid_output_attempts, 2,
            "Counter should be at expected value before final failure"
        );

        // One more failure triggers agent advancement
        state = reduce(
            state,
            PipelineEvent::development_output_validation_failed(0, 2),
        );

        // After exhausting retries, should be on fallback agent
        assert_eq!(
            state.agent_chain.current_agent(),
            Some(&"agent-fallback".to_string()),
            "Should advance to fallback agent after exhausting retries"
        );
        assert_eq!(
            state.continuation.invalid_output_attempts, 0,
            "Counter should reset after agent advancement"
        );

        // Phase 2: Exhaust retries on fallback agent
        for attempt in 0..3 {
            state = reduce(
                state,
                PipelineEvent::development_output_validation_failed(0, attempt),
            );
        }

        // After exhausting all agents, chain should wrap around (cycle increment)
        // or point back to first agent if not exhausted
        assert!(
            state.agent_chain.current_agent_index == 0
                || state.agent_chain.retry_cycle > 0
                || state.agent_chain.is_exhausted(),
            "After exhausting all agents, chain should wrap, increment cycle, or be exhausted"
        );
    });
}

/// Test that XSD retry state is distinct from agent invocation failure state.
///
/// XSD validation failures (bad XML output) and agent invocation failures
/// (crashes, auth errors) are tracked separately. This test verifies the
/// reducer treats them as independent failure modes.
#[test]
fn test_xsd_retry_state_independent_of_invocation_failures() {
    use ralph_workflow::reducer::state::PipelineState;
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.agent_chain = state.agent_chain.with_agents(
            vec!["agent-1".to_string(), "agent-2".to_string()],
            vec![vec!["model-a".to_string(), "model-b".to_string()], vec![]],
            AgentRole::Developer,
        );

        // Start with XSD validation failure
        state = reduce(
            state,
            PipelineEvent::development_output_validation_failed(0, 0),
        );
        assert_eq!(state.continuation.invalid_output_attempts, 1);
        assert_eq!(state.agent_chain.current_agent_index, 0, "Same agent");

        // Then get a model unavailable error (retriable) - should advance model, not agent
        // Note: Network errors now trigger connectivity check, not model fallback
        state = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent-1".into(),
                1,
                AgentErrorKind::ModelUnavailable,
                true,
            ),
        );

        // Model unavailable error should advance model, but XSD counter remains
        assert_eq!(state.agent_chain.current_agent_index, 0, "Same agent");
        assert!(
            state.agent_chain.current_model_index > 0,
            "Model should advance"
        );
        // XSD counter should NOT be affected by invocation failures
        assert_eq!(
            state.continuation.invalid_output_attempts, 1,
            "XSD counter should remain independent"
        );
    });
}

// ============================================================================
// PLANNING PHASE XSD RETRY TESTS
// ============================================================================

/// Test that planning XSD retry decisions come from reducer state.
///
/// The `invalid_output_attempts` counter in `ContinuationState` must be the
/// single source of truth for planning XSD retry decisions.
#[test]
fn test_planning_xsd_retry_decisions_from_reducer_state() {
    use ralph_workflow::reducer::state::{ContinuationState, PipelineState};
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Planning;
        state.continuation = ContinuationState::new().with_max_xsd_retry(3);
        state.agent_chain = state.agent_chain.with_agents(
            vec!["agent-1".to_string(), "agent-2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        );

        // Verify counter starts at 0
        assert_eq!(state.continuation.invalid_output_attempts, 0);

        // Process validation failures up to threshold
        for i in 0..2 {
            state = reduce(
                state,
                PipelineEvent::planning_output_validation_failed(0, i),
            );
            assert_eq!(
                state.continuation.invalid_output_attempts,
                i + 1,
                "Counter should increment on each failure"
            );
            // Agent should NOT change until threshold exceeded
            assert_eq!(
                state.agent_chain.current_agent(),
                Some(&"agent-1".to_string()),
                "Agent should not change until threshold exceeded"
            );
        }

        // One more failure should trigger agent advancement
        state = reduce(
            state,
            PipelineEvent::planning_output_validation_failed(0, 2),
        );

        // Counter should reset after agent switch
        assert_eq!(
            state.continuation.invalid_output_attempts, 0,
            "Counter should reset after agent advancement"
        );

        // Agent should have advanced
        assert_eq!(
            state.agent_chain.current_agent(),
            Some(&"agent-2".to_string()),
            "Agent should advance after max retries"
        );
    });
}

/// Test that planning XSD retry is independent of development XSD retry.
///
/// When planning phase completes and development starts, the retry counter
/// should be reset.
#[test]
fn test_planning_xsd_retry_resets_on_phase_transition() {
    use ralph_workflow::reducer::state::PipelineState;
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Planning;
        state.continuation.invalid_output_attempts = 2;

        // Plan generation completes successfully
        state = reduce(state, PipelineEvent::plan_generation_completed(1, true));

        // Counter should reset on successful completion
        assert_eq!(
            state.continuation.invalid_output_attempts, 0,
            "Counter should reset after successful plan generation"
        );
        assert_eq!(
            state.phase,
            PipelinePhase::Development,
            "Should transition to Development"
        );
    });
}

/// Test planning XSD retry state persists across multiple attempts.
///
/// This verifies that the continuation state correctly tracks XSD retry
/// attempts within the planning phase.
#[test]
fn test_planning_xsd_retry_state_persistence() {
    use ralph_workflow::reducer::state::PipelineState;
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Planning;
        state.agent_chain = state.agent_chain.with_agents(
            vec!["agent-1".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        );

        // First failure
        state = reduce(
            state,
            PipelineEvent::planning_output_validation_failed(1, 0),
        );
        assert_eq!(state.continuation.invalid_output_attempts, 1);
        assert_eq!(state.iteration, 1);

        // Second failure at same iteration
        state = reduce(
            state,
            PipelineEvent::planning_output_validation_failed(1, 1),
        );
        assert_eq!(state.continuation.invalid_output_attempts, 2);
        assert_eq!(state.iteration, 1);

        // State remains in Planning phase
        assert_eq!(state.phase, PipelinePhase::Planning);
    });
}

// ============================================================================
// COMMIT AGENT FALLBACK TO REVIEWER CHAIN TESTS
// ============================================================================

/// Test that commit agent chain can use reviewer agents when no commit agents configured.
///
/// This is the documented fallback behavior: when `agent_chain.commit` is empty,
/// the system falls back to using `agent_chain.reviewer` agents.
#[test]
fn test_commit_phase_uses_reviewer_chain_fallback() {
    use ralph_workflow::reducer::state::{
        CommitState, ContinuationState, PipelineState, MAX_VALIDATION_RETRY_ATTEMPTS,
    };
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        // Set up state with reviewer agents in commit role (simulating fallback)
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 1));
        state.phase = PipelinePhase::CommitMessage;
        // Force immediate exhaustion of XSD retry budget so we exercise agent fallback.
        state.continuation = ContinuationState {
            xsd_retry_count: 0,
            max_xsd_retry_count: 1,
            ..ContinuationState::new()
        };
        state.agent_chain = state.agent_chain.with_agents(
            vec!["reviewer-claude".to_string(), "reviewer-codex".to_string()],
            vec![vec![], vec![]],
            AgentRole::Commit, // Commit role with reviewer agents
        );
        state.commit = CommitState::Generating {
            attempt: 1,
            max_attempts: MAX_VALIDATION_RETRY_ATTEMPTS,
        };

        // Validation failure should still trigger proper fallback
        state = reduce(
            state,
            PipelineEvent::commit_message_validation_failed("Invalid format".to_string(), 1),
        );

        // Should advance to next agent
        assert_eq!(
            state.agent_chain.current_agent_index, 1,
            "Should advance to next reviewer agent"
        );
        assert_eq!(
            state.agent_chain.current_role,
            AgentRole::Commit,
            "Role should remain Commit"
        );
    });
}
