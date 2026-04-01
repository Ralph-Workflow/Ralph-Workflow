//! Integration tests for offline detection with freeze-and-resume workflow.
//!
//! Tests the full lifecycle of the offline detection feature:
//! - Network error triggers connectivity check without consuming budget
//! - Probe debouncing prevents rapid offline/online thrashing
//! - Budget preservation during offline windows
//! - Automatic resume when connectivity returns
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{AgentErrorKind, PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::{ConnectivityState, ContinuationState, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;

/// Test that InvocationFailed(Network) sets check_pending without consuming budget.
///
/// When a network error occurs, the reducer should:
/// 1. Set connectivity.check_pending = true
/// 2. NOT advance models or reset retry counters
/// 3. NOT change agent chain state
#[test]
fn test_network_failure_sets_check_pending_preserves_budget() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: ralph_workflow::reducer::state::AgentChainState::initial().with_agents(
                vec!["agent1".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            ),
            continuation: ContinuationState {
                same_agent_retry_count: 1,
                same_agent_retry_pending: true,
                xsd_retry_count: 2,
                xsd_retry_pending: true,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Simulate network failure
        let new_state = reduce(
            state.clone(),
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::InvocationFailed {
                    role: AgentRole::Developer,
                    agent: "agent1".into(),
                    exit_code: 1,
                    error_kind: AgentErrorKind::Network,
                    retriable: true,
                },
            ),
        );

        // check_pending should be set
        assert!(
            new_state.connectivity.check_pending,
            "Network failure should set check_pending"
        );

        // Budget should be preserved
        assert_eq!(
            new_state.continuation.same_agent_retry_count, 1,
            "same_agent_retry_count should be preserved"
        );
        assert_eq!(
            new_state.continuation.xsd_retry_count, 2,
            "xsd_retry_count should be preserved"
        );

        // Agent chain should be unchanged
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            "agent1",
            "Agent should not change on network failure"
        );
    });
}

/// Test that check_pending blocks XSD retry in orchestration priority.
///
/// While connectivity is being verified, no budget-consuming effects should run.
#[test]
fn test_check_pending_blocks_xsd_retry_in_orchestrator() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            connectivity: ConnectivityState {
                check_pending: true,
                ..ConnectivityState::default()
            },
            continuation: ContinuationState {
                xsd_retry_pending: true,
                xsd_retry_count: 3,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        // Connectivity check should take priority
        assert!(
            matches!(effect, Effect::CheckNetworkConnectivity),
            "check_pending should block xsd_retry_pending, got {:?}",
            effect
        );
    });
}

/// Test that check_pending blocks same-agent retry in orchestration priority.
#[test]
fn test_check_pending_blocks_same_agent_retry_in_orchestrator() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                check_pending: true,
                ..ConnectivityState::default()
            },
            continuation: ContinuationState {
                same_agent_retry_pending: true,
                same_agent_retry_count: 1,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        assert!(
            matches!(effect, Effect::CheckNetworkConnectivity),
            "check_pending should block same_agent_retry_pending, got {:?}",
            effect
        );
    });
}

/// Test that entering offline mode preserves retry budget.
///
/// When connectivity probe threshold is reached and we enter offline mode,
/// all continuation/retry state should be preserved.
#[test]
fn test_offline_mode_preserves_retry_budget() {
    with_default_timeout(|| {
        // Start with check_pending to simulate verification in progress
        let state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                check_pending: true,
                consecutive_failures: 1,
                required_failures_to_go_offline: 2,
                ..ConnectivityState::default()
            },
            continuation: ContinuationState {
                same_agent_retry_count: 2,
                same_agent_retry_pending: true,
                xsd_retry_count: 3,
                xsd_retry_pending: true,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Second probe failure enters offline mode
        let new_state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );

        // Should be offline now
        assert!(
            new_state.connectivity.is_offline,
            "Should be offline after reaching failure threshold"
        );

        // Budget should be preserved
        assert_eq!(
            new_state.continuation.same_agent_retry_count, 2,
            "same_agent_retry_count should be preserved through offline transition"
        );
        assert_eq!(
            new_state.continuation.xsd_retry_count, 3,
            "xsd_retry_count should be preserved through offline transition"
        );
    });
}

/// Test that is_offline + poll_pending blocks continuation in orchestration.
///
/// While offline, only connectivity polling should occur.
#[test]
fn test_offline_blocks_continuation_in_orchestrator() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                is_offline: true,
                poll_pending: true,
                offline_poll_interval_ms: 5000,
                ..ConnectivityState::default()
            },
            continuation: ContinuationState {
                xsd_retry_pending: true,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        assert!(
            matches!(effect, Effect::PollForConnectivity { .. }),
            "poll_pending should block continuation, got {:?}",
            effect
        );
    });
}

/// Test that successful probe while offline transitions back online.
///
/// When connectivity is restored while in offline mode, the pipeline
/// should exit offline mode and clear poll_pending.
#[test]
fn test_successful_probe_exits_offline_mode() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                is_offline: true,
                poll_pending: true,
                consecutive_failures: 2,
                consecutive_successes: 0,
                required_failures_to_go_offline: 2,
                required_successes_to_go_online: 1,
                ..ConnectivityState::default()
            },
            continuation: ContinuationState {
                xsd_retry_count: 3,
                same_agent_retry_count: 2,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let new_state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckSucceeded,
            ),
        );

        // Should be back online
        assert!(
            !new_state.connectivity.is_offline,
            "Should be back online after successful probe"
        );
        assert!(
            !new_state.connectivity.poll_pending,
            "poll_pending should be cleared"
        );

        // Budget should remain preserved
        assert_eq!(
            new_state.continuation.xsd_retry_count, 3,
            "xsd_retry_count should remain preserved after going back online"
        );
        assert_eq!(
            new_state.continuation.same_agent_retry_count, 2,
            "same_agent_retry_count should remain preserved after going back online"
        );
    });
}

/// Test that back-online allows XSD retry to proceed in orchestration.
#[test]
fn test_back_online_allows_xsd_retry_to_proceed() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            connectivity: ConnectivityState::default(), // Online, no pending checks
            continuation: ContinuationState {
                xsd_retry_pending: true,
                xsd_retry_count: 3,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                effect,
                Effect::PreparePlanningPrompt {
                    prompt_mode: ralph_workflow::reducer::PromptMode::XsdRetry,
                    ..
                }
            ),
            "After back online, xsd_retry should proceed, got {:?}",
            effect
        );
    });
}

/// Test debounce: alternating failures and successes don't cause thrashing.
///
/// Rapid oscillation between success and failure should NOT enter offline mode
/// due to the debounce counters being reset on each success.
#[test]
fn test_debounce_prevents_offline_online_thrashing() {
    with_default_timeout(|| {
        // Start with check_pending (simulating verification after a network error)
        let mut state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                check_pending: true,
                ..ConnectivityState::default()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Fail 1: below threshold (threshold = 2)
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert!(
            !state.connectivity.is_offline,
            "Should not be offline after 1 failure"
        );

        // Success 1: resets failure count
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckSucceeded,
            ),
        );
        assert!(
            !state.connectivity.is_offline,
            "Should be online after success"
        );
        assert_eq!(
            state.connectivity.consecutive_failures, 0,
            "Failure count should reset on success"
        );

        // Fail 2: still below threshold (0 failures + 1 = 1, threshold = 2)
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert!(
            !state.connectivity.is_offline,
            "Should not be offline after 2 non-consecutive failures"
        );

        // Success 2: still no thrashing
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckSucceeded,
            ),
        );
        assert!(
            !state.connectivity.is_offline,
            "Should remain online despite alternating failures/successes"
        );
    });
}

/// Test that successful probe while online (not offline) clears check_pending.
#[test]
fn test_successful_probe_clears_check_pending_while_online() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                check_pending: true,
                ..ConnectivityState::default()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let new_state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckSucceeded,
            ),
        );

        assert!(
            !new_state.connectivity.check_pending,
            "Successful probe should clear check_pending"
        );
        assert!(!new_state.connectivity.is_offline, "Should remain online");
        assert_eq!(
            new_state.connectivity.consecutive_failures, 0,
            "Failure count should reset"
        );
    });
}

/// Test that first probe failure increments counter but stays in checking state.
#[test]
fn test_first_probe_failure_increments_counter_stays_checking() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                check_pending: true,
                consecutive_failures: 0,
                ..ConnectivityState::default()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let new_state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );

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
    });
}

/// Test that InternalError (non-network) still consumes retry budget.
///
/// This verifies the existing behavior is preserved: non-network errors
/// go through the normal retry flow.
#[test]
fn test_internal_error_consumes_retry_budget() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: ralph_workflow::reducer::state::AgentChainState::initial().with_agents(
                vec!["agent1".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            ),
            continuation: ContinuationState {
                same_agent_retry_count: 0,
                same_agent_retry_pending: false,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        let new_state = reduce(
            state.clone(),
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::InvocationFailed {
                    role: AgentRole::Developer,
                    agent: "agent1".into(),
                    exit_code: 1,
                    error_kind: AgentErrorKind::InternalError,
                    retriable: false,
                },
            ),
        );

        // InternalError should trigger same-agent retry (consuming budget)
        assert!(
            new_state.continuation.same_agent_retry_pending,
            "Internal error should trigger same-agent retry"
        );
        assert_eq!(
            new_state.continuation.same_agent_retry_count, 1,
            "same_agent_retry_count should increment"
        );

        // Connectivity state should NOT be affected
        assert!(
            !new_state.connectivity.check_pending,
            "Non-network error should not set check_pending"
        );
    });
}

/// Test full offline lifecycle: online -> network error -> check_pending ->
/// probe fails x2 -> offline -> probe succeeds -> back online -> XSD retry proceeds.
#[test]
fn test_full_offline_lifecycle_preserves_budget() {
    with_default_timeout(|| {
        // Start online, in Planning phase with XSD retry pending
        let mut state = PipelineState {
            phase: PipelinePhase::Planning,
            connectivity: ConnectivityState::default(),
            continuation: ContinuationState {
                xsd_retry_pending: true,
                xsd_retry_count: 3,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // 1. Network error sets check_pending
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::InvocationFailed {
                    role: AgentRole::Developer,
                    agent: "agent1".into(),
                    exit_code: 1,
                    error_kind: AgentErrorKind::Network,
                    retriable: true,
                },
            ),
        );
        assert!(
            state.connectivity.check_pending,
            "Step 1: check_pending should be set"
        );
        assert_eq!(
            state.continuation.xsd_retry_count, 3,
            "Step 1: xsd_retry_count preserved"
        );

        // 2. First probe fails - still checking
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert!(
            !state.connectivity.is_offline,
            "Step 2: Still checking (1 failure, threshold=2)"
        );
        assert_eq!(
            state.continuation.xsd_retry_count, 3,
            "Step 2: xsd_retry_count still preserved"
        );

        // 3. Second probe fails - enter offline mode
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert!(
            state.connectivity.is_offline,
            "Step 3: Now offline (2 failures)"
        );
        assert!(
            state.connectivity.poll_pending,
            "Step 3: poll_pending is set"
        );
        assert_eq!(
            state.continuation.xsd_retry_count, 3,
            "Step 3: xsd_retry_count still preserved"
        );

        // 4. Probe succeeds - back online, budget preserved
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckSucceeded,
            ),
        );
        assert!(!state.connectivity.is_offline, "Step 4: Back online");
        assert!(
            !state.connectivity.poll_pending,
            "Step 4: poll_pending cleared"
        );
        assert_eq!(
            state.continuation.xsd_retry_count, 3,
            "Step 4: xsd_retry_count preserved through entire offline window"
        );

        // 5. Orchestrator allows XSD retry to proceed
        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::PreparePlanningPrompt {
                    prompt_mode: ralph_workflow::reducer::PromptMode::XsdRetry,
                    ..
                }
            ),
            "Step 5: XSD retry should proceed after back online, got {:?}",
            effect
        );
    });
}

/// Test that user stop (Ctrl+C) takes precedence over offline polling.
///
/// When the pipeline is offline (poll_pending=true) and the user stops the run,
/// the Interrupted phase should take precedence over PollForConnectivity.
#[test]
fn test_user_stop_while_offline_takes_precedence() {
    with_default_timeout(|| {
        // State: offline and polling, but user has stopped the run
        let state = PipelineState {
            phase: PipelinePhase::Interrupted,
            connectivity: ConnectivityState {
                is_offline: true,
                poll_pending: true,
                consecutive_failures: 2,
                ..ConnectivityState::default()
            },
            // interrupted_by_user is set when user presses Ctrl+C
            // This flag skips pre-termination commit safety check
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Determine next effect
        let effect = determine_next_effect(&state);

        // User stop (Interrupted phase) should take precedence over offline polling
        // The orchestrator should NOT return PollForConnectivity when phase is Interrupted
        assert!(
            !matches!(effect, Effect::PollForConnectivity { .. }),
            "Interrupted phase should take precedence over offline polling, got {:?}",
            effect
        );
    });
}

/// Test that continuation_attempt is not consumed during offline windows.
///
/// While the pipeline is offline, no continuation attempts should be counted.
/// This ensures connectivity issues are not counted as agent failures.
#[test]
fn test_continuation_attempt_unchanged_across_offline_window() {
    with_default_timeout(|| {
        // Start online with continuation_attempt=1
        let mut state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState::default(),
            continuation: ContinuationState {
                continuation_attempt: 1,
                continue_pending: true,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // 1. Network error sets check_pending
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::InvocationFailed {
                    role: AgentRole::Developer,
                    agent: "agent1".into(),
                    exit_code: 1,
                    error_kind: AgentErrorKind::Network,
                    retriable: true,
                },
            ),
        );
        assert_eq!(
            state.continuation.continuation_attempt, 1,
            "Step 1: continuation_attempt should be preserved"
        );

        // 2. First probe fails - still checking
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert_eq!(
            state.continuation.continuation_attempt, 1,
            "Step 2: continuation_attempt should be preserved"
        );

        // 3. Second probe fails - enter offline mode
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert!(state.connectivity.is_offline, "Step 3: Should be offline");
        assert_eq!(
            state.continuation.continuation_attempt, 1,
            "Step 3: continuation_attempt should be preserved through offline transition"
        );

        // 4. While offline, simulate a few poll cycles (state doesn't change during poll)
        for _ in 0..3 {
            let effect = determine_next_effect(&state);
            assert!(
                matches!(effect, Effect::PollForConnectivity { .. }),
                "Should be polling while offline"
            );
            // The poll doesn't change continuation_attempt
        }
        assert_eq!(
            state.continuation.continuation_attempt, 1,
            "After polling: continuation_attempt should still be 1"
        );

        // 5. Probe succeeds - back online
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckSucceeded,
            ),
        );
        assert!(
            !state.connectivity.is_offline,
            "Step 5: Should be back online"
        );
        assert_eq!(
            state.continuation.continuation_attempt, 1,
            "Step 5: continuation_attempt should be preserved through entire offline window"
        );
    });
}

/// Test that connectivity_interruptions_total increments on first offline entry.
#[test]
fn test_connectivity_interruptions_metric_increments_on_offline_entry() {
    with_default_timeout(|| {
        // Start with check_pending and 0 failures (threshold=2)
        let mut state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                check_pending: true,
                consecutive_failures: 0,
                required_failures_to_go_offline: 2,
                ..ConnectivityState::default()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Initially no interruptions
        assert_eq!(
            state.metrics.connectivity_interruptions_total, 0,
            "Should start with 0 interruptions"
        );

        // First probe failure - still online (1 failure, threshold=2)
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert!(
            !state.connectivity.is_offline,
            "Should not be offline after 1 failure"
        );
        assert_eq!(
            state.metrics.connectivity_interruptions_total, 0,
            "Should still be 0 interruptions"
        );

        // Second probe failure - now offline, should increment metric
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert!(
            state.connectivity.is_offline,
            "Should be offline after 2 failures"
        );
        assert_eq!(
            state.metrics.connectivity_interruptions_total, 1,
            "Should increment to 1 on offline entry"
        );
    });
}

/// Test that connectivity_interruptions_total increments again on second offline window.
#[test]
fn test_second_offline_window_increments_metric_again() {
    with_default_timeout(|| {
        // Start offline with 1 prior interruption
        let mut state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                is_offline: true,
                poll_pending: true,
                consecutive_failures: 2,
                ..ConnectivityState::default()
            },
            metrics: ralph_workflow::reducer::state::RunMetrics {
                connectivity_interruptions_total: 1,
                ..ralph_workflow::reducer::state::RunMetrics::default()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Go back online
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckSucceeded,
            ),
        );
        assert!(!state.connectivity.is_offline, "Should be back online");

        // Network error again - set check_pending
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::InvocationFailed {
                    role: AgentRole::Developer,
                    agent: "agent1".into(),
                    exit_code: 1,
                    error_kind: AgentErrorKind::Network,
                    retriable: true,
                },
            ),
        );
        assert!(
            state.connectivity.check_pending,
            "Should set check_pending on network error"
        );

        // First probe failure
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );

        // Second probe failure - second offline entry
        state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert_eq!(
            state.metrics.connectivity_interruptions_total, 2,
            "Should increment to 2 on second offline entry"
        );
    });
}

/// Test that process starting offline does not consume budget and enters offline state.
#[test]
fn test_process_starts_offline_enters_offline_immediately() {
    with_default_timeout(|| {
        // Simulate: first agent invocation fails with Network error as if started with no internet
        let state = PipelineState {
            phase: PipelinePhase::Development,
            continuation: ContinuationState {
                same_agent_retry_count: 1,
                same_agent_retry_pending: true,
                xsd_retry_count: 2,
                xsd_retry_pending: true,
                ..ContinuationState::new()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Simulate: Network error on first invocation (as if started offline)
        let state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::InvocationFailed {
                    role: AgentRole::Developer,
                    agent: "agent1".into(),
                    exit_code: 1,
                    error_kind: AgentErrorKind::Network,
                    retriable: true,
                },
            ),
        );

        // Should set check_pending but not consume retry budget
        assert!(
            state.connectivity.check_pending,
            "Network error should set check_pending"
        );
        assert_eq!(
            state.continuation.same_agent_retry_count, 1,
            "same_agent_retry_count should be preserved"
        );
        assert_eq!(
            state.continuation.xsd_retry_count, 2,
            "xsd_retry_count should be preserved"
        );

        // Now simulate 2 probe failures to enter offline mode
        let state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert!(
            !state.connectivity.is_offline,
            "Should not be offline after 1 failure"
        );

        let state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert!(
            state.connectivity.is_offline,
            "Should be offline after 2 failures"
        );
        assert_eq!(
            state.metrics.connectivity_interruptions_total, 1,
            "Should record 1 connectivity interruption"
        );

        // Budget still preserved
        assert_eq!(
            state.continuation.same_agent_retry_count, 1,
            "same_agent_retry_count should still be preserved"
        );
        assert_eq!(
            state.continuation.xsd_retry_count, 2,
            "xsd_retry_count should still be preserved"
        );
    });
}

/// Test that metric does not increment during polling (subsequent probe failures while offline).
#[test]
fn test_metric_does_not_increment_during_offline_polling() {
    with_default_timeout(|| {
        // Start offline with 1 prior interruption
        let state = PipelineState {
            phase: PipelinePhase::Development,
            connectivity: ConnectivityState {
                is_offline: true,
                poll_pending: true,
                consecutive_failures: 2,
                ..ConnectivityState::default()
            },
            metrics: ralph_workflow::reducer::state::RunMetrics {
                connectivity_interruptions_total: 1,
                ..ralph_workflow::reducer::state::RunMetrics::default()
            },
            ..with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        // Additional probe failures while already offline should NOT increment
        let state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert_eq!(
            state.metrics.connectivity_interruptions_total, 1,
            "Should NOT increment during polling"
        );

        let state = reduce(
            state,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::ConnectivityCheckFailed,
            ),
        );
        assert_eq!(
            state.metrics.connectivity_interruptions_total, 1,
            "Should still be 1 after multiple poll failures"
        );
    });
}
