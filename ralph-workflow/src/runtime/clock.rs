//! Clock and time utilities in the runtime boundary.
//!
//! This module provides time-related capabilities that domain code
//! can use through trait abstraction.

use std::time::{Duration, Instant};

/// Trait for time operations, allowing testability.
pub trait Clock: Send + Sync {
    /// Get the current instant.
    fn now(&self) -> Instant;

    /// Get a duration since the given instant.
    fn elapsed(&self, start: Instant) -> Duration {
        self.now().duration_since(start)
    }
}

/// Real clock implementation using system time.
pub struct RealClock;

impl Clock for RealClock {
    fn now(&self) -> Instant {
        Instant::now()
    }
}
