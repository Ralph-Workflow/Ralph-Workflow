// =========================================================================
// Continuation state tests
// =========================================================================

#[test]
fn test_continuation_state_initial() {
    let state = ContinuationState::new();
    assert!(!state.is_continuation());
    assert_eq!(state.continuation_attempt, 0);
    assert!(state.previous_status.is_none());
    assert!(state.previous_summary.is_none());
    assert!(state.previous_files_changed.is_none());
    assert!(state.previous_next_steps.is_none());
}

#[test]
fn test_continuation_state_default() {
    let state = ContinuationState::default();
    assert!(!state.is_continuation());
    assert_eq!(state.continuation_attempt, 0);

    let pending = ContinuationState {
        continue_pending: true,
        fix_continue_pending: true,
        ..ContinuationState::default()
    };
    assert!(pending.pending_continuation_for_drain(crate::agents::AgentDrain::Development));
    assert!(pending.pending_continuation_for_drain(crate::agents::AgentDrain::Fix));
    assert!(!pending.pending_continuation_for_drain(crate::agents::AgentDrain::Review));
    assert!(!pending.pending_continuation_for_drain(crate::agents::AgentDrain::Analysis));

    let exhausted = ContinuationState {
        continuation_attempt: 2,
        max_continue_count: 2,
        fix_continuation_attempt: 1,
        max_fix_continue_count: 3,
        ..ContinuationState::default()
    };
    assert!(exhausted.continuation_exhausted_for_drain(crate::agents::AgentDrain::Development));
    assert!(!exhausted.continuation_exhausted_for_drain(crate::agents::AgentDrain::Fix));
    assert!(!exhausted.continuation_exhausted_for_drain(crate::agents::AgentDrain::Review));
}

#[test]
fn test_continuation_trigger_partial() {
    let state = ContinuationState::new();
    let new_state = state.trigger_continuation(
        DevelopmentStatus::Partial,
        "Did some work".to_string(),
        Some(vec!["file1.rs".to_string()]),
        Some("Continue with tests".to_string()),
    );

    assert!(new_state.is_continuation());
    assert_eq!(new_state.continuation_attempt, 1);
    assert_eq!(new_state.previous_status, Some(DevelopmentStatus::Partial));
    assert_eq!(
        new_state.previous_summary,
        Some("Did some work".to_string())
    );
    assert_eq!(
        new_state.previous_files_changed,
        Some(vec!["file1.rs".to_string()].into_boxed_slice())
    );
    assert_eq!(
        new_state.previous_next_steps,
        Some("Continue with tests".to_string())
    );
}

#[test]
fn test_continuation_trigger_failed() {
    let state = ContinuationState::new();
    let new_state = state.trigger_continuation(
        DevelopmentStatus::Failed,
        "Build failed".to_string(),
        None,
        Some("Fix errors".to_string()),
    );

    assert!(new_state.is_continuation());
    assert_eq!(new_state.continuation_attempt, 1);
    assert_eq!(new_state.previous_status, Some(DevelopmentStatus::Failed));
    assert_eq!(new_state.previous_summary, Some("Build failed".to_string()));
    assert!(new_state.previous_files_changed.is_none());
    assert_eq!(
        new_state.previous_next_steps,
        Some("Fix errors".to_string())
    );
}

#[test]
fn test_continuation_reset() {
    let state = ContinuationState::new().trigger_continuation(
        DevelopmentStatus::Partial,
        "Work".to_string(),
        None,
        None,
    );

    let reset = state.reset();
    assert!(!reset.is_continuation());
    assert_eq!(reset.continuation_attempt, 0);
    assert!(reset.previous_status.is_none());
    assert!(reset.previous_summary.is_none());
}

#[test]
fn test_multiple_continuations() {
    let state = ContinuationState::new()
        .trigger_continuation(
            DevelopmentStatus::Partial,
            "First".to_string(),
            Some(vec!["a.rs".to_string()]),
            None,
        )
        .trigger_continuation(
            DevelopmentStatus::Partial,
            "Second".to_string(),
            Some(vec!["b.rs".to_string()]),
            Some("Do more".to_string()),
        );

    assert_eq!(state.continuation_attempt, 2);
    assert_eq!(state.previous_summary, Some("Second".to_string()));
    assert_eq!(
        state.previous_files_changed,
        Some(vec!["b.rs".to_string()].into_boxed_slice())
    );
    assert_eq!(state.previous_next_steps, Some("Do more".to_string()));
}

#[test]
fn test_development_status_display() {
    assert_eq!(format!("{}", DevelopmentStatus::Completed), "completed");
    assert_eq!(format!("{}", DevelopmentStatus::Partial), "partial");
    assert_eq!(format!("{}", DevelopmentStatus::Failed), "failed");
}

#[test]
fn test_pipeline_state_initial_has_empty_continuation() {
    let state = PipelineState::initial(5, 2);
    assert!(!state.continuation.is_continuation());
    assert_eq!(state.continuation.continuation_attempt, 0);
}

#[test]
fn test_agent_chain_reset_clears_rate_limit_continuation_prompt() {
    let mut chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );
    chain.rate_limit_continuation_prompt = Some(RateLimitContinuationPrompt {
        drain: crate::agents::AgentDrain::Development,
        role: AgentRole::Developer,
        prompt: "saved".to_string(),
    });

    let reset = chain.reset();
    assert!(
        reset.rate_limit_continuation_prompt.is_none(),
        "reset() should clear rate_limit_continuation_prompt"
    );
}

#[test]
fn test_agent_chain_reset_for_role_clears_rate_limit_continuation_prompt() {
    let mut chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );
    chain.rate_limit_continuation_prompt = Some(RateLimitContinuationPrompt {
        drain: crate::agents::AgentDrain::Development,
        role: AgentRole::Developer,
        prompt: "saved".to_string(),
    });

    let reset = chain.reset_for_role(AgentRole::Reviewer);
    assert!(
        reset.rate_limit_continuation_prompt.is_none(),
        "reset_for_role() should clear rate_limit_continuation_prompt"
    );
}

#[test]
fn test_switch_to_next_agent_with_prompt_advances_retry_cycle_when_single_agent() {
    let chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );

    let next = chain.switch_to_next_agent_with_prompt(Some("prompt".to_string()));
    assert!(
        !next.is_exhausted(),
        "single-agent rate limit fallback should not immediately exhaust the chain"
    );
    assert_eq!(next.retry_cycle, 1);
}

#[test]
fn test_switch_to_next_agent_with_prompt_advances_retry_cycle_on_wraparound() {
    let mut chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );
    chain.current_agent_index = 1;

    let next = chain.switch_to_next_agent_with_prompt(Some("prompt".to_string()));
    assert!(
        !next.is_exhausted(),
        "rate limit fallback should not immediately exhaust on wraparound"
    );
    assert_eq!(next.retry_cycle, 1);
}

#[test]
fn test_agent_chain_tracks_explicit_drain_and_mode() {
    let chain = AgentChainState::initial()
        .with_agents(
            vec!["agent1".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_drain(crate::agents::AgentDrain::Development)
        .with_mode(crate::agents::DrainMode::Continuation);

    assert_eq!(chain.current_drain, crate::agents::AgentDrain::Development);
    assert_eq!(chain.current_mode, crate::agents::DrainMode::Continuation);
}

#[test]
fn test_agent_chain_serialization_round_trip_preserves_drain_and_mode() {
    let chain = AgentChainState::initial()
        .with_agents(
            vec!["agent1".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_drain(crate::agents::AgentDrain::Analysis)
        .with_mode(crate::agents::DrainMode::XsdRetry);

    let json = serde_json::to_string(&chain).expect("agent chain should serialize");
    let restored: AgentChainState =
        serde_json::from_str(&json).expect("agent chain should deserialize");

    assert_eq!(restored.current_drain, crate::agents::AgentDrain::Analysis);
    assert_eq!(restored.current_mode, crate::agents::DrainMode::XsdRetry);
}
