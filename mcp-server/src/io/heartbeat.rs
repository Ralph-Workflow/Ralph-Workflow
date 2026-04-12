//! Heartbeat monitoring utilities for MCP server lifecycle control.
use std::sync::mpsc::{Receiver, Sender};
use std::time::{Duration, Instant};

/// Sender side of the heartbeat channel.
pub type HeartbeatSender = Sender<Instant>;

/// Receiver side of the heartbeat channel.
pub type HeartbeatReceiver = Receiver<Instant>;

/// Heartbeat policy defines ping timing and grace window values.
#[derive(Debug, Clone, Copy)]
pub struct HeartbeatPolicy {
    /// How often the orchestrator sends a heartbeat ping.
    pub ping_interval: Duration,
    /// Number of missed pings tolerated before entering the grace window.
    pub max_missed_heartbeats: u32,
    /// Extra window after the last allowed miss before triggering termination.
    pub reconnect_window: Duration,
}

impl HeartbeatPolicy {
    /// Create a new policy from explicit durations and miss count.
    pub fn new(
        ping_interval: Duration,
        max_missed_heartbeats: u32,
        reconnect_window: Duration,
    ) -> Self {
        assert!(
            ping_interval > Duration::ZERO,
            "heartbeat interval must be positive"
        );
        assert!(
            max_missed_heartbeats > 0,
            "max_missed_heartbeats must be > 0"
        );
        Self {
            ping_interval,
            max_missed_heartbeats,
            reconnect_window,
        }
    }

    /// Create the heartbeat channel pair.
    pub fn make_channel(&self) -> (HeartbeatSender, HeartbeatReceiver) {
        std::sync::mpsc::channel::<Instant>()
    }
}

impl Default for HeartbeatPolicy {
    fn default() -> Self {
        Self {
            ping_interval: Duration::from_secs(2),
            max_missed_heartbeats: 3,
            reconnect_window: Duration::from_secs(10),
        }
    }
}

/// Decision emitted by the heartbeat monitor after checking timings.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HeartbeatDecision {
    /// Heartbeat stream is healthy.
    Healthy,
    /// Grace window has begun (miss limit reached but reconnect window open).
    GraceWindow {
        /// Number of consecutive missed heartbeat intervals.
        misses: u32,
        /// Deadline when the reconnect window expires.
        deadline: Instant,
    },
    /// Heartbeat deadline exceeded and termination should start.
    Terminate,
}

/// Tracks heartbeat arrivals and determines when the server should drain.
pub struct HeartbeatMonitor {
    policy: HeartbeatPolicy,
    next_expected: Instant,
    missed: u32,
    grace_deadline: Option<Instant>,
    terminated: bool,
}

impl HeartbeatMonitor {
    fn already_terminated_decision(&self) -> Option<HeartbeatDecision> {
        self.terminated.then_some(HeartbeatDecision::Terminate)
    }

    fn advance_missed_intervals(&mut self, now: Instant) {
        while now >= self.next_expected {
            self.missed = self.missed.saturating_add(1);
            self.next_expected += self.policy.ping_interval;
        }
    }

    fn under_miss_threshold_decision(&self) -> Option<HeartbeatDecision> {
        (self.missed < self.policy.max_missed_heartbeats).then_some(HeartbeatDecision::Healthy)
    }

    fn resolve_grace_deadline(&self, now: Instant) -> Instant {
        self.grace_deadline
            .or_else(|| self.grace_deadline_from_miss_history())
            .unwrap_or(now + self.policy.reconnect_window)
    }

    fn grace_or_terminate_decision(
        &mut self,
        now: Instant,
        deadline: Instant,
    ) -> HeartbeatDecision {
        self.grace_deadline = Some(deadline);
        if now >= deadline {
            self.terminated = true;
            HeartbeatDecision::Terminate
        } else {
            HeartbeatDecision::GraceWindow {
                misses: self.missed,
                deadline,
            }
        }
    }

    fn grace_deadline_from_miss_history(&self) -> Option<Instant> {
        if self.missed < self.policy.max_missed_heartbeats {
            return None;
        }

        let first_missed_expected = self.next_expected - (self.policy.ping_interval * self.missed);
        let threshold_reached_at = first_missed_expected
            + (self.policy.ping_interval * (self.policy.max_missed_heartbeats - 1));
        Some(threshold_reached_at + self.policy.reconnect_window)
    }

    /// Create a new monitor that starts counting from `Instant::now()`.
    pub fn new(policy: HeartbeatPolicy) -> Self {
        let now = Instant::now();
        Self {
            policy,
            next_expected: now + policy.ping_interval,
            missed: 0,
            grace_deadline: None,
            terminated: false,
        }
    }

    /// Record a heartbeat arrival.
    pub fn record_heartbeat(&mut self, when: Instant) {
        if self.terminated {
            return;
        }
        self.next_expected = when + self.policy.ping_interval;
        self.missed = 0;
        self.grace_deadline = None;
    }

    /// Check heartbeat health at `now` and return the resulting decision.
    pub fn check(&mut self, now: Instant) -> HeartbeatDecision {
        if let Some(decision) = self.already_terminated_decision() {
            return decision;
        }

        self.advance_missed_intervals(now);

        if let Some(decision) = self.under_miss_threshold_decision() {
            return decision;
        }

        let deadline = self.resolve_grace_deadline(now);
        self.grace_or_terminate_decision(now, deadline)
    }
}
