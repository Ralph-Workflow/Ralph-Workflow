//! State transition tests during fallback.
//!
//! Tests for drain initialization timing consistency: the window between
//! when `current_drain` is set (e.g., to Fix) and when `agents` is populated
//! by a subsequent `ChainInitialized` event.

use crate::agents::DrainMode;
use crate::agents::{AgentDrain, AgentRole};
use crate::common::domain_types::AgentName;
use crate::reducer::create_test_state;
use crate::reducer::event::{PipelineEvent, PipelinePhase, TimeoutOutputKind};
use crate::reducer::state::{AgentChainState, ArtifactType, PipelineState};
use crate::reducer::state_reduction::reduce;

/// Build a state that represents a reviewer in mid-review (pass 0, issues not yet found,
/// agents populated). Used as the starting point for sequence-faithful review→Fix tests.
fn state_in_review_with_agents() -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: false,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string(), "codex".to_string()],
                vec![vec![], vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(AgentDrain::Review),
        ..create_test_state()
    }
}

/// Build a state where `current_drain = Fix` but `agents` list is empty.
///
/// This represents the intermediate window between a review completion event
/// (which sets the drain to Fix) and the subsequent `ChainInitialized` event
/// that populates the agents list.
fn state_with_fix_drain_and_empty_agents() -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Review,
        agent_chain: AgentChainState::initial().with_drain(AgentDrain::Fix),
        ..create_test_state()
    }
}

#[test]
fn test_invocation_started_with_empty_agents_does_not_panic() {
    // InvocationStarted must be handled safely even when agents=[], since the drain
    // may be set before the chain is initialized.
    let state = state_with_fix_drain_and_empty_agents();
    assert!(state.agent_chain.agents.is_empty());
    assert_eq!(state.agent_chain.current_drain, AgentDrain::Fix);

    let agent = AgentName::from("claude".to_string());
    let next = reduce(
        state,
        PipelineEvent::agent_invocation_started(AgentRole::Reviewer, agent, None),
    );

    // Must not panic; xsd_retry_session_reuse_pending must be cleared
    assert!(!next.continuation.xsd_retry_session_reuse_pending);
    assert_eq!(next.agent_chain.current_drain, AgentDrain::Fix);
}

#[test]
fn test_xsd_validation_failed_with_empty_agents_transitions_to_xsd_retry_mode() {
    // XsdValidationFailed must set drain mode to XsdRetry even when agents=[].
    // This represents the case where validation fails before ChainInitialized.
    let state = state_with_fix_drain_and_empty_agents();
    assert!(state.agent_chain.agents.is_empty());

    let next = reduce(
        state,
        PipelineEvent::agent_xsd_validation_failed(
            AgentRole::Reviewer,
            ArtifactType::Issues,
            "schema error".to_string(),
            0,
        ),
    );

    assert_eq!(
        next.agent_chain.current_mode,
        DrainMode::XsdRetry,
        "XsdValidationFailed must transition to XsdRetry mode even with empty agents list"
    );
    assert_eq!(next.agent_chain.current_drain, AgentDrain::Fix);
    assert!(
        next.continuation.xsd_retry_pending,
        "xsd_retry_pending must be set after XsdValidationFailed"
    );
}

#[test]
fn test_chain_initialized_after_fix_drain_populates_agents_and_preserves_drain() {
    // Simulates the common sequence: set drain to Fix (e.g., after review issues found),
    // then ChainInitialized populates the agents list.
    // After ChainInitialized, agents must be populated and current_drain must be Fix.
    let state = state_with_fix_drain_and_empty_agents();
    assert!(state.agent_chain.agents.is_empty());
    assert_eq!(state.agent_chain.current_drain, AgentDrain::Fix);

    let claude = AgentName::from("claude".to_string());
    let codex = AgentName::from("codex".to_string());

    let next = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Fix,
            vec![claude, codex],
            3,
            1000,
            2.0,
            60_000,
        ),
    );

    assert_eq!(
        next.agent_chain.agents.len(),
        2,
        "ChainInitialized must populate agents list"
    );
    assert_eq!(next.agent_chain.agents[0], "claude");
    assert_eq!(next.agent_chain.agents[1], "codex");
    assert_eq!(
        next.agent_chain.current_drain,
        AgentDrain::Fix,
        "current_drain must remain Fix after ChainInitialized with Fix drain"
    );
    assert_eq!(next.agent_chain.current_agent_index, 0);
    assert_eq!(next.agent_chain.current_model_index, 0);
    assert_eq!(next.agent_chain.retry_cycle, 0);
}

#[test]
fn test_timed_out_no_output_with_empty_agents_switches_agent_safely() {
    // TimedOut(NoOutput) must not panic when agents=[] — the switch_to_next_agent
    // call simply keeps agent_index=0 (no agents to switch to).
    let state = state_with_fix_drain_and_empty_agents();
    assert!(state.agent_chain.agents.is_empty());

    let agent = AgentName::from("claude".to_string());
    let next = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Reviewer,
            agent,
            TimeoutOutputKind::NoOutput,
            None,
            None,
        ),
    );

    // Must not panic; session must be cleared
    assert_eq!(next.agent_chain.last_session_id, None);
    assert_eq!(next.agent_chain.current_drain, AgentDrain::Fix);
}

#[test]
fn test_review_completed_with_issues_via_real_event_path_sets_fix_drain_with_empty_agents() {
    // Sequence-faithful test: rather than constructing the intermediate state by hand,
    // this test fires the real triggering event (ReviewCompleted with issues_found=true)
    // to reach the Fix-drain / empty-agents window, then fires ChainInitialized to
    // confirm the full two-step initialization sequence works correctly.
    //
    // Step 1: start from a normal Review state with agents populated.
    let state = state_in_review_with_agents();
    assert!(
        !state.agent_chain.agents.is_empty(),
        "precondition: agents must be populated before review completion"
    );
    assert_eq!(state.agent_chain.current_drain, AgentDrain::Review);

    // Step 2: fire ReviewCompleted with issues_found=true.
    // The reducer (pass_management) creates a fresh AgentChainState::initial().reset_for_drain(Fix),
    // which has an empty agents list.
    let state = reduce(state, PipelineEvent::review_completed(0, true));

    assert_eq!(
        state.agent_chain.current_drain,
        AgentDrain::Fix,
        "review completion with issues must switch current_drain to Fix"
    );
    assert!(
        state.agent_chain.agents.is_empty(),
        "agents list must be empty after review completion: ChainInitialized has not run yet"
    );

    // Step 3: fire ChainInitialized for Fix drain — this populates the agents list.
    let claude = AgentName::from("claude".to_string());
    let codex = AgentName::from("codex".to_string());
    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Fix,
            vec![claude, codex],
            3,
            1000,
            2.0,
            60_000,
        ),
    );

    assert_eq!(
        state.agent_chain.agents.len(),
        2,
        "ChainInitialized must populate the agents list"
    );
    assert_eq!(
        state.agent_chain.current_drain,
        AgentDrain::Fix,
        "current_drain must remain Fix after ChainInitialized"
    );
    assert_eq!(state.agent_chain.current_agent_index, 0);
    assert_eq!(state.agent_chain.current_model_index, 0);
    assert_eq!(state.agent_chain.retry_cycle, 0);
}
