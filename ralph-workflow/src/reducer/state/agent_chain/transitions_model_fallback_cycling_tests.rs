use super::*;

#[test]
fn test_advance_to_next_model_cycles_through_multiple_models_before_switching_agent() {
    let state = AgentChainState::initial().with_agents(
        vec!["claude".to_string(), "codex".to_string()],
        vec![
            vec!["m1".to_string(), "m2".to_string()],
            vec!["m3".to_string()],
        ],
        AgentRole::Developer,
    );

    let state = state.advance_to_next_model();
    assert_eq!(state.current_agent_index, 0, "should stay on agent 0");
    assert_eq!(state.current_model_index, 1, "should advance to model 1");

    let state = state.advance_to_next_model();
    assert_eq!(state.current_agent_index, 1, "should switch to agent 1");
    assert_eq!(
        state.current_model_index, 0,
        "model_index must reset to 0 on agent switch"
    );
}

#[test]
fn test_advance_to_next_model_resets_model_index_to_zero_on_agent_switch() {
    let state = AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string(), "codex".to_string()],
            vec![
                vec!["m1".to_string(), "m2".to_string()],
                vec!["m3".to_string(), "m4".to_string()],
            ],
            AgentRole::Developer,
        )
        .advance_to_next_model();

    assert_eq!(state.current_model_index, 1);

    let state = state.advance_to_next_model();

    assert_eq!(state.current_agent_index, 1);
    assert_eq!(
        state.current_model_index, 0,
        "current_model_index must reset to 0 after switching to next agent"
    );
}

#[test]
fn test_advance_to_next_model_with_empty_models_list_switches_agent_immediately() {
    let state = AgentChainState::initial().with_agents(
        vec!["claude".to_string(), "codex".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );

    let next = state.advance_to_next_model();

    assert_eq!(
        next.current_agent_index, 1,
        "empty models list must cause immediate agent switch"
    );
    assert_eq!(next.current_model_index, 0);
}

#[test]
fn test_advance_to_next_model_on_last_agent_last_model_wraps_to_cycle() {
    let state = AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string()],
            vec![vec!["m1".to_string(), "m2".to_string()]],
            AgentRole::Developer,
        )
        .with_session_id(Some("sess".to_string()));

    let state = state.advance_to_next_model();
    assert_eq!(state.current_model_index, 1);

    let next = state.advance_to_next_model();

    assert_eq!(
        next.current_agent_index, 0,
        "should wrap back to agent 0 when all models exhausted (single-agent: same agent)"
    );
    assert_eq!(next.current_model_index, 0);
    assert_eq!(
        next.retry_cycle, 1,
        "retry_cycle must increment when chain wraps"
    );
    assert!(
        next.backoff_pending_ms.is_some(),
        "backoff must be set when starting a new retry cycle"
    );
    assert_eq!(
        next.last_session_id, None,
        "session must be cleared when chain wraps: switch_to_next_agent clears session"
    );
}

#[test]
fn test_advance_to_next_model_single_model_switches_to_next_agent_immediately() {
    let state = AgentChainState::initial().with_agents(
        vec!["claude".to_string(), "codex".to_string()],
        vec![vec!["m1".to_string()], vec!["m2".to_string()]],
        AgentRole::Developer,
    );

    let next = state.advance_to_next_model();

    assert_eq!(
        next.current_agent_index, 1,
        "single-model agent: advance_to_next_model must switch to next agent"
    );
    assert_eq!(next.current_model_index, 0);
    assert_eq!(
        next.last_session_id, None,
        "session must be cleared on agent switch"
    );
}
