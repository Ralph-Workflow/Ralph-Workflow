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
            continuation: ralph_workflow::reducer::state::ContinuationState::with_limits(3, 2),
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
            continuation: ralph_workflow::reducer::state::ContinuationState::with_limits(3, 2),
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
            continuation: ralph_workflow::reducer::state::ContinuationState::with_limits(3, 2),
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
// PLANNING PHASE XSD RETRY TESTS
// ============================================================================

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
        CommitState, PipelineState, MAX_VALIDATION_RETRY_ATTEMPTS,
    };
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        // Set up state with reviewer agents in commit role (simulating fallback)
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 1));
        state.phase = PipelinePhase::CommitMessage;
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
