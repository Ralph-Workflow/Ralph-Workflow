use std::cell::Cell;

use crate::json_parser::health::ParserHealth;
use crate::logger::Colors;

pub struct HealthMonitor {
    health: Cell<ParserHealth>,
    parser_name: &'static str,
    threshold_warned: Cell<bool>,
}

impl HealthMonitor {
    #[must_use]
    pub fn new(parser_name: &'static str) -> Self {
        Self {
            health: Cell::new(ParserHealth::new()),
            parser_name,
            threshold_warned: Cell::new(false),
        }
    }

    pub fn record_parsed(&self) {
        self.health.update(|mut h| {
            h.record_parsed();
            h
        });
    }

    pub fn record_ignored(&self) {
        self.health.update(|mut h| {
            h.record_ignored();
            h
        });
    }

    pub fn record_unknown_event(&self) {
        self.health.update(|mut h| {
            h.record_unknown_event();
            h
        });
    }

    pub fn record_parse_error(&self) {
        self.health.update(|mut h| {
            h.record_parse_error();
            h
        });
    }

    pub fn record_control_event(&self) {
        self.health.update(|mut h| {
            h.record_control_event();
            h
        });
    }

    pub fn record_partial_event(&self) {
        self.health.update(|mut h| {
            h.record_partial_event();
            h
        });
    }

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
