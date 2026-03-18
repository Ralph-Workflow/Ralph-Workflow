//! Timer utilities in the runtime boundary.
//!
//! This module provides time tracking capabilities that domain code can use.
//! Clock reading is isolated here to comply with functional programming rules.

use std::time::{Duration, Instant};

/// Timer for tracking execution duration with phase support.
/// This is a boundary module - clock access is allowed here.
#[derive(Clone)]
pub struct Timer {
    start_time: Instant,
    phase_start: Instant,
}

impl Timer {
    #[must_use]
    pub fn new() -> Self {
        let now = Instant::now();
        Self {
            start_time: now,
            phase_start: now,
        }
    }

    #[must_use]
    pub fn from_timestamps(start_time: Instant, phase_start: Instant) -> Self {
        Self {
            start_time,
            phase_start,
        }
    }

    pub fn start_phase(&mut self) {
        self.phase_start = Instant::now();
    }

    #[must_use]
    pub fn elapsed(&self) -> Duration {
        self.start_time.elapsed()
    }

    #[must_use]
    pub fn phase_elapsed(&self) -> Duration {
        self.phase_start.elapsed()
    }

    #[must_use]
    pub fn format_duration(duration: Duration) -> String {
        let total_secs = duration.as_secs();
        let mins = total_secs / 60;
        let secs = total_secs % 60;
        format!("{mins}m {secs:02}s")
    }

    #[must_use]
    pub fn elapsed_formatted(&self) -> String {
        Self::format_duration(self.elapsed())
    }

    #[must_use]
    pub fn phase_elapsed_formatted(&self) -> String {
        Self::format_duration(self.phase_elapsed())
    }
}

impl Default for Timer {
    fn default() -> Self {
        Self::new()
    }
}
