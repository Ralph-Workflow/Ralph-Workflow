//! Agent chain exhaustion tests.
//!
//! Tests behavior when all agents in the chain are exhausted and the
//! pipeline needs to cycle back to the first agent.

use crate::reducer::state_reduction::tests::*;

#[test]
fn test_reduce_agent_chain_exhaustion() {
    let state = PipelineState {
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec!["model1".to_string()]],
                AgentRole::Developer,
            )
            .with_max_cycles(3),
        ..create_test_state()
    };

    let exhausted_state = reduce(
        state,
        PipelineEvent::agent_chain_exhausted(AgentRole::Developer),
    );

    assert_eq!(exhausted_state.agent_chain.current_agent_index, 0);
    assert_eq!(exhausted_state.agent_chain.current_model_index, 0);
    assert_eq!(exhausted_state.agent_chain.retry_cycle, 1);
}
