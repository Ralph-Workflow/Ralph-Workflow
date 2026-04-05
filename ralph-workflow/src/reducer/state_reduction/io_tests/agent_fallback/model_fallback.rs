//! Model-level fallback wiring tests.
//!
//! Verifies that `ChainInitialized` events correctly populate `models_per_agent`
//! in the state, and that `advance_to_next_model` advances the model index (not
//! the agent index) when models are configured for the current agent.

use crate::agents::{AgentDrain, AgentRole};
use crate::common::domain_types::AgentName;
use crate::reducer::create_test_state;
use crate::reducer::event::{AgentErrorKind, PipelineEvent};
use crate::reducer::state_reduction::reduce;

/// Verify that a `ChainInitialized` event with non-empty `models_per_agent` correctly
/// populates `agent_chain.models_per_agent` in the resulting state.
#[test]
fn test_chain_initialized_event_with_models_populates_models_per_agent() {
    let state = create_test_state();
    let agents = vec![
        AgentName::from("opencode/zai/glm-4.7"),
        AgentName::from("claude"),
    ];
    let models_per_agent = vec![
        vec![
            "-m opencode/glm-4.7-free".to_string(),
            "-m opencode/claude-sonnet-4".to_string(),
        ],
        vec![], // claude has no model fallback
    ];

    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Development,
            agents,
            models_per_agent.clone(),
            3,
            1000,
            2.0,
            60000,
        ),
    );

    assert_eq!(
        new_state.agent_chain.models_per_agent.len(),
        2,
        "models_per_agent must have one entry per agent"
    );
    assert_eq!(
        new_state.agent_chain.models_per_agent[0],
        vec![
            "-m opencode/glm-4.7-free".to_string(),
            "-m opencode/claude-sonnet-4".to_string()
        ],
        "first agent's model list must match what was passed in ChainInitialized"
    );
    assert!(
        new_state.agent_chain.models_per_agent[1].is_empty(),
        "second agent (claude) must have an empty model list"
    );
}

/// Verify that `InvocationFailed(retriable=true)` advances the model index for the current
/// agent rather than switching to the next agent when models are configured.
#[test]
fn test_advance_to_next_model_with_models_advances_model_not_agent() {
    let state = create_test_state();
    let agents = vec![
        AgentName::from("opencode/zai/glm-4.7"),
        AgentName::from("claude"),
    ];
    let models_per_agent = vec![
        vec![
            "-m opencode/glm-4.7-free".to_string(),
            "-m opencode/claude-sonnet-4".to_string(),
        ],
        vec![],
    ];

    // Initialize chain with models for agent 0
    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Development,
            agents,
            models_per_agent,
            3,
            1000,
            2.0,
            60000,
        ),
    );

    assert_eq!(state.agent_chain.current_agent_index, 0);
    assert_eq!(state.agent_chain.current_model_index, 0);

    // Emit a retriable InvocationFailed (non-network) — this triggers advance_to_next_model.
    let agent_name = AgentName::from("opencode/zai/glm-4.7");
    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            agent_name,
            1,
            AgentErrorKind::ModelUnavailable,
            true,
        ),
    );

    assert_eq!(
        new_state.agent_chain.current_agent_index, 0,
        "agent index must remain 0 when models are available for the current agent"
    );
    assert_eq!(
        new_state.agent_chain.current_model_index, 1,
        "model index must advance to 1 when a model-level fallback is available"
    );
}

/// Verify the model-exhaustion boundary: after trying all models for agent 0, the
/// second retriable failure must switch to agent 1, reset current_model_index to 0,
/// and clear last_session_id (sessions are agent-scoped).
#[test]
fn test_model_exhaustion_boundary_switches_agent_and_clears_session() {
    let state = create_test_state();
    let agents = vec![
        AgentName::from("opencode/zai/glm-4.7"),
        AgentName::from("claude"),
    ];
    let models_per_agent = vec![
        vec![
            "-m opencode/glm-4.7-free".to_string(),
            "-m opencode/claude-sonnet-4".to_string(),
        ],
        vec![], // claude has no model fallback
    ];

    // Initialize chain with 2 models for agent 0.
    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            AgentDrain::Development,
            agents,
            models_per_agent,
            3,
            1000,
            2.0,
            60000,
        ),
    );

    // Establish a session so we can verify it is cleared on agent switch.
    let state = reduce(
        state,
        PipelineEvent::agent_session_established(
            AgentRole::Developer,
            AgentName::from("opencode/zai/glm-4.7"),
            "session-abc".to_string(),
        ),
    );
    assert_eq!(
        state.agent_chain.last_session_id,
        Some("session-abc".to_string()),
        "setup: session must be set before the boundary test"
    );

    // First retriable failure: model 0 → model 1, agent stays at 0, session preserved.
    let agent_name = AgentName::from("opencode/zai/glm-4.7");
    let state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            agent_name.clone(),
            1,
            AgentErrorKind::ModelUnavailable,
            true,
        ),
    );
    assert_eq!(
        state.agent_chain.current_agent_index, 0,
        "after first failure: agent index must stay at 0"
    );
    assert_eq!(
        state.agent_chain.current_model_index, 1,
        "after first failure: model index must advance to 1"
    );
    assert_eq!(
        state.agent_chain.last_session_id,
        Some("session-abc".to_string()),
        "after first failure: session must be preserved (same agent)"
    );

    // Second retriable failure: models exhausted for agent 0 → switch to agent 1,
    // model index resets to 0, session cleared (agent-scoped).
    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            agent_name,
            2,
            AgentErrorKind::ModelUnavailable,
            true,
        ),
    );
    assert_eq!(
        new_state.agent_chain.current_agent_index, 1,
        "after second failure: agent index must advance to 1 (models exhausted)"
    );
    assert_eq!(
        new_state.agent_chain.current_model_index, 0,
        "after second failure: model index must reset to 0 on agent switch"
    );
    assert_eq!(
        new_state.agent_chain.last_session_id, None,
        "after second failure: session must be cleared (sessions are agent-scoped)"
    );
}
