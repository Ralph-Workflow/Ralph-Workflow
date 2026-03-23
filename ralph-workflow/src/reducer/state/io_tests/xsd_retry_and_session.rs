// =========================================================================
// XSD retry and session tracking tests
// =========================================================================

#[test]
fn test_artifact_type_display() {
    assert_eq!(format!("{}", ArtifactType::Plan), "plan");
    assert_eq!(
        format!("{}", ArtifactType::DevelopmentResult),
        "development_result"
    );
    assert_eq!(format!("{}", ArtifactType::Issues), "issues");
    assert_eq!(format!("{}", ArtifactType::FixResult), "fix_result");
    assert_eq!(format!("{}", ArtifactType::CommitMessage), "commit_message");
}

#[test]
fn test_continuation_state_with_limits() {
    let state = ContinuationState::with_limits(5, 2, 7);
    assert_eq!(state.max_xsd_retry_count, 5);
    assert_eq!(state.max_same_agent_retry_count, 7);
    assert_eq!(state.max_continue_count, 2);
    assert!(!state.is_continuation());
}

#[test]
fn test_continuation_state_default_limits() {
    let state = ContinuationState::new();
    assert_eq!(state.max_xsd_retry_count, 10);
    assert_eq!(state.max_same_agent_retry_count, 2);
    assert_eq!(state.max_continue_count, 3);
}

#[test]
fn test_continuation_reset_preserves_limits() {
    let state = ContinuationState::with_limits(5, 2, 7)
        .trigger_xsd_retry()
        .trigger_xsd_retry()
        .trigger_same_agent_retry(SameAgentRetryReason::Timeout);
    assert_eq!(state.xsd_retry_count, 2);
    assert_eq!(state.same_agent_retry_count, 1);

    let reset = state.reset();
    assert_eq!(reset.xsd_retry_count, 0);
    assert_eq!(reset.same_agent_retry_count, 0);
    assert_eq!(reset.max_xsd_retry_count, 5);
    assert_eq!(reset.max_same_agent_retry_count, 7);
    assert_eq!(reset.max_continue_count, 2);
}

#[test]
fn test_continuation_with_artifact() {
    let state = ContinuationState::new().with_artifact(ArtifactType::DevelopmentResult);
    assert_eq!(
        state.current_artifact,
        Some(ArtifactType::DevelopmentResult)
    );
    assert_eq!(state.xsd_retry_count, 0);
    assert!(!state.xsd_retry_pending);
}

#[test]
fn test_xsd_retry_trigger() {
    let state = ContinuationState::new()
        .with_artifact(ArtifactType::Plan)
        .trigger_xsd_retry();

    assert!(state.xsd_retry_pending);
    assert_eq!(state.xsd_retry_count, 1);
    assert!(
        state.xsd_retry_session_reuse_pending,
        "XSD retry should reuse the prior session when available"
    );
    assert_eq!(state.current_artifact, Some(ArtifactType::Plan));
}

#[test]
fn test_xsd_retry_clear_pending() {
    let state = ContinuationState::new()
        .trigger_xsd_retry()
        .clear_xsd_retry_pending();

    assert!(!state.xsd_retry_pending);
    assert_eq!(state.xsd_retry_count, 1);
}

#[test]
fn test_xsd_retries_exhausted() {
    let state = ContinuationState::with_limits(2, 3, 2);
    assert!(!state.xsd_retries_exhausted());

    let state = state.trigger_xsd_retry();
    assert!(!state.xsd_retries_exhausted());

    let state = state.trigger_xsd_retry();
    assert!(state.xsd_retries_exhausted());
}

#[test]
fn test_same_agent_retry_trigger_and_clear_pending() {
    let state = ContinuationState::new()
        .trigger_same_agent_retry(SameAgentRetryReason::Timeout)
        .clear_same_agent_retry_pending();

    assert!(!state.same_agent_retry_pending);
    assert_eq!(state.same_agent_retry_count, 1);
    assert!(state.same_agent_retry_reason.is_none());
}

#[test]
fn test_same_agent_retries_exhausted() {
    let state = ContinuationState::new().with_max_same_agent_retry(2);
    assert!(!state.same_agent_retries_exhausted());

    let state = state.trigger_same_agent_retry(SameAgentRetryReason::Timeout);
    assert!(!state.same_agent_retries_exhausted());

    let state = state.trigger_same_agent_retry(SameAgentRetryReason::InternalError);
    assert!(state.same_agent_retries_exhausted());
}

#[test]
fn test_continue_trigger() {
    let state = ContinuationState::new().trigger_continue();
    assert!(state.continue_pending);
}

#[test]
fn test_continue_clear_pending() {
    let state = ContinuationState::new()
        .trigger_continue()
        .clear_continue_pending();
    assert!(!state.continue_pending);
}

#[test]
fn test_continuations_exhausted() {
    let state = ContinuationState::with_limits(10, 2, 2);
    assert!(!state.continuations_exhausted());

    let state =
        state.trigger_continuation(DevelopmentStatus::Partial, "First".to_string(), None, None);
    assert!(!state.continuations_exhausted());
    assert!(state.continue_pending);

    // Second trigger_continuation hits the defensive check (next_attempt = 2 >= 2)
    // Counter stays at 1, continue_pending cleared
    let state =
        state.trigger_continuation(DevelopmentStatus::Partial, "Second".to_string(), None, None);
    assert_eq!(
        state.continuation_attempt, 1,
        "defensive check should prevent increment"
    );
    assert!(
        !state.continuations_exhausted(),
        "counter at 1, so 1 < 2 is false (not exhausted by counter check)"
    );
    assert!(
        !state.continue_pending,
        "defensive check must clear continue_pending"
    );
}

#[test]
fn test_continuations_exhausted_semantics() {
    // Test the documented semantics: max_continue_count=3 means 3 total attempts
    // Attempts 0, 1, 2 are allowed; attempt 3+ triggers exhaustion
    let state = ContinuationState::with_limits(10, 3, 2);
    assert_eq!(state.continuation_attempt, 0);
    assert!(
        !state.continuations_exhausted(),
        "attempt 0 should not be exhausted"
    );

    let state = state.trigger_continuation(DevelopmentStatus::Partial, "1".to_string(), None, None);
    assert_eq!(state.continuation_attempt, 1);
    assert!(
        !state.continuations_exhausted(),
        "attempt 1 should not be exhausted"
    );
    assert!(state.continue_pending);

    let state = state.trigger_continuation(DevelopmentStatus::Partial, "2".to_string(), None, None);
    assert_eq!(state.continuation_attempt, 2);
    assert!(
        !state.continuations_exhausted(),
        "attempt 2 should not be exhausted"
    );
    assert!(state.continue_pending);

    // Third trigger_continuation hits the defensive check (next_attempt = 3 >= 3)
    // Counter stays at 2, continue_pending cleared
    let state = state.trigger_continuation(DevelopmentStatus::Partial, "3".to_string(), None, None);
    assert_eq!(
        state.continuation_attempt, 2,
        "defensive check should prevent increment to 3"
    );
    assert!(
        !state.continuations_exhausted(),
        "counter at 2, so 2 < 3 is true (not exhausted by counter check)"
    );
    assert!(
        !state.continue_pending,
        "must not leave continue_pending=true once exhausted"
    );
}

#[test]
fn test_xsd_retries_exhausted_with_zero_max() {
    // max_xsd_retry_count=0 means XSD retries are disabled (immediate agent fallback)
    let state = ContinuationState::with_limits(10, 3, 2).with_max_xsd_retry(0);
    assert!(
        state.xsd_retries_exhausted(),
        "0 max retries should be immediately exhausted"
    );
}

#[test]
fn test_trigger_continuation_resets_xsd_retry() {
    let state = ContinuationState::new()
        .with_artifact(ArtifactType::DevelopmentResult)
        .trigger_xsd_retry()
        .trigger_xsd_retry()
        .trigger_continuation(
            DevelopmentStatus::Partial,
            "Work done".to_string(),
            None,
            None,
        );

    assert_eq!(state.xsd_retry_count, 0);
    assert!(!state.xsd_retry_pending);
    // continue_pending is now set to true by trigger_continuation to enable
    // orchestration to derive the continuation effect
    assert!(state.continue_pending);
    assert_eq!(
        state.current_artifact,
        Some(ArtifactType::DevelopmentResult)
    );
}

#[test]
fn test_agent_chain_session_id() {
    let chain = AgentChainState::initial()
        .with_agents(
            vec!["agent1".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        )
        .with_session_id(Some("session-123".to_string()));

    assert_eq!(chain.last_session_id, Some("session-123".to_string()));
}

#[test]
fn test_agent_chain_clear_session_id() {
    let chain = AgentChainState::initial()
        .with_session_id(Some("session-123".to_string()))
        .clear_session_id();

    assert!(chain.last_session_id.is_none());
}

#[test]
fn test_agent_chain_reset_clears_session_id() {
    let mut chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );
    chain.last_session_id = Some("session-123".to_string());

    let reset = chain.reset();
    assert!(
        reset.last_session_id.is_none(),
        "reset() should clear last_session_id"
    );
}

#[test]
fn test_agent_chain_reset_for_role_clears_session_id() {
    let mut chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );
    chain.last_session_id = Some("session-123".to_string());

    let reset = chain.reset_for_role(AgentRole::Reviewer);
    assert!(
        reset.last_session_id.is_none(),
        "reset_for_role() should clear last_session_id"
    );
}
