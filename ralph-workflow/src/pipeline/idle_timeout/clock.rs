// Clock trait and implementations for idle timeout monitoring

use super::file_activity::FileActivityTracker;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Clock trait for obtaining current time. Enables testing with mock clocks.
///
/// Prefer implementations that return monotonically increasing millisecond values.
///
/// In practice, some test clocks may simulate backwards jumps. This module
/// treats any backwards movement as *no elapsed time* by using
/// `saturating_sub(now, last)`.
pub trait Clock: Send + Sync {
    /// Returns the current time in milliseconds since an arbitrary epoch.
    ///
    /// If the value goes backwards, elapsed time is clamped to zero.
    fn now_millis(&self) -> u64;
}

/// Production clock using `Instant` for monotonic time.
///
/// Unlike `SystemTime`, `Instant` is guaranteed to be monotonically increasing
/// and is not affected by NTP synchronization, system sleep/wake cycles, or
/// manual clock adjustments. This prevents spurious idle timeout kills caused
/// by clock jumps.
pub struct MonotonicClock {
    /// The reference point for all time measurements.
    epoch: Instant,
}

impl MonotonicClock {
    /// Create a new monotonic clock with the current instant as epoch.
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

/// Global monotonic clock instance for production use.
///
/// This is lazily initialized on first use and provides monotonic time
/// measurements for the entire process lifetime.
fn global_clock() -> &'static MonotonicClock {
    use std::sync::OnceLock;
    static CLOCK: OnceLock<MonotonicClock> = OnceLock::new();
    CLOCK.get_or_init(MonotonicClock::new)
}

/// Default idle timeout in seconds (5 minutes).
///
/// This value was chosen because:
/// - Complex tool operations rarely take more than 2 minutes of silence
/// - LLM reasoning models may take 30-60 seconds between outputs
/// - If no output for 5 minutes, the agent is almost certainly stuck
pub const IDLE_TIMEOUT_SECS: u64 = 300;

/// Shared timestamp for tracking last activity.
///
/// Stores milliseconds since process start (monotonic time).
/// Use [`new_activity_timestamp`] to create.
pub type SharedActivityTimestamp = Arc<AtomicU64>;

/// Shared file activity tracker for timeout detection.
///
/// Tracks modification times of AI-generated files to detect ongoing work
/// that may not produce stdout/stderr output. Use [`new_file_activity_tracker`]
/// to create.
pub type SharedFileActivityTracker = Arc<Mutex<FileActivityTracker>>;

/// Creates a new shared activity timestamp initialized to the current time.
///
/// Uses monotonic time (via `Instant`) to prevent issues with clock jumps
/// from NTP synchronization or system sleep/wake cycles.
#[must_use]
pub fn new_activity_timestamp() -> SharedActivityTimestamp {
    Arc::new(AtomicU64::new(global_clock().now_millis()))
}

/// Creates a new shared activity timestamp using a custom clock.
///
/// This variant is primarily used for testing with mock clocks.
pub fn new_activity_timestamp_with_clock(clock: &dyn Clock) -> SharedActivityTimestamp {
    Arc::new(AtomicU64::new(clock.now_millis()))
}

/// Creates a new shared file activity tracker.
///
/// The tracker monitors AI-generated files in the `.agent/` directory to detect
/// ongoing work that may not produce stdout/stderr output.
#[must_use]
pub fn new_file_activity_tracker() -> SharedFileActivityTracker {
    Arc::new(Mutex::new(FileActivityTracker::new()))
}

/// Updates the shared activity timestamp to the current time.
///
/// Uses monotonic time to ensure consistent behavior regardless of
/// system clock changes.
pub fn touch_activity(timestamp: &SharedActivityTimestamp) {
    timestamp.store(global_clock().now_millis(), Ordering::Release);
}

/// Updates the shared activity timestamp using a custom clock.
///
/// This variant is primarily used for testing with mock clocks.
pub fn touch_activity_with_clock(timestamp: &SharedActivityTimestamp, clock: &dyn Clock) {
    timestamp.store(clock.now_millis(), Ordering::Release);
}

/// Returns the duration since the last activity update.
///
/// Uses monotonic time, so the result is always non-negative and
/// unaffected by system clock changes.
pub fn time_since_activity(timestamp: &SharedActivityTimestamp) -> Duration {
    let last_ms = timestamp.load(Ordering::Acquire);
    let now_ms = global_clock().now_millis();
    Duration::from_millis(now_ms.saturating_sub(last_ms))
}

/// Returns the duration since the last activity update using a custom clock.
///
/// This variant is primarily used for testing with mock clocks.
///
/// If the clock value goes backwards relative to the stored activity timestamp,
/// the elapsed time is clamped to zero.
pub fn time_since_activity_with_clock(
    timestamp: &SharedActivityTimestamp,
    clock: &dyn Clock,
) -> Duration {
    let last_ms = timestamp.load(Ordering::Acquire);
    let now_ms = clock.now_millis();
    Duration::from_millis(now_ms.saturating_sub(last_ms))
}

/// Checks if the idle timeout has been exceeded.
///
/// Uses monotonic time to prevent spurious timeout triggers from clock jumps.
pub fn is_idle_timeout_exceeded(timestamp: &SharedActivityTimestamp, timeout: Duration) -> bool {
    time_since_activity(timestamp) > timeout
}

/// Checks if the idle timeout has been exceeded using a custom clock.
///
/// This variant is primarily used for testing with mock clocks.
pub fn is_idle_timeout_exceeded_with_clock(
    timestamp: &SharedActivityTimestamp,
    timeout: Duration,
    clock: &dyn Clock,
) -> bool {
    time_since_activity_with_clock(timestamp, clock) > timeout
}
