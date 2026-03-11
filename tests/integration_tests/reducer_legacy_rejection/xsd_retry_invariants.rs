//! Integration tests for XSD retry state tracking invariants.
//!
//! Verifies that XSD validation retry logic is tracked explicitly in reducer state
//! rather than hidden in phase handlers, ensuring retry behavior is observable
//! and deterministic.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../../INTEGRATION_TESTS.md](../../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;

// ============================================================================
// XSD RETRY STATE TRACKING TESTS
// ============================================================================

/// Test that XSD validation failures produce observable retry behavior.
///
/// This verifies that when `OutputValidationFailed` events are processed, the reducer
/// produces retry effects (not chain exhaustion), making retry decisions
/// explicit and observable rather than hidden in phase code.
#[test]
fn test_xsd_retry_count_in_reducer_state() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::effect::Effect;
    use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase};
    use ralph_workflow::reducer::orchestration::determine_next_effect;
    use ralph_workflow::reducer::state::PipelineState;
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.agent_chain = state.agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        );

        // Initial development state should emit the first development step.
        let initial_effect = determine_next_effect(&state);
        assert_eq!(
            initial_effect,
            Effect::PrepareDevelopmentContext { iteration: 0 },
            "Initial development effect should be PrepareDevelopmentContext"
        );

        // One XSD validation failure should enter explicit XSD retry mode.
        let state = reduce(
            state,
            PipelineEvent::development_output_validation_failed(0, 0),
        );
        let effect = determine_next_effect(&state);
        assert_eq!(
            effect,
            Effect::InitializeAgentChain {
                drain: ralph_workflow::agents::AgentDrain::Analysis,
                role: AgentRole::Analysis,
            },
            "After first XSD failure, orchestration should switch to analysis role"
        );

        // Second failure (still within budget) should keep deriving XSD retry effect.
        let state = reduce(
            state,
            PipelineEvent::development_output_validation_failed(0, 1),
        );
        let effect = determine_next_effect(&state);
        assert_eq!(
            effect,
            Effect::InitializeAgentChain {
                drain: ralph_workflow::agents::AgentDrain::Analysis,
                role: AgentRole::Analysis,
            },
            "Under XSD retry budget, orchestration should continue deriving analysis retry effect"
        );
    });
}

/// Test that max XSD retries triggers agent advancement via reducer.
///
/// After configured XSD failures, the reducer should
/// advance the agent chain, making fallback behavior explicit in state.
#[test]
fn test_max_xsd_retries_advances_agent_chain_via_reducer() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::effect::Effect;
    use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase};
    use ralph_workflow::reducer::orchestration::determine_next_effect;
    use ralph_workflow::reducer::state::{ContinuationState, PipelineState};
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.continuation = ContinuationState::new().with_max_xsd_retry(3);
        state.agent_chain = state.agent_chain.with_agents(
            vec!["primary-agent".to_string(), "fallback-agent".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        );

        // Verify we start with primary agent
        assert_eq!(
            state.agent_chain.current_agent(),
            Some(&"primary-agent".to_string()),
            "Should start with primary agent"
        );

        // Under retry budget, orchestration should keep deriving XSD retry effects.
        let mut current_state = state;
        for attempt in 0..2 {
            current_state = reduce(
                current_state,
                PipelineEvent::development_output_validation_failed(0, attempt),
            );
            let effect = determine_next_effect(&current_state);
            assert_eq!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Analysis,
                    role: AgentRole::Analysis,
                },
                "Before exhaustion, XSD failures should derive analysis retry effect"
            );
        }

        // Before exhaustion we must still be on the primary agent.
        assert_eq!(
            current_state.agent_chain.current_agent(),
            Some(&"primary-agent".to_string()),
            "Before exhaustion, pipeline should still target primary agent"
        );

        // One more failure should trigger agent advancement and clear retry pending.
        let final_state = reduce(
            current_state,
            PipelineEvent::development_output_validation_failed(0, 2),
        );

        // Agent chain should have advanced.
        assert_eq!(
            final_state.agent_chain.current_agent(),
            Some(&"fallback-agent".to_string()),
            "Agent chain should advance to fallback after exhausting retries"
        );

        // After advancement, orchestration should resume normal development effect flow.
        let effect = determine_next_effect(&final_state);
        assert_eq!(
            effect,
            Effect::PrepareDevelopmentContext { iteration: 0 },
            "After advancing agents, orchestration should resume normal development flow"
        );
    });
}

/// Test that XSD retry loop exhaustion triggers reducer state transitions.
///
/// When XSD validation fails repeatedly, the reducer state must track exhaustion
/// and trigger agent advancement. Phase modules must NOT silently give up or
/// make fallback decisions internally.
#[test]
fn test_xsd_retry_exhaustion_triggers_state_transition() {
    use ralph_workflow::agents::AgentRole;
    use ralph_workflow::reducer::effect::Effect;
    use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase};
    use ralph_workflow::reducer::orchestration::determine_next_effect;
    use ralph_workflow::reducer::state::{ContinuationState, PipelineState};
    use ralph_workflow::reducer::state_reduction::reduce;

    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 1));
        state.phase = PipelinePhase::Development;
        state.continuation = ContinuationState::new().with_max_xsd_retry(3);
        state.agent_chain = state.agent_chain.with_agents(
            vec!["agent-1".to_string(), "agent-2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        );

        // Exhaust retries via reducer events (not hidden in phase code)
        let mut current = state;
        for attempt in 0..3 {
            current = reduce(
                current,
                PipelineEvent::development_output_validation_failed(0, attempt),
            );
        }

        // After exhausting retries, agent chain should advance
        // This proves the retry policy is in reducer, not phase module
        assert_eq!(
            current.agent_chain.current_agent(),
            Some(&"agent-2".to_string()),
            "Agent chain must advance after retry exhaustion (reducer-driven policy)"
        );

        // Retry flags should be cleared after exhaustion-driven agent switch.
        assert!(
            !current.continuation.xsd_retry_pending,
            "xsd_retry_pending should be cleared after retry exhaustion"
        );

        // Observable behavior: after agent switch, orchestration resumes normal flow.
        let effect = determine_next_effect(&current);
        assert_eq!(
            effect,
            Effect::PrepareDevelopmentContext { iteration: 0 },
            "After agent switch, orchestration should resume normal development flow"
        );
    });
}
