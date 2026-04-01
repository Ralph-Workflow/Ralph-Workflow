// Connectivity state and offline detection tests.
//
// Tests for the offline freeze-and-resume workflow:
// - Network error triggers connectivity check without consuming budget
// - Connectivity state transitions (online -> offline -> online)
// - Orchestrator priority blocking during offline windows
// - Budget preservation during offline periods
// - connectivity_interruptions_total metric tracking

use super::*;

// =============================================================================
// InvocationFailed(Network) Tests
// =============================================================================

#[test]
fn test_network_failure_sets_check_pending_not_retry() {
    // Given: state with no connectivity issues
    let state = create_test_state();

    // When: a network error occurs
    let event = PipelineEvent::Agent(AgentEvent::InvocationFailed {
        role: AgentRole::Developer,
        agent: AgentName::from("agent1"),
        exit_code: 1,
        error_kind: AgentErrorKind::Network,
        retriable: true,
    });
    let new_state = reduce(state.clone(), event);

    // Then: check_pending is set but no retry state is consumed
    assert!(
        new_state.connectivity.check_pending,
        "Network failure should set check_pending"
    );
    assert!(
        !new_state.connectivity.is_offline,
        "Should not be offline after just setting check_pending"
    );
    // Retry state should be preserved (not reset to 0)
    assert_eq!(
        new_state.continuation.same_agent_retry_count, state.continuation.same_agent_retry_count,
        "same_agent_retry_count should be preserved"
    );
    assert_eq!(
        new_state.continuation.xsd_retry_count, state.continuation.xsd_retry_count,
        "xsd_retry_count should be preserved"
    );
}

#[test]
fn test_offline_detection_freezes_retry_state() {
    // Given: state with xsd_retry_pending=true and some retry counts
    let state = PipelineState {
        continuation: ContinuationState {
            xsd_retry_count: 3,
            xsd_retry_pending: true,
            same_agent_retry_count: 2,
            same_agent_retry_pending: true,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    // When: network error sets check_pending
    let event = PipelineEvent::Agent(AgentEvent::InvocationFailed {
        role: AgentRole::Developer,
        agent: AgentName::from("agent1"),
        exit_code: 1,
        error_kind: AgentErrorKind::Network,
        retriable: true,
    });
    let new_state = reduce(state.clone(), event);

    // Then: retry state is preserved
    assert_eq!(
        new_state.continuation.xsd_retry_count, 3,
        "xsd_retry_count should be preserved"
    );
    assert!(
        new_state.continuation.xsd_retry_pending,
        "xsd_retry_pending should be preserved"
    );
    assert_eq!(
        new_state.continuation.same_agent_retry_count, 2,
        "same_agent_retry_count should be preserved"
    );
    assert!(
        new_state.continuation.same_agent_retry_pending,
        "same_agent_retry_pending should be preserved"
    );
}

#[test]
fn test_non_network_failure_still_consumes_budget() {
    // Given: state with no retry issues
    let state = create_test_state();

    // When: a non-network non-retriable error occurs (like InternalError)
    // InternalError is handled in the retriable=false branch
    let event = PipelineEvent::Agent(AgentEvent::InvocationFailed {
        role: AgentRole::Developer,
        agent: AgentName::from("agent1"),
        exit_code: 1,
        error_kind: AgentErrorKind::InternalError,
        retriable: false, // InternalError is non-retriable
    });
    let new_state = reduce(state.clone(), event);

    // Then: retry budget is consumed (existing behavior preserved)
    assert!(
        new_state.continuation.same_agent_retry_pending,
        "Internal error should trigger same-agent retry"
    );
    assert_eq!(
        new_state.continuation.same_agent_retry_count, 1,
        "same_agent_retry_count should increment"
    );
    // Connectivity state should not be affected
    assert!(
        !new_state.connectivity.check_pending,
        "Non-network error should not set check_pending"
    );
}

// =============================================================================
// Connectivity Probe Result Tests
// =============================================================================

#[test]
fn test_connectivity_check_succeeded_while_online() {
    // Given: state with check_pending=true (we were verifying connectivity)
    let state = PipelineState {
        connectivity: ConnectivityState {
            check_pending: true,
            ..ConnectivityState::default()
        },
        ..create_test_state()
    };

    // When: connectivity check succeeds
    let event = PipelineEvent::Agent(AgentEvent::ConnectivityCheckSucceeded);
    let new_state = reduce(state.clone(), event);

    // Then: check_pending is cleared
    assert!(
        !new_state.connectivity.check_pending,
        "Successful check should clear check_pending"
    );
    assert!(
        !new_state.connectivity.is_offline,
        "Should remain online after successful probe"
    );
    assert_eq!(
        new_state.connectivity.consecutive_failures, 0,
        "Failure count should reset"
    );
}

#[test]
fn test_connectivity_check_failed_below_threshold() {
    // Given: state with check_pending=true
    let state = PipelineState {
        connectivity: ConnectivityState {
            check_pending: true,
            consecutive_failures: 0,
            ..ConnectivityState::default()
        },
        ..create_test_state()
    };

    // When: connectivity check fails (first time, threshold is 2)
    let event = PipelineEvent::Agent(AgentEvent::ConnectivityCheckFailed);
    let new_state = reduce(state.clone(), event);

    // Then: failure count increments, still checking (not offline yet)
    assert_eq!(
        new_state.connectivity.consecutive_failures, 1,
        "Failure count should increment"
    );
    assert!(
        new_state.connectivity.check_pending,
        "Should still be checking (threshold not reached)"
    );
    assert!(
        !new_state.connectivity.is_offline,
        "Should not be offline yet (threshold=2)"
    );
}

#[test]
fn test_connectivity_check_failed_at_threshold_enters_offline() {
    // Given: state with check_pending=true and already 1 failure
    let state = PipelineState {
        connectivity: ConnectivityState {
            check_pending: true,
            consecutive_failures: 1,
            ..ConnectivityState::default()
        },
        ..create_test_state()
    };

    // When: connectivity check fails again (reaches threshold of 2)
    let event = PipelineEvent::Agent(AgentEvent::ConnectivityCheckFailed);
    let new_state = reduce(state.clone(), event);

    // Then: should enter offline mode
    assert!(
        new_state.connectivity.is_offline,
        "Should be offline after reaching failure threshold"
    );
    assert!(
        new_state.connectivity.poll_pending,
        "Should have poll_pending when offline"
    );
    assert!(
        !new_state.connectivity.check_pending,
        "check_pending should be cleared when entering offline"
    );
}

#[test]
fn test_back_online_resumes_without_budget_consumption() {
    // Given: state that was offline with preserved retry counts
    let state = PipelineState {
        connectivity: ConnectivityState {
            is_offline: true,
            poll_pending: true,
            consecutive_failures: 2,
            ..ConnectivityState::default()
        },
        continuation: ContinuationState {
            xsd_retry_count: 3,
            same_agent_retry_count: 2,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    // When: connectivity check succeeds (back online)
    let event = PipelineEvent::Agent(AgentEvent::ConnectivityCheckSucceeded);
    let new_state = reduce(state.clone(), event);

    // Then: back online, budget preserved
    assert!(!new_state.connectivity.is_offline, "Should be back online");
    assert!(
        !new_state.connectivity.poll_pending,
        "poll_pending should be cleared"
    );
    assert!(
        !new_state.connectivity.check_pending,
        "check_pending should be cleared"
    );
    // Budget should be exactly as preserved
    assert_eq!(
        new_state.continuation.xsd_retry_count, 3,
        "xsd_retry_count should be preserved through offline window"
    );
    assert_eq!(
        new_state.continuation.same_agent_retry_count, 2,
        "same_agent_retry_count should be preserved through offline window"
    );
}

// =============================================================================
// Orchestrator Priority Tests
// =============================================================================

#[test]
fn test_offline_state_check_pending_blocks_retry_in_orchestrator() {
    // Given: state with xsd_retry_pending=true AND check_pending=true
    let state = PipelineState {
        connectivity: ConnectivityState {
            check_pending: true,
            ..ConnectivityState::default()
        },
        continuation: ContinuationState {
            xsd_retry_pending: true,
            xsd_retry_count: 3,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    // When: determining next effect
    let effect = determine_next_effect(&state);

    // Then: connectivity check takes priority over XSD retry
    assert!(
        matches!(effect, Effect::CheckNetworkConnectivity),
        "check_pending should block xsd_retry_pending: got {:?}",
        effect
    );
}

#[test]
fn test_offline_state_poll_pending_blocks_continuation_in_orchestrator() {
    // Given: state with continue_pending=true AND is_offline=true
    let state = PipelineState {
        connectivity: ConnectivityState {
            is_offline: true,
            poll_pending: true,
            offline_poll_interval_ms: 5000,
            ..ConnectivityState::default()
        },
        ..create_test_state()
    };

    // When: determining next effect
    let effect = determine_next_effect(&state);

    // Then: offline polling takes priority over continuation
    assert!(
        matches!(effect, Effect::PollForConnectivity { .. }),
        "poll_pending should block continuation: got {:?}",
        effect
    );
}

#[test]
fn test_back_online_allows_xsd_retry_to_proceed() {
    // Given: state with xsd_retry_pending=true and connectivity restored
    // create_test_state() starts in Planning phase
    let state = PipelineState {
        connectivity: ConnectivityState::default(), // Online, no pending checks
        continuation: ContinuationState {
            xsd_retry_pending: true,
            xsd_retry_count: 3,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    // When: determining next effect
    let effect = determine_next_effect(&state);

    // Then: XSD retry effect is derived (PreparePlanningPrompt with XsdRetry mode for Planning phase)
    assert!(
        matches!(
            effect,
            Effect::PreparePlanningPrompt {
                prompt_mode: crate::reducer::PromptMode::XsdRetry,
                ..
            }
        ),
        "After back online, xsd_retry should proceed: got {:?}",
        effect
    );
}

#[test]
fn test_orchestrator_connectivity_priority_before_same_agent_retry() {
    // Given: state with same_agent_retry_pending=true AND check_pending=true
    let state = PipelineState {
        connectivity: ConnectivityState {
            check_pending: true,
            ..ConnectivityState::default()
        },
        continuation: ContinuationState {
            same_agent_retry_pending: true,
            same_agent_retry_count: 1,
            ..ContinuationState::new()
        },
        ..create_test_state()
    };

    // When: determining next effect
    let effect = determine_next_effect(&state);

    // Then: connectivity check takes priority over same-agent retry
    assert!(
        matches!(effect, Effect::CheckNetworkConnectivity),
        "check_pending should block same_agent_retry_pending: got {:?}",
        effect
    );
}

// =============================================================================
// Debounce Behavior Tests
// =============================================================================

#[test]
fn test_debounce_prevents_rapid_offline_online_thrashing() {
    // Given: online state with check_pending
    let mut state = PipelineState {
        connectivity: ConnectivityState {
            check_pending: true,
            ..ConnectivityState::default()
        },
        ..create_test_state()
    };

    // Simulate: probe fails, succeeds, fails, succeeds (alternating)
    // Should NOT enter offline mode due to debounce

    // Fail 1: below threshold
    state = reduce(
        state,
        PipelineEvent::Agent(AgentEvent::ConnectivityCheckFailed),
    );
    assert!(
        !state.connectivity.is_offline,
        "Should not be offline after 1 failure"
    );

    // Success 1: resets debounce
    state = reduce(
        state,
        PipelineEvent::Agent(AgentEvent::ConnectivityCheckSucceeded),
    );
    assert!(
        !state.connectivity.is_offline,
        "Should still be online after success"
    );
    assert_eq!(
        state.connectivity.consecutive_failures, 0,
        "Failure count should reset on success"
    );

    // Fail 2: still below threshold (threshold is 2)
    state = reduce(
        state,
        PipelineEvent::Agent(AgentEvent::ConnectivityCheckFailed),
    );
    assert!(
        !state.connectivity.is_offline,
        "Should not be offline after 2 non-consecutive failures"
    );

    // Success 2: still online
    state = reduce(
        state,
        PipelineEvent::Agent(AgentEvent::ConnectivityCheckSucceeded),
    );
    assert!(!state.connectivity.is_offline, "Should remain online");
}

#[test]
fn test_single_success_exits_offline_mode() {
    // Given: offline state with poll_pending
    let state = PipelineState {
        connectivity: ConnectivityState {
            is_offline: true,
            poll_pending: true,
            consecutive_failures: 2,
            required_successes_to_go_online: 1, // Default is 1
            ..ConnectivityState::default()
        },
        ..create_test_state()
    };

    // When: single successful probe
    let event = PipelineEvent::Agent(AgentEvent::ConnectivityCheckSucceeded);
    let new_state = reduce(state, event);

    // Then: immediately back online (threshold is 1)
    assert!(
        !new_state.connectivity.is_offline,
        "Should be back online after 1 success"
    );
    assert!(
        !new_state.connectivity.poll_pending,
        "poll_pending should be cleared"
    );
}

// =============================================================================
// connectivity_interruptions_total Metric Tests
// =============================================================================

#[test]
fn test_connectivity_interruption_metric_increments_on_offline_entry() {
    // Given: fully online state with 1 prior failed probe (check_pending, not yet offline)
    let state = PipelineState {
        connectivity: ConnectivityState {
            check_pending: true,
            consecutive_failures: 1,
            required_failures_to_go_offline: 2,
            ..ConnectivityState::default()
        },
        ..create_test_state()
    };

    // When: second probe fails — crossing the threshold into offline mode
    let new_state = reduce(
        state,
        PipelineEvent::Agent(AgentEvent::ConnectivityCheckFailed),
    );

    assert!(new_state.connectivity.is_offline, "Should be offline now");
    assert_eq!(
        new_state.metrics.connectivity_interruptions_total, 1,
        "Should record exactly one connectivity interruption on offline entry"
    );
}

#[test]
fn test_connectivity_interruption_metric_does_not_increment_during_polling() {
    // Given: already-offline state (is_offline=true)
    let state = PipelineState {
        connectivity: ConnectivityState {
            is_offline: true,
            poll_pending: true,
            consecutive_failures: 2,
            ..ConnectivityState::default()
        },
        metrics: RunMetrics {
            connectivity_interruptions_total: 1,
            ..RunMetrics::default()
        },
        ..create_test_state()
    };

    // When: poll fails again (still offline)
    let new_state = reduce(
        state,
        PipelineEvent::Agent(AgentEvent::ConnectivityCheckFailed),
    );

    assert_eq!(
        new_state.metrics.connectivity_interruptions_total, 1,
        "Should NOT increment again while already offline"
    );
}
