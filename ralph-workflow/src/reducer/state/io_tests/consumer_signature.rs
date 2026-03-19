// Tests for consumer_signature_sha256 stability.
//
// These tests verify that the consumer signature is:
// - Stable when only current_agent_index or current_model_index changes
// - Different when the agent chain configuration changes (agents, models, role)
// - Deterministic across repeated computations

use crate::agents::AgentRole;
use crate::reducer::state::AgentChainState;

#[test]
fn test_consumer_signature_stable_across_agent_index_changes() {
    // Changing current_agent_index should NOT change the signature.
    let chain = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string(), "agent-b".to_string()],
        vec![vec!["model-1".to_string()], vec!["model-2".to_string()]],
        AgentRole::Commit,
    );
    let sig1 = chain.consumer_signature_sha256();

    let chain2 = AgentChainState {
        current_agent_index: 1,
        ..chain
    };
    let sig2 = chain2.consumer_signature_sha256();

    assert_eq!(
        sig1, sig2,
        "signature should be stable when only current_agent_index changes"
    );
}

#[test]
fn test_consumer_signature_stable_across_model_index_changes() {
    // Changing current_model_index should NOT change the signature.
    let chain = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec!["model-1".to_string(), "model-2".to_string()]],
        AgentRole::Commit,
    );
    let sig1 = chain.consumer_signature_sha256();

    let chain2 = AgentChainState {
        current_model_index: 1,
        ..chain
    };
    let sig2 = chain2.consumer_signature_sha256();

    assert_eq!(
        sig1, sig2,
        "signature should be stable when only current_model_index changes"
    );
}

#[test]
fn test_consumer_signature_stable_across_retry_cycle_changes() {
    // Changing retry_cycle should NOT change the signature.
    let chain = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );
    let sig1 = chain.consumer_signature_sha256();

    let chain2 = AgentChainState {
        retry_cycle: 3,
        ..chain
    };
    let sig2 = chain2.consumer_signature_sha256();

    assert_eq!(
        sig1, sig2,
        "signature should be stable when only retry_cycle changes"
    );
}

#[test]
fn test_consumer_signature_changes_when_agents_change() {
    let chain1 = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );
    let chain2 = AgentChainState::initial().with_agents(
        vec!["agent-b".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );

    assert_ne!(
        chain1.consumer_signature_sha256(),
        chain2.consumer_signature_sha256(),
        "signature should change when agents differ"
    );
}

#[test]
fn test_consumer_signature_changes_when_models_change() {
    let chain1 = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec!["model-1".to_string()]],
        AgentRole::Commit,
    );
    let chain2 = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec!["model-2".to_string()]],
        AgentRole::Commit,
    );

    assert_ne!(
        chain1.consumer_signature_sha256(),
        chain2.consumer_signature_sha256(),
        "signature should change when models differ"
    );
}

#[test]
fn test_consumer_signature_changes_when_role_changes() {
    let chain1 = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );
    let chain2 = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );

    assert_ne!(
        chain1.consumer_signature_sha256(),
        chain2.consumer_signature_sha256(),
        "signature should change when role differs"
    );
}

#[test]
fn test_consumer_signature_is_deterministic() {
    let chain = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string(), "agent-b".to_string()],
        vec![
            vec!["model-1".to_string(), "model-2".to_string()],
            vec!["model-3".to_string()],
        ],
        AgentRole::Reviewer,
    );

    let sig1 = chain.consumer_signature_sha256();
    let sig2 = chain.consumer_signature_sha256();
    let sig3 = chain.consumer_signature_sha256();

    assert_eq!(sig1, sig2, "signature should be deterministic");
    assert_eq!(sig2, sig3, "signature should be deterministic");
}

#[test]
fn test_consumer_signature_is_order_independent_for_agents() {
    // The signature sorts agent pairs, so order shouldn't matter.
    let chain1 = AgentChainState::initial().with_agents(
        vec!["agent-a".to_string(), "agent-b".to_string()],
        vec![vec!["model-1".to_string()], vec!["model-2".to_string()]],
        AgentRole::Commit,
    );
    let chain2 = AgentChainState::initial().with_agents(
        vec!["agent-b".to_string(), "agent-a".to_string()],
        vec![vec!["model-2".to_string()], vec!["model-1".to_string()]],
        AgentRole::Commit,
    );

    assert_eq!(
        chain1.consumer_signature_sha256(),
        chain2.consumer_signature_sha256(),
        "signature should be order-independent for agent pairs"
    );
}

#[test]
fn test_consumer_signature_handles_empty_chain() {
    let chain = AgentChainState::initial();
    let sig = chain.consumer_signature_sha256();

    // Should not panic and should return a valid hash.
    assert_eq!(sig.len(), 64, "SHA-256 hash should be 64 hex characters");
}
