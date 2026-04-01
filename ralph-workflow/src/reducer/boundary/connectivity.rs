//! Connectivity checking via TCP probing.
//!
//! Provides network connectivity detection for the offline freeze-and-resume feature.
//! Uses TCP connections to known-good hosts to verify connectivity.

use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use crate::reducer::effect::EffectResult;
use crate::reducer::event::{AgentEvent, PipelineEvent};
use crate::reducer::state::ConnectivityState;
use crate::reducer::ui_event::UIEvent;

/// Probe targets: Cloudflare DNS (1.1.1.1:443) and Google DNS (8.8.8.8:53).
///
/// Using direct IPs avoids DNS dependency, making the check more fundamental.
/// The any() combinator means only ONE needs to succeed.
const PROBE_TARGETS: &[(&str, u16)] = &[("1.1.1.1", 443), ("8.8.8.8", 53)];

/// TCP connection timeout for each probe.
const PROBE_TIMEOUT: Duration = Duration::from_secs(5);

/// Probe network connectivity by attempting TCP connections to known-good hosts.
///
/// Returns `true` if at least one probe target is reachable, `false` otherwise.
/// This is a blocking I/O operation in the boundary layer (architecturally correct).
fn probe_connectivity() -> bool {
    PROBE_TARGETS.iter().any(|(host, port)| {
        let addr: SocketAddr = match format!("{host}:{port}").parse() {
            Ok(a) => a,
            Err(_) => return false,
        };
        TcpStream::connect_timeout(&addr, PROBE_TIMEOUT).is_ok()
    })
}

/// Determine UI message for connectivity check based on current state.
///
/// Pure policy function - determines what message to show based on connectivity state.
fn connectivity_check_message(is_offline: bool, check_pending: bool) -> String {
    if is_offline {
        "Still offline — verifying connectivity...".to_string()
    } else if check_pending {
        "Verifying network connectivity...".to_string()
    } else {
        "Checking network connectivity...".to_string()
    }
}

/// Determine UI message for offline polling based on current state.
///
/// Pure policy function - determines what message to show during polling.
fn offline_poll_message(is_offline: bool, poll_pending: bool) -> String {
    if is_offline && poll_pending {
        "Still offline — polling for connectivity...".to_string()
    } else if is_offline {
        "Offline detected — run paused. No continuation budget or retry budget is being consumed. Waiting for connectivity to return.".to_string()
    } else {
        "Polling for connectivity...".to_string()
    }
}

/// Handle the `CheckNetworkConnectivity` effect.
///
/// This is a one-time connectivity probe triggered immediately after a Network-class
/// agent failure. It probes connectivity and emits the appropriate event:
/// - If online: `AgentEvent::ConnectivityCheckSucceeded`
/// - If offline: `AgentEvent::ConnectivityCheckFailed`
///
/// The reducer processes these events to update ConnectivityState.
pub(super) fn check_network_connectivity(connectivity: &ConnectivityState) -> EffectResult {
    let message = connectivity_check_message(connectivity.is_offline, connectivity.check_pending);
    let ui_event = UIEvent::AgentActivity {
        agent: "connectivity".to_string(),
        message,
    };

    if probe_connectivity() {
        EffectResult::event(PipelineEvent::Agent(AgentEvent::ConnectivityCheckSucceeded))
            .with_ui_event(ui_event)
    } else {
        EffectResult::event(PipelineEvent::Agent(AgentEvent::ConnectivityCheckFailed))
            .with_ui_event(ui_event)
    }
}

/// Handle the `PollForConnectivity` effect.
///
/// This is emitted repeatedly while `is_offline=true`. Each execution:
/// 1. Sleeps for `interval_ms`
/// 2. Probes connectivity
/// 3. Emits the appropriate event:
///    - If still offline: `AgentEvent::ConnectivityCheckFailed`
///    - If back online: `AgentEvent::ConnectivityCheckSucceeded`
///
/// The orchestrator re-derives this effect each cycle while offline, providing
/// debounced polling without handler-side loops.
pub(super) fn poll_for_connectivity(
    interval_ms: u64,
    connectivity: &ConnectivityState,
) -> EffectResult {
    let message = offline_poll_message(connectivity.is_offline, connectivity.poll_pending);
    let ui_event = UIEvent::AgentActivity {
        agent: "connectivity".to_string(),
        message,
    };

    std::thread::sleep(Duration::from_millis(interval_ms));
    if probe_connectivity() {
        EffectResult::event(PipelineEvent::Agent(AgentEvent::ConnectivityCheckSucceeded))
            .with_ui_event(ui_event)
    } else {
        EffectResult::event(PipelineEvent::Agent(AgentEvent::ConnectivityCheckFailed))
            .with_ui_event(ui_event)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_probe_connectivity_does_not_panic() {
        // Verify the function executes without panicking (return type is bool)
        let _ = probe_connectivity();
    }

    #[test]
    fn test_check_network_connectivity_returns_effect_result() {
        let connectivity_state = ConnectivityState::default();
        let result = check_network_connectivity(&connectivity_state);
        // Should always return an event (not Option)
        let _ = result.event;
    }

    #[test]
    fn test_check_network_connectivity_emits_ui_event_when_online() {
        let connectivity_state = ConnectivityState::default();
        let result = check_network_connectivity(&connectivity_state);
        // Should have a UI event
        assert!(
            !result.ui_events.is_empty(),
            "Should emit UI event when checking connectivity"
        );
    }

    #[test]
    fn test_poll_for_connectivity_returns_effect_result() {
        // Use a very short interval for testing
        let connectivity_state = ConnectivityState {
            is_offline: true,
            poll_pending: true,
            ..ConnectivityState::default()
        };
        let result = poll_for_connectivity(1, &connectivity_state);
        // Should always return an event (not Option)
        let _ = result.event;
    }

    #[test]
    fn test_poll_for_connectivity_emits_ui_event() {
        let connectivity_state = ConnectivityState {
            is_offline: true,
            poll_pending: true,
            ..ConnectivityState::default()
        };
        let result = poll_for_connectivity(1, &connectivity_state);
        // Should have a UI event
        assert!(
            !result.ui_events.is_empty(),
            "Should emit UI event when polling for connectivity"
        );
    }
}
