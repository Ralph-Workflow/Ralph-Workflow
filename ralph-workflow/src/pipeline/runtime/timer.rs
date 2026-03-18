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
    /// Create a new timer, starting now
    #[must_use]
    pub fn new() -> Self {
        let now = Instant::now();
        Self {
            start_time: now,
            phase_start: now,
        }
    }

    /// Create a timer with pre-existing timestamps (for replay/testing)
    #[must_use]
    pub fn from_timestamps(start_time: Instant, phase_start: Instant) -> Self {
        Self {
            start_time,
            phase_start,
        }
    }

    /// Start a new phase timer
    pub fn start_phase(&mut self) {
        self.phase_start = Instant::now();
    }

    /// Get elapsed time since timer start
    #[must_use]
    pub fn elapsed(&self) -> Duration {
        self.start_time.elapsed()
    }

    /// Get elapsed time since phase start
    #[must_use]
    pub fn phase_elapsed(&self) -> Duration {
        self.phase_start.elapsed()
    }

    /// Format a duration as "Xm YYs"
    #[must_use]
    pub fn format_duration(duration: Duration) -> String {
        let total_secs = duration.as_secs();
        let mins = total_secs / 60;
        let secs = total_secs % 60;
        format!("{mins}m {secs:02}s")
    }

    /// Get formatted elapsed time since start
    #[must_use]
    pub fn elapsed_formatted(&self) -> String {
        Self::format_duration(self.elapsed())
    }

    /// Get formatted elapsed time since phase start
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

#[cfg(test)]
mod timer_tests {
    use super::*;

    #[test]
    fn test_format_duration_zero() {
        let d = Duration::from_secs(0);
        assert_eq!(Timer::format_duration(d), "0m 00s");
    }

    #[test]
    fn test_format_duration_seconds() {
        let d = Duration::from_secs(30);
        assert_eq!(Timer::format_duration(d), "0m 30s");
    }

    #[test]
    fn test_format_duration_minutes() {
        let d = Duration::from_secs(65);
        assert_eq!(Timer::format_duration(d), "1m 05s");
    }

    #[test]
    fn test_format_duration_large() {
        let d = Duration::from_secs(3661);
        assert_eq!(Timer::format_duration(d), "61m 01s");
    }
}
