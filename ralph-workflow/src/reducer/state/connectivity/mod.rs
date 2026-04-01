//! Connectivity state for offline detection and freeze-and-resume workflow.
//!
//! Tracks network connectivity status to enable the pipeline to detect when
//! network connectivity is lost, freeze workflow state without consuming
//! continuation/retry budgets, and automatically resume when connectivity returns.

use serde::{Deserialize, Serialize};

/// Tracks network connectivity status for offline detection.
///
/// This state is used to implement the freeze-and-resume workflow feature:
/// - When network connectivity is lost, the pipeline enters offline mode
/// - While offline, all budget-consuming operations are suspended
/// - When connectivity returns, the pipeline resumes from the frozen checkpoint
///
/// # State Transitions
///
/// ```text
/// [Online] --(probe failed)--> [Probe Failing] --(threshold reached)--> [Offline]
/// [Offline] --(probe succeeded)--> [Back Online] --(orchestrator resumes)--> [Online]
/// ```
///
/// # Debouncing
///
/// To prevent rapid offline/online thrashing on unstable connections:
/// - Must fail N consecutive probes before entering offline mode (default: 2)
/// - Must succeed 1 probe before exiting offline mode (default: 1)
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ConnectivityState {
    /// True when we have confirmed the pipeline is in offline mode (poll loop active).
    pub is_offline: bool,

    /// True when an InvocationFailed{Network} event was received and we need to verify
    /// connectivity before consuming retry budget.
    pub check_pending: bool,

    /// True when offline is confirmed and we are in the poll-for-reconnect loop.
    pub poll_pending: bool,

    /// Consecutive failed probes (used for debounce threshold before entering offline mode).
    pub consecutive_failures: u32,

    /// Consecutive successful probes (used for debounce before exiting offline mode).
    pub consecutive_successes: u32,

    /// How many consecutive failures before entering offline mode (default: 2).
    pub required_failures_to_go_offline: u32,

    /// How many consecutive successes before exiting offline mode (default: 1).
    pub required_successes_to_go_online: u32,

    /// Milliseconds to wait between polls while offline (default: 5000).
    pub offline_poll_interval_ms: u64,
}

impl Default for ConnectivityState {
    fn default() -> Self {
        Self {
            is_offline: false,
            check_pending: false,
            poll_pending: false,
            consecutive_failures: 0,
            consecutive_successes: 0,
            required_failures_to_go_offline: 2,
            required_successes_to_go_online: 1,
            offline_poll_interval_ms: 5000,
        }
    }
}

impl ConnectivityState {
    /// Create a new empty connectivity state (fully online, no pending checks).
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Mark that a connectivity check is needed (triggered by Network error).
    #[must_use]
    pub fn trigger_check(self) -> Self {
        Self {
            check_pending: true,
            ..self
        }
    }

    /// Clear the check_pending flag.
    #[must_use]
    pub fn clear_check_pending(self) -> Self {
        Self {
            check_pending: false,
            ..self
        }
    }

    /// Record a failed connectivity probe.
    ///
    /// Returns a new state with updated counters. If the failure threshold is reached,
    /// transitions to offline mode.
    #[must_use]
    pub fn on_probe_failed(self) -> Self {
        let consecutive_failures = self.consecutive_failures.saturating_add(1);
        let consecutive_successes = 0;
        let is_offline = consecutive_failures >= self.required_failures_to_go_offline;
        let poll_pending = is_offline;
        // Keep checking until threshold is reached
        let check_pending = !is_offline;
        Self {
            is_offline,
            poll_pending,
            check_pending,
            consecutive_failures,
            consecutive_successes,
            ..self
        }
    }

    /// Record a successful connectivity probe.
    ///
    /// Returns a new state with updated counters. If the success threshold is reached
    /// while offline, transitions back to online mode.
    #[must_use]
    pub fn on_probe_succeeded(self) -> Self {
        let consecutive_successes = self.consecutive_successes.saturating_add(1);
        let consecutive_failures = 0;
        let back_online = consecutive_successes >= self.required_successes_to_go_online;
        let is_offline = if back_online { false } else { self.is_offline };
        let poll_pending = if back_online {
            false
        } else {
            self.poll_pending
        };
        let check_pending = false;
        Self {
            is_offline,
            poll_pending,
            check_pending,
            consecutive_failures,
            consecutive_successes,
            ..self
        }
    }

    /// Reset debounce counters without changing offline/online status.
    ///
    /// Use when a transient state change should reset the debounce counters.
    #[must_use]
    pub fn reset_debounce(self) -> Self {
        Self {
            consecutive_failures: 0,
            consecutive_successes: 0,
            ..self
        }
    }

    /// Returns true if we have entered offline mode (debounce threshold was met).
    #[must_use]
    pub const fn is_offline_mode(&self) -> bool {
        self.is_offline
    }

    /// Returns true if a connectivity check is pending.
    #[must_use]
    pub const fn is_check_pending(&self) -> bool {
        self.check_pending
    }

    /// Returns true if we are actively polling for connectivity (offline mode).
    #[must_use]
    pub const fn is_poll_pending(&self) -> bool {
        self.poll_pending
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_state_is_fully_online() {
        let state = ConnectivityState::default();
        assert!(!state.is_offline);
        assert!(!state.check_pending);
        assert!(!state.poll_pending);
        assert_eq!(state.consecutive_failures, 0);
        assert_eq!(state.consecutive_successes, 0);
    }

    #[test]
    fn test_trigger_check_sets_check_pending() {
        let state = ConnectivityState::default().trigger_check();
        assert!(state.check_pending);
        assert!(!state.is_offline);
        assert!(!state.poll_pending);
    }

    #[test]
    fn test_single_probe_failure_below_threshold() {
        // required_failures_to_go_offline is 2 by default
        let state = ConnectivityState::default().on_probe_failed();
        assert!(
            !state.is_offline,
            "Should not be offline after only 1 failure"
        );
        assert!(
            state.check_pending,
            "Should still be checking after 1 failure"
        );
        assert!(!state.poll_pending);
        assert_eq!(state.consecutive_failures, 1);
    }

    #[test]
    fn test_threshold_probe_failures_trigger_offline() {
        // required_failures_to_go_offline is 2 by default
        let state = ConnectivityState::default()
            .on_probe_failed()
            .on_probe_failed();
        assert!(
            state.is_offline,
            "Should be offline after 2 consecutive failures"
        );
        assert!(!state.check_pending, "Should not be checking when offline");
        assert!(state.poll_pending);
        assert_eq!(state.consecutive_failures, 2);
    }

    #[test]
    fn test_probe_success_while_online_clears_check() {
        let state = ConnectivityState::default()
            .trigger_check()
            .on_probe_succeeded();
        assert!(!state.check_pending);
        assert!(!state.is_offline);
        assert_eq!(state.consecutive_failures, 0);
    }

    #[test]
    fn test_probe_success_while_offline_triggers_back_online() {
        // required_successes_to_go_online is 1 by default
        let state = ConnectivityState {
            is_offline: true,
            poll_pending: true,
            check_pending: false,
            consecutive_failures: 2,
            consecutive_successes: 0,
            ..Default::default()
        }
        .on_probe_succeeded();

        assert!(
            !state.is_offline,
            "Should be back online after 1 successful probe"
        );
        assert!(!state.poll_pending);
        assert!(!state.check_pending);
        assert_eq!(state.consecutive_failures, 0);
        assert_eq!(state.consecutive_successes, 1);
    }

    #[test]
    fn test_debounce_resets_on_success() {
        let state = ConnectivityState::default()
            .on_probe_failed()
            .on_probe_succeeded();

        assert_eq!(
            state.consecutive_failures, 0,
            "Failures should reset to 0 on success"
        );
        assert_eq!(state.consecutive_successes, 1);
    }

    #[test]
    fn test_clear_check_pending() {
        let state = ConnectivityState::default()
            .trigger_check()
            .clear_check_pending();

        assert!(!state.check_pending);
    }

    #[test]
    fn test_reset_debounce() {
        let state = ConnectivityState {
            consecutive_failures: 3,
            consecutive_successes: 2,
            ..Default::default()
        }
        .reset_debounce();

        assert_eq!(state.consecutive_failures, 0);
        assert_eq!(state.consecutive_successes, 0);
        // Online status should be unchanged
        assert!(!state.is_offline);
    }
}
