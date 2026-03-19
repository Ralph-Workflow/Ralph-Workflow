// Health monitor implementation.
//
// Contains the HealthMonitor struct for tracking parser health.

use std::cell::Cell;

use crate::json_parser::health::ParserHealth;
use crate::logger::Colors;

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
        self.health.update(|mut h| {
            h.record_parsed();
            h
        });
    }

    /// Record that an event was ignored
    pub fn record_ignored(&self) {
        self.health.update(|mut h| {
            h.record_ignored();
            h
        });
    }

    /// Record an unknown event type (valid JSON but unhandled)
    pub fn record_unknown_event(&self) {
        self.health.update(|mut h| {
            h.record_unknown_event();
            h
        });
    }

    /// Record a parse error (malformed JSON)
    pub fn record_parse_error(&self) {
        self.health.update(|mut h| {
            h.record_parse_error();
            h
        });
    }

    /// Record a control event (state management with no user-facing output)
    pub fn record_control_event(&self) {
        self.health.update(|mut h| {
            h.record_control_event();
            h
        });
    }

    /// Record a partial/delta event (streaming content displayed incrementally)
    ///
    /// Partial events represent streaming content that is shown to the user
    /// in real-time as deltas. These are NOT errors and should not trigger
    /// health warnings.
    pub fn record_partial_event(&self) {
        self.health.update(|mut h| {
            h.record_partial_event();
            h
        });
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
