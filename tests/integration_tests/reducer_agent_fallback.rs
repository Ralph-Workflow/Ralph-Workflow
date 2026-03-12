//! Tests that agent fallback decisions are ONLY made by the reducer.
//!
//! These tests verify that:
//! 1. Agent chain initialization happens via `Effect::InitializeAgentChain`
//! 2. Agent fallback happens via reducer events (`AgentEvent::InvocationFailed`, etc.)
//! 3. Orchestration correctly detects empty agent chains and emits initialization effects
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{AgentErrorKind, PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::{AgentChainState, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;

/// Test that Development phase with empty agent chain emits `InitializeAgentChain` effect.
///
/// When the Development phase starts with an empty agent chain,
/// orchestration MUST return `InitializeAgentChain` effect, not `PrepareDevelopmentContext`.
#[test]
fn test_development_phase_with_empty_chain_emits_initialize_effect() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: AgentChainState::initial(), // Empty chain
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Development,
                    ..
                }
            ),
            "Development with empty chain must emit InitializeAgentChain, got {effect:?}"
        );
    });
}

/// Test that Planning phase with empty agent chain emits `InitializeAgentChain` effect.
///
/// When the Planning phase starts with an empty agent chain,
/// orchestration MUST return `InitializeAgentChain` effect, not `PreparePlanningPrompt`.
#[test]
fn test_planning_phase_with_empty_chain_emits_initialize_effect() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            agent_chain: AgentChainState::initial(), // Empty chain
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Planning,
                    ..
                }
            ),
            "Planning with empty chain must emit InitializeAgentChain, got {effect:?}"
        );
    });
}

/// Test that Review phase with empty agent chain emits `InitializeAgentChain` effect.
///
/// When the Review phase starts with an empty agent chain,
/// orchestration MUST return `InitializeAgentChain` effect for Reviewer role.
#[test]
fn test_review_phase_with_empty_chain_emits_initialize_effect() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            agent_chain: AgentChainState::initial(), // Empty chain
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Review,
                    ..
                }
            ),
            "Review with empty chain must emit InitializeAgentChain for Reviewer, got {effect:?}"
        );
    });
}

/// Test that auth failure triggers reducer-managed agent fallback.
///
/// When agent auth fails, the reducer (not phase code) decides fallback by
/// processing `AgentEvent::InvocationFailed` with retriable=false.
#[test]
fn test_auth_failure_triggers_reducer_fallback() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            ),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Initial agent should be agent1
        assert_eq!(state.agent_chain.current_agent().unwrap(), "agent1");

        // Simulate auth failure via reducer event
        let new_state = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "agent1".to_string(),
                1,
                AgentErrorKind::Authentication,
                false,
            ),
        );

        // Reducer should have advanced to next agent (behavioral check via public accessor)
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            "agent2",
            "Reducer must advance agent chain on auth failure"
        );
    });
}

/// Test that agent chain is cleared when transitioning from Development to Review.
///
/// When transitioning from Development to Review (via commit creation),
/// the agent chain must be cleared/reset so Review initializes its own chain.
#[test]
fn test_agent_chain_clears_on_dev_to_review_transition() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Development),
            iteration: 4, // Last iteration (0-indexed, total=5)
            total_iterations: 5,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["dev_agent".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            ),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Transition to Review via commit creation
        let new_state = reduce(
            state,
            PipelineEvent::commit_created("abc123".to_string(), "msg".to_string()),
        );

        // Should now be in Review phase
        assert_eq!(new_state.phase, PipelinePhase::Review);

        // Agent chain should be reset for Reviewer role.
        // Verify behavioral outcome: orchestration should emit InitializeAgentChain for Reviewer.
        let effect = determine_next_effect(&new_state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Review,
                    ..
                }
            ),
            "After dev->review transition, orchestration must require chain re-initialization, got: {effect:?}"
        );
    });
}

/// Test that orchestration returns `PrepareDevelopmentContext` only when chain is initialized.
///
/// Must NOT return `PrepareDevelopmentContext` when chain is empty.
#[test]
fn test_orchestration_requires_initialized_chain_for_development() {
    with_default_timeout(|| {
        // Empty chain
        let state_empty = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: AgentChainState::initial(),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect_empty = determine_next_effect(&state_empty);
        assert!(
            !matches!(effect_empty, Effect::PrepareDevelopmentContext { .. }),
            "Must initialize agent chain before running development"
        );

        // Initialized chain
        let state_initialized = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 0,
            total_iterations: 5,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            ),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect_initialized = determine_next_effect(&state_initialized);
        assert!(
            matches!(effect_initialized, Effect::PrepareDevelopmentContext { .. }),
            "Should start development chain when initialized, got {effect_initialized:?}"
        );
    });
}

/// Test that orchestration begins the review chain only when chain is initialized.
///
/// Must NOT emit review effects when chain is empty.
#[test]
fn test_orchestration_requires_initialized_chain_for_review() {
    with_default_timeout(|| {
        // Empty chain
        let state_empty = PipelineState {
            phase: PipelinePhase::Review,
            agent_chain: AgentChainState::initial(),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect_empty = determine_next_effect(&state_empty);
        assert!(
            !matches!(effect_empty, Effect::PrepareReviewContext { .. }),
            "Must initialize agent chain before running review"
        );

        // Initialized chain
        let state_initialized = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["reviewer".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            ),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect_initialized = determine_next_effect(&state_initialized);
        assert!(
            matches!(effect_initialized, Effect::PrepareReviewContext { pass: 0 }),
            "Should begin review chain when chain is initialized, got {effect_initialized:?}"
        );
    });
}

/// Test that `ChainInitialized` event correctly sets up the agent chain.
///
/// When `AgentChainInitialized` event is processed, the state should have
/// the agent chain populated with the provided agents.
#[test]
fn test_chain_initialized_event_populates_state() {
    with_default_timeout(|| {
        // Define expected agents at test level for consistency between event and assertions
        const EXPECTED_AGENTS: [&str; 2] = ["claude", "codex"];

        let state = PipelineState {
            phase: PipelinePhase::Planning,
            agent_chain: AgentChainState::initial(), // Empty
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let new_state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Developer.into(),
                EXPECTED_AGENTS
                    .iter()
                    .map(std::string::ToString::to_string)
                    .collect(),
                3,
                1000,
                2.0,
                60000,
            ),
        );

        assert_eq!(
            new_state.agent_chain.agents.len(),
            EXPECTED_AGENTS.len(),
            "Should initialize with {} agents",
            EXPECTED_AGENTS.len()
        );
        assert_eq!(
            new_state.agent_chain.agents[0], EXPECTED_AGENTS[0],
            "First agent should be {}",
            EXPECTED_AGENTS[0]
        );
        assert_eq!(
            new_state.agent_chain.agents[1], EXPECTED_AGENTS[1],
            "Second agent should be {}",
            EXPECTED_AGENTS[1]
        );
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            EXPECTED_AGENTS[0],
            "Current agent should be the first agent ({})",
            EXPECTED_AGENTS[0]
        );
        assert_eq!(new_state.agent_chain.current_role, AgentRole::Developer);
    });
}

/// Test that rate limit fallback preserves prompt context.
///
/// When an agent hits rate limits, the reducer should preserve the prompt
/// for the next agent to continue the work.
#[test]
fn test_rate_limit_fallback_preserves_prompt() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            ),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let new_state = reduce(
            state,
            PipelineEvent::agent_rate_limited(
                AgentRole::Developer,
                "agent1".to_string(),
                Some("continue this work".to_string()),
            ),
        );

        assert_eq!(
            new_state.agent_chain.rate_limit_continuation_prompt,
            Some(
                ralph_workflow::reducer::state::RateLimitContinuationPrompt {
                    drain: ralph_workflow::agents::AgentDrain::Development,
                    role: AgentRole::Developer,
                    prompt: "continue this work".to_string(),
                }
            ),
            "Rate limit fallback should preserve prompt context"
        );
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            "agent2",
            "Should switch to next agent"
        );
    });
}

/// Test that auth fallback switches agent and does NOT preserve prompt.
///
/// Unlike rate limit fallback which preserves prompt context for continuation,
/// auth fallback indicates a credential issue with the current agent.
/// The next agent may have different (valid) credentials, so we don't
/// carry over prompt context.
#[test]
fn test_auth_fallback_switches_agent_without_prompt() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            ),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let new_state = reduce(
            state,
            PipelineEvent::agent_auth_failed(AgentRole::Developer, "agent1".to_string()),
        );

        // Should switch to next agent
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            "agent2",
            "Auth fallback should switch to next agent"
        );

        // Session should be cleared
        assert!(
            new_state.agent_chain.last_session_id.is_none(),
            "Auth fallback should clear session ID"
        );

        // Prompt context should NOT be preserved (unlike rate limit)
        assert!(
            new_state
                .agent_chain
                .rate_limit_continuation_prompt
                .is_none(),
            "Auth fallback should NOT preserve prompt context"
        );
    });
}

/// Test that reducer clears continuation prompt on success.
///
/// When an agent invocation succeeds, any saved continuation prompt
/// from a previous rate-limited agent should be cleared.
#[test]
fn test_success_clears_continuation_prompt() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            ),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Rate-limit agent1 to move to agent2 with saved prompt (event-driven setup)
        let state = reduce(
            state,
            PipelineEvent::agent_rate_limited(
                AgentRole::Developer,
                "agent1".to_string(),
                Some("saved prompt".to_string()),
            ),
        );
        // Verify preconditions established by the event
        assert_eq!(state.agent_chain.current_agent().unwrap(), "agent2");
        assert!(state.agent_chain.rate_limit_continuation_prompt.is_some());

        // Act: success on agent2 should clear continuation prompt
        let new_state = reduce(
            state,
            PipelineEvent::agent_invocation_succeeded(AgentRole::Developer, "agent2".to_string()),
        );

        assert!(
            new_state
                .agent_chain
                .rate_limit_continuation_prompt
                .is_none(),
            "Success should clear continuation prompt"
        );
    });
}

/// Test that exhausted agent chain emits an explicit abort effect.
///
/// When the agent chain is exhausted (all agents tried, max cycles reached),
/// orchestration must emit an explicit terminal effect. The pipeline must not
/// stall by repeatedly checkpointing.
#[test]
fn test_exhausted_chain_triggers_checkpoint() {
    with_default_timeout(|| {
        let mut state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["agent1".to_string()],
                    vec![vec![]],
                    AgentRole::Developer,
                )
                .with_max_cycles(3),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Exhaust all retry cycles through auth failures (event-driven setup).
        // For each non-terminal cycle, consume BackoffWait and emit RetryCycleStarted
        // using the cycle value carried by the effect (not reducer internals).
        loop {
            state = reduce(
                state,
                PipelineEvent::agent_invocation_failed(
                    AgentRole::Developer,
                    "agent1".to_string(),
                    1,
                    AgentErrorKind::Authentication,
                    false,
                ),
            );

            let effect = determine_next_effect(&state);
            match effect {
                Effect::BackoffWait {
                    role: AgentRole::Developer,
                    cycle,
                    ..
                } => {
                    state = reduce(
                        state,
                        PipelineEvent::agent_retry_cycle_started(AgentRole::Developer, cycle),
                    );
                }
                Effect::ReportAgentChainExhausted { .. } => break,
                other => panic!(
                    "Expected BackoffWait or ReportAgentChainExhausted while exhausting chain, got {other:?}"
                ),
            }
        }

        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::ReportAgentChainExhausted { .. }),
            "Exhausted chain should report exhaustion, got {effect:?}"
        );
    });
}

/// Test full agent fallback flow via reducer events.
///
/// This test simulates a complete fallback scenario:
/// 1. Start with agent chain
/// 2. First agent fails with auth error
/// 3. Reducer advances to next agent
/// 4. Second agent succeeds
#[test]
fn test_full_agent_fallback_flow() {
    with_default_timeout(|| {
        // Start with two agents
        let mut state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 0,
            total_iterations: 5,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["claude".to_string(), "codex".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            ),
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Verify we start with first agent
        assert_eq!(state.agent_chain.current_agent().unwrap(), "claude");

        // First agent fails with auth error
        state = reduce(
            state,
            PipelineEvent::agent_invocation_failed(
                AgentRole::Developer,
                "claude".to_string(),
                1,
                AgentErrorKind::Authentication,
                false,
            ),
        );

        // Should now be on second agent
        assert_eq!(state.agent_chain.current_agent().unwrap(), "codex");

        // Second agent succeeds
        state = reduce(
            state,
            PipelineEvent::agent_invocation_succeeded(AgentRole::Developer, "codex".to_string()),
        );

        // Should still be on second agent (success doesn't advance)
        assert_eq!(state.agent_chain.current_agent().unwrap(), "codex");
        assert!(
            state.agent_chain.rate_limit_continuation_prompt.is_none(),
            "Continuation prompt should be cleared on success"
        );
    });
}
