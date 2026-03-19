//! Agent chain construction and initialization tests.
//!
//! Tests for initializing agent chains with different roles and configurations.

use std::sync::Arc;

use crate::agents::{AgentDrain, AgentRole};
use crate::reducer::io_tests::{create_test_state, reduce, PipelineEvent, PipelineState};

#[test]
fn test_agent_chain_initialized_for_developer() {
    let state = create_test_state();
    let agents = vec!["agent1".to_string(), "agent2".to_string()];

    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Development,
            agents.clone(),
            3,
            1000,
            2.0,
            60000,
        ),
    );

    assert_eq!(new_state.agent_chain.agents, Arc::from(agents));
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 0);
    assert_eq!(new_state.agent_chain.current_drain, AgentDrain::Development);
}

#[test]
fn test_agent_chain_initialized_for_reviewer() {
    let state = create_test_state();
    let agents = vec!["reviewer1".to_string()];

    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Review,
            agents.clone(),
            3,
            1000,
            2.0,
            60000,
        ),
    );

    assert_eq!(new_state.agent_chain.agents, Arc::from(agents));
    assert_eq!(new_state.agent_chain.current_drain, AgentDrain::Review);
}

#[test]
fn test_agent_chain_initialized_for_commit_role() {
    let state = create_test_state();
    let agents = vec!["commit-agent1".to_string()];

    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Commit,
            agents.clone(),
            3,
            1000,
            2.0,
            60000,
        ),
    );

    assert_eq!(new_state.agent_chain.agents, Arc::from(agents));
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 0);
    assert_eq!(new_state.agent_chain.current_role, AgentRole::Commit);
    assert_eq!(new_state.agent_chain.current_drain, AgentDrain::Commit);
}

#[test]
fn test_agent_chain_initialized_resets_retry_cycle() {
    let base_state = create_test_state();
    // Setup with non-zero retry_cycle
    let mut agent_chain = base_state.agent_chain.clone();
    agent_chain.retry_cycle = 5; // Start with retry_cycle = 5

    let state = PipelineState {
        agent_chain,
        ..base_state
    };

    assert_eq!(state.agent_chain.retry_cycle, 5);

    let new_agents = vec!["new-agent1".to_string(), "new-agent2".to_string()];
    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Review,
            new_agents.clone(),
            3,
            1000,
            2.0,
            60000,
        ),
    );

    // CRITICAL: AgentChainInitialized uses reset_for_role() which RESETS retry_cycle to 0
    // This is DIFFERENT from reset() which preserves retry_cycle
    assert_eq!(new_state.agent_chain.agents, Arc::from(new_agents));
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_model_index, 0);
    assert_eq!(new_state.agent_chain.retry_cycle, 0); // RESET to 0, not preserved
    assert_eq!(new_state.agent_chain.current_role, AgentRole::Reviewer);
    assert_eq!(new_state.agent_chain.current_drain, AgentDrain::Review);
}

#[test]
fn test_agent_chain_initialized_with_empty_list() {
    let state = create_test_state();
    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Development,
            vec![],
            3,
            1000,
            2.0,
            60000,
        ),
    );

    // Empty agent list should be accepted
    assert_eq!(new_state.agent_chain.agents.len(), 0);
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
}

#[test]
fn test_agent_chain_initialized_contains_full_fallback_chain() {
    // When AgentChainInitialized event is emitted, it should contain
    // all agents from the fallback config, not just a single agent
    let state = create_test_state();
    let agents = vec![
        "codex".to_string(),
        "opencode".to_string(),
        "claude".to_string(),
    ];

    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Review,
            agents.clone(),
            3,
            1000,
            2.0,
            60000,
        ),
    );

    assert_eq!(
        new_state.agent_chain.agents,
        Arc::from(agents),
        "Agent chain should contain all agents from the fallback config"
    );
    assert_eq!(
        new_state.agent_chain.current_agent_index, 0,
        "Agent chain should start at index 0"
    );
    assert_eq!(
        new_state.agent_chain.current_agent().map(String::as_str),
        Some("codex"),
        "Current agent should be the first in the chain"
    );
}

#[test]
fn test_consumer_signature_distinguishes_shared_chains_by_drain() {
    let planning = crate::reducer::state::AgentChainState::initial()
        .with_agents(
            vec!["agent-a".to_string()],
            vec![vec!["model-1".to_string()]],
            AgentRole::Developer,
        )
        .with_drain(AgentDrain::Planning);
    let development = crate::reducer::state::AgentChainState::initial()
        .with_agents(
            vec!["agent-a".to_string()],
            vec![vec!["model-1".to_string()]],
            AgentRole::Developer,
        )
        .with_drain(AgentDrain::Development);

    assert_ne!(
        planning.consumer_signature_sha256(),
        development.consumer_signature_sha256(),
        "shared ordered chains must still hash differently for different drains"
    );
}
