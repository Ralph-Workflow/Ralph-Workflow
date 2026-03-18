//! Runtime boundary for phases module.
//! This module contains OS-boundary code like timing operations.

use std::time::{Duration, Instant};

/// Capture the current instant for timing purposes.
/// This is a boundary function - clock access is allowed here.
#[must_use]
pub fn capture_time() -> Instant {
    Instant::now()
}

/// Calculate elapsed time in seconds.
/// This is a boundary function - clock access is allowed here.
#[must_use]
pub fn elapsed_seconds(start: Instant) -> u64 {
    start.elapsed().as_secs()
}

/// Calculate elapsed duration.
/// This is a boundary function - clock access is allowed here.
#[must_use]
pub fn elapsed(start: Instant) -> Duration {
    start.elapsed()
}
