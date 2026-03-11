// Review phase tests.
//
// Tests for ReviewPhaseStarted event handling: clearing agent chain,
// resetting continuation state, and preserving backoff policy.

use super::*;

#[test]
fn test_review_phase_started_clears_agent_chain_for_reviewer_role() {
    use crate::reducer::orchestration::determine_next_effect;

    // Simulate typical state after Development where the agent chain is populated
    // for developer runs.
    let state = create_test_state();

    // Enter Review phase.
    let review_state = reduce(state, PipelineEvent::review_phase_started());

    // The reviewer phase must not reuse the developer chain.
    assert!(
        review_state.agent_chain.agents.is_empty(),
        "Review phase should clear populated agent_chain to force reviewer initialization"
    );
    assert_eq!(
        review_state.agent_chain.current_role,
        AgentRole::Reviewer,
        "Review phase should set agent_chain role to Reviewer"
    );

    // Orchestration should deterministically emit InitializeAgentChain for reviewers.
    let effect = determine_next_effect(&review_state);
    assert!(matches!(
        effect,
        crate::reducer::effect::Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Review,
            ..
        }
    ));
}

#[test]
fn test_review_phase_started_resets_continuation_state() {
    use crate::reducer::state::{ContinuationState, DevelopmentStatus};

    let state = PipelineState {
        continuation: ContinuationState {
            previous_status: Some(DevelopmentStatus::Partial),
            previous_summary: Some("prev summary".to_string()),
            previous_files_changed: Some(vec!["src/lib.rs".to_string()].into_boxed_slice()),
            previous_next_steps: Some("next steps".to_string()),
            continuation_attempt: 2,
            invalid_output_attempts: 3,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    let review_state = reduce(state, PipelineEvent::review_phase_started());

    assert_eq!(
        review_state.continuation,
        ContinuationState::new(),
        "Entering Review should reset continuation state to avoid cross-phase leakage"
    );
}

#[test]
fn test_review_phase_started_preserves_agent_chain_backoff_policy() {
    // Review phase resets the chain, but must preserve the configured
    // retry/backoff policy so behavior is consistent across phases.
    let mut state = create_test_state();
    state.agent_chain = state
        .agent_chain
        .with_max_cycles(7)
        .with_backoff_policy(1234, 3.5, 98765);

    let review_state = reduce(state.clone(), PipelineEvent::review_phase_started());

    assert_eq!(
        review_state.agent_chain.max_cycles,
        state.agent_chain.max_cycles
    );
    assert_eq!(
        review_state.agent_chain.retry_delay_ms,
        state.agent_chain.retry_delay_ms
    );
    assert!(
        (review_state.agent_chain.backoff_multiplier - state.agent_chain.backoff_multiplier).abs()
            < f64::EPSILON
    );
    assert_eq!(
        review_state.agent_chain.max_backoff_ms,
        state.agent_chain.max_backoff_ms
    );
}
