//! Health monitoring for parsers.
//!
//! This module provides the HealthMonitor for tracking parser health.

pub mod metrics;
pub mod parser_health;

use crate::logger::Colors;
use std::cell::Cell;

pub use metrics::{StreamingPattern, StreamingQualityMetrics};
pub use parser_health::ParserHealth;

/// A wrapper that monitors parser health and provides graceful degradation
///
/// This wraps any parser function to track how many events are being ignored
/// and emit warnings when the parser seems to be misconfigured.
pub struct HealthMonitor {
    health: Cell<ParserHealth>,
    parser_name: &'static str,
    threshold_warned: Cell<bool>,
}

impl HealthMonitor {
    /// Create a new health monitor for a parser
    #[must_use]
    pub fn new(parser_name: &'static str) -> Self {
        Self {
            health: Cell::new(ParserHealth::new()),
            parser_name,
            threshold_warned: Cell::new(false),
        }
    }

    /// Record that an event was parsed successfully
    pub fn record_parsed(&self) {
        let mut h = self.health.get();
        h.record_parsed();
        self.health.set(h);
    }

    /// Record that an event was ignored
    pub fn record_ignored(&self) {
        let mut h = self.health.get();
        h.record_ignored();
        self.health.set(h);
    }

    /// Record an unknown event type (valid JSON but unhandled)
    pub fn record_unknown_event(&self) {
        let mut h = self.health.get();
        h.record_unknown_event();
        self.health.set(h);
    }

    /// Record a parse error (malformed JSON)
    pub fn record_parse_error(&self) {
        let mut h = self.health.get();
        h.record_parse_error();
        self.health.set(h);
    }

    /// Record a control event (state management with no user-facing output)
    pub fn record_control_event(&self) {
        let mut h = self.health.get();
        h.record_control_event();
        self.health.set(h);
    }

    /// Record a partial/delta event (streaming content displayed incrementally)
    ///
    /// Partial events represent streaming content that is shown to the user
    /// in real-time as deltas. These are NOT errors and should not trigger
    /// health warnings.
    pub fn record_partial_event(&self) {
        let mut h = self.health.get();
        h.record_partial_event();
        self.health.set(h);
    }

    /// Check if we should warn about parser health (only warn once)
    pub fn check_and_warn(&self, colors: Colors) -> Option<String> {
        if self.threshold_warned.get() {
            return None;
        }

        let health = self.health.get();
        let warning = health.warning(self.parser_name, colors);
        if warning.is_some() {
            self.threshold_warned.set(true);
        }
        warning
    }
}
