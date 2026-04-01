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

/// Determine UI message for resume confirmation when connectivity is restored.
///
/// Pure policy function - returns the message shown when the pipeline resumes
/// from an offline state.
fn resume_confirmation_message() -> String {
    "Connectivity restored — resuming workflow. No continuation budget or retry budget was consumed during the offline window.".to_string()
}

/// Message type enumeration for poll UI messages.
///
/// Pure policy enum - categorizes the type of poll message to emit.
#[derive(Clone, Copy)]
enum PollUiMessageType {
    /// Emit offline polling message only (still offline).
    OfflinePoll,
    /// Emit connectivity check message only (not offline).
    ConnectivityCheck,
    /// Emit resume confirmation after offline polling context (restoring from offline).
    ResumeConfirmation,
}

/// Determine which type of poll UI message to emit.
///
/// Pure policy function - makes the branching decision about which message type
/// to emit based on connectivity state and probe result.
fn determine_poll_ui_message_type(
    was_offline: bool,
    is_offline: bool,
    probe_result: bool,
) -> PollUiMessageType {
    if was_offline && probe_result {
        // Connectivity restored while we were offline: emit resume confirmation
        PollUiMessageType::ResumeConfirmation
    } else if is_offline {
        // Still offline: emit offline polling message
        PollUiMessageType::OfflinePoll
    } else {
        // Not offline: emit connectivity check message
        PollUiMessageType::ConnectivityCheck
    }
}

/// Determine which UI messages to emit after a connectivity poll.
///
/// Thin wiring function - calls pure policy function and translates result to messages.
/// Boundary functions should contain no policy logic, only wiring.
fn poll_ui_messages(
    was_offline: bool,
    connectivity: &ConnectivityState,
    probe_result: bool,
) -> Vec<String> {
    let message_type =
        determine_poll_ui_message_type(was_offline, connectivity.is_offline, probe_result);
    match message_type {
        PollUiMessageType::ResumeConfirmation => vec![
            offline_poll_message(connectivity.is_offline, connectivity.poll_pending),
            resume_confirmation_message(),
        ],
        PollUiMessageType::OfflinePoll => {
            vec![offline_poll_message(
                connectivity.is_offline,
                connectivity.poll_pending,
            )]
        }
        PollUiMessageType::ConnectivityCheck => {
            vec![connectivity_check_message(
                connectivity.is_offline,
                connectivity.check_pending,
            )]
        }
    }
}

/// Determine which UI events to emit after a connectivity poll.
///
/// Thin wiring function - calls pure policy function and wraps result in UIEvent.
/// Boundary functions should contain no policy logic, only wiring.
fn poll_ui_events(
    was_offline: bool,
    connectivity: &ConnectivityState,
    probe_result: bool,
) -> Vec<UIEvent> {
    poll_ui_messages(was_offline, connectivity, probe_result)
        .into_iter()
        .map(|msg| UIEvent::AgentActivity {
            agent: "connectivity".to_string(),
            message: msg,
        })
        .collect()
}

/// Determine the connectivity event based on probe result.
///
/// Pure policy function - maps probe result to the appropriate event.
fn poll_connectivity_event(probe_result: bool) -> PipelineEvent {
    if probe_result {
        PipelineEvent::Agent(AgentEvent::ConnectivityCheckSucceeded)
    } else {
        PipelineEvent::Agent(AgentEvent::ConnectivityCheckFailed)
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
///    - If back online while offline: emits resume confirmation UIEvent
///
/// The orchestrator re-derives this effect each cycle while offline, providing
/// debounced polling without handler-side loops.
pub(super) fn poll_for_connectivity(
    interval_ms: u64,
    connectivity: &ConnectivityState,
) -> EffectResult {
    let was_offline = connectivity.is_offline;

    std::thread::sleep(Duration::from_millis(interval_ms));

    let probe_result = probe_connectivity();

    // Pure policy: determine event and UI events
    let event = poll_connectivity_event(probe_result);
    let ui_events = poll_ui_events(was_offline, connectivity, probe_result);

    // Emit all UI events via chained with_ui_event calls
    let mut result = EffectResult::event(event);
    for ui_event in ui_events {
        result = result.with_ui_event(ui_event);
    }
    result
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

    #[test]
    fn test_poll_for_connectivity_emits_resume_confirmation_on_restore() {
        // Verify the message-selection logic directly via the resume_confirmation_message helper.
        // The actual network probe result is not testable in a unit test, so we test the
        // policy function directly.
        let resume = resume_confirmation_message();
        assert!(
            resume.contains("Connectivity restored"),
            "Resume message should contain 'Connectivity restored', got: {resume}"
        );
        assert!(
            resume.contains("resuming") || resume.contains("resume"),
            "Resume message should mention resuming workflow, got: {resume}"
        );
    }

    #[test]
    fn test_poll_ui_events_does_not_emit_resume_when_still_offline() {
        // Given: state that was offline and probe failed (still offline)
        let connectivity = ConnectivityState {
            is_offline: true,
            poll_pending: true,
            ..ConnectivityState::default()
        };
        let was_offline = true;
        let probe_result = false; // Probe failed - still offline!

        // When: poll_ui_events is called
        let events = poll_ui_events(was_offline, &connectivity, probe_result);

        // Then: should NOT emit resume message
        assert_eq!(
            events.len(),
            1,
            "Should emit exactly 1 event when still offline"
        );
        let msg = match &events[0] {
            UIEvent::AgentActivity { message, .. } => message.clone(),
            other => panic!("Expected AgentActivity, got: {:?}", other),
        };
        assert!(
            !msg.contains("Connectivity restored"),
            "Should NOT emit resume message when probe failed, got: {}",
            msg
        );
        assert!(
            msg.contains("Still offline"),
            "Should emit still-offline message, got: {}",
            msg
        );
    }

    #[test]
    fn test_poll_ui_events_emits_resume_when_online_restored() {
        // Given: state that was offline but probe succeeded (connectivity restored)
        let connectivity = ConnectivityState {
            is_offline: true, // Still true because reducer hasn't processed event yet
            poll_pending: true,
            ..ConnectivityState::default()
        };
        let was_offline = true;
        let probe_result = true; // Probe succeeded - connectivity restored!

        // When: poll_ui_events is called
        let events = poll_ui_events(was_offline, &connectivity, probe_result);

        // Then: should emit TWO events on restore:
        // 1. First: offline polling context message
        // 2. Second: resume confirmation message
        assert_eq!(events.len(), 2, "Should emit exactly 2 events on restore");

        // First event: offline polling context
        let first_msg = match &events[0] {
            UIEvent::AgentActivity { message, .. } => message.clone(),
            other => panic!("Expected AgentActivity, got: {:?}", other),
        };
        assert!(
            first_msg.contains("Still offline") || first_msg.contains("polling"),
            "First event should be offline polling context, got: {}",
            first_msg
        );

        // Second event: resume confirmation
        let second_msg = match &events[1] {
            UIEvent::AgentActivity { message, .. } => message.clone(),
            other => panic!("Expected AgentActivity, got: {:?}", other),
        };
        assert!(
            second_msg.contains("Connectivity restored"),
            "Second event should be resume message, got: {}",
            second_msg
        );
    }

    #[test]
    fn test_poll_ui_events_emits_check_message_when_online_and_not_offline() {
        // Given: state that was online (not offline) and probe succeeded
        let connectivity = ConnectivityState {
            is_offline: false,
            check_pending: true,
            ..ConnectivityState::default()
        };
        let was_offline = false;
        let probe_result = true;

        // When: poll_ui_events is called
        let events = poll_ui_events(was_offline, &connectivity, probe_result);

        // Then: should emit connectivity check message (not resume, not offline)
        assert_eq!(events.len(), 1, "Should emit exactly 1 event");
        let msg = match &events[0] {
            UIEvent::AgentActivity { message, .. } => message.clone(),
            other => panic!("Expected AgentActivity, got: {:?}", other),
        };
        assert!(
            !msg.contains("Connectivity restored"),
            "Should NOT emit resume when was_offline=false, got: {}",
            msg
        );
        assert!(
            !msg.contains("Still offline"),
            "Should NOT emit still-offline when online, got: {}",
            msg
        );
    }
}
