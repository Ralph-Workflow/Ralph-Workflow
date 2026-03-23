use crate::reducer::state::AgentChainState;

/// Test that model budget is calculated as min across all agents in chain.
///
/// When the agent chain contains [claude (300KB), qwen (100KB), default (200KB)],
/// the effective budget should be 100KB (the minimum).
#[test]
fn test_effective_model_budget_uses_min_across_agent_chain() {
    use crate::phases::commit::effective_model_budget_bytes;

    // claude (300KB) + qwen (100KB) + default (200KB) = min is 100KB
    let agents = vec![
        "claude-opus".to_string(),
        "qwen-turbo".to_string(),
        "gpt-4".to_string(),
    ];
    let budget = effective_model_budget_bytes(&agents);

    // qwen has the smallest budget at 100KB (GLM_MAX_PROMPT_SIZE)
    assert_eq!(
        budget, 100_000,
        "budget should be min across agent chain (qwen's 100KB)"
    );
}

/// Test that `consumer_signature_sha256` changes when agent chain configuration changes.
///
/// This ensures that when the agent chain is modified (agents added/removed),
/// the materialized inputs will be invalidated and re-materialized with
/// the new budget.
#[test]
fn test_consumer_signature_changes_with_agent_chain() {
    let chain1 = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Commit,
    );
    let chain2 = AgentChainState::initial().with_agents(
        vec!["claude".to_string(), "qwen".to_string()],
        vec![vec![], vec![]],
        crate::agents::AgentRole::Commit,
    );

    let sig1 = chain1.consumer_signature_sha256();
    let sig2 = chain2.consumer_signature_sha256();

    assert_ne!(
        sig1, sig2,
        "consumer signature should change when agent chain changes"
    );
}

/// Test that `consumer_signature_sha256` is stable when only `current_agent_index` changes.
///
/// During XSD retry or fallback attempts, the `current_agent_index` changes but
/// the overall chain configuration stays the same. The signature should be
/// stable so we don't unnecessarily re-materialize.
#[test]
fn test_consumer_signature_stable_during_fallback() {
    let chain1 = AgentChainState::initial().with_agents(
        vec!["claude".to_string(), "qwen".to_string()],
        vec![vec![], vec![]],
        crate::agents::AgentRole::Commit,
    );
    let mut chain2 = chain1.clone();
    chain2.current_agent_index = 1; // Fallback to second agent

    let sig1 = chain1.consumer_signature_sha256();
    let sig2 = chain2.consumer_signature_sha256();

    assert_eq!(
        sig1, sig2,
        "consumer signature should be stable when only current_agent_index changes"
    );
}
