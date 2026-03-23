// pipeline/idle_timeout/clock/io.rs — boundary module for clock utilities.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Clock utilities for idle timeout monitoring.
// This is a boundary module - clock access is allowed here.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime};

pub trait Clock: Send + Sync {
    fn now_millis(&self) -> u64;
}

pub struct MonotonicClock {
    epoch: Instant,
}

impl MonotonicClock {
    #[must_use]
    pub fn new() -> Self {
        Self {
            epoch: Instant::now(),
        }
    }
}

impl Default for MonotonicClock {
    fn default() -> Self {
        Self::new()
    }
}

impl Clock for MonotonicClock {
    fn now_millis(&self) -> u64 {
        u64::try_from(self.epoch.elapsed().as_millis()).unwrap_or(u64::MAX)
    }
}

pub const IDLE_TIMEOUT_SECS: u64 = 300;

pub type SharedActivityTimestamp = Arc<AtomicU64>;

pub type SharedFileActivityTracker = Arc<inner::FileActivityTrackerInner>;

pub mod inner {
    use crate::pipeline::idle_timeout::FileActivityTracker;

    pub struct FileActivityTrackerInner(pub FileActivityTracker);

    impl FileActivityTrackerInner {
        pub fn new() -> Self {
            Self(FileActivityTracker::new())
        }

        pub fn lock(&self) -> &FileActivityTracker {
            &self.0
        }
    }

    impl Default for FileActivityTrackerInner {
        fn default() -> Self {
            Self::new()
        }
    }
}

#[must_use]
pub fn new_activity_timestamp() -> SharedActivityTimestamp {
    Arc::new(AtomicU64::new(current_time_millis()))
}

#[must_use]
pub fn new_activity_timestamp_with_clock(clock: &dyn Clock) -> SharedActivityTimestamp {
    Arc::new(AtomicU64::new(clock.now_millis()))
}

#[must_use]
pub fn new_file_activity_tracker() -> SharedFileActivityTracker {
    Arc::new(inner::FileActivityTrackerInner::new())
}

pub fn touch_activity(timestamp: &SharedActivityTimestamp) {
    let now_ms = current_time_millis();
    timestamp.store(now_ms, Ordering::Release);
}

pub fn touch_activity_with_clock(timestamp: &SharedActivityTimestamp, clock: &dyn Clock) {
    timestamp.store(clock.now_millis(), Ordering::Release);
}

pub fn time_since_activity(timestamp: &SharedActivityTimestamp) -> Duration {
    let last_ms = timestamp.load(Ordering::Acquire);
    let now_ms = current_time_millis();
    Duration::from_millis(now_ms.saturating_sub(last_ms))
}

pub fn time_since_activity_with_clock(
    timestamp: &SharedActivityTimestamp,
    clock: &dyn Clock,
) -> Duration {
    let last_ms = timestamp.load(Ordering::Acquire);
    let now_ms = clock.now_millis();
    Duration::from_millis(now_ms.saturating_sub(last_ms))
}

pub fn is_idle_timeout_exceeded(timestamp: &SharedActivityTimestamp, timeout: Duration) -> bool {
    time_since_activity(timestamp) > timeout
}

pub fn is_idle_timeout_exceeded_with_clock(
    timestamp: &SharedActivityTimestamp,
    timeout: Duration,
    clock: &dyn Clock,
) -> bool {
    time_since_activity_with_clock(timestamp, clock) > timeout
}

fn current_time_millis() -> u64 {
    SystemTime::UNIX_EPOCH
        .elapsed()
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok())
        .unwrap_or(u64::MAX)
}
