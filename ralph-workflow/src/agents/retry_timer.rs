//! Retry timer provider for controlling sleep behavior in retry logic.
//!
//! This module provides a trait-based abstraction for `std::thread::sleep`
//! to make retry logic testable. Production code uses real sleep delays,
//! while tests can use immediate (no-op) sleeps for fast execution.

use std::sync::Arc;
use std::time::Duration;

/// Provider for sleep operations in retry logic.
///
/// This trait allows different sleep implementations:
/// - Production: Real `std::thread::sleep` with actual delays
/// - Testing: Immediate (no-op) sleeps for fast test execution
pub trait RetryTimerProvider: Send + Sync {
    /// Sleep for the specified duration.
    fn sleep(&self, duration: Duration);
}

/// Production retry timer that actually sleeps.
#[derive(Debug, Clone)]
struct ProductionRetryTimer;

impl RetryTimerProvider for ProductionRetryTimer {
    fn sleep(&self, duration: Duration) {
        std::thread::sleep(duration);
    }
}

/// Create a new production retry timer.
///
/// This is used in production code where actual sleep delays are needed.
pub fn production_timer() -> Arc<dyn RetryTimerProvider> {
    Arc::new(ProductionRetryTimer)
}

/// Test retry timer that doesn't actually sleep (immediate return).
///
/// This is used in tests to avoid long delays while still exercising
/// the retry logic. The sleep duration is tracked for assertions.
#[cfg(test)]
#[derive(Debug, Clone)]
pub struct TestRetryTimer {
    /// Optional tracking of sleep durations for test assertions.
    /// Uses interior mutability to track sleeps through shared references.
    tracked: Option<Arc<std::sync::atomic::AtomicU64>>,
}

#[cfg(test)]
impl Default for TestRetryTimer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl TestRetryTimer {
    /// Create a new test retry timer without tracking.
    pub fn new() -> Self {
        Self { tracked: None }
    }

    /// Create a new test retry timer that tracks total sleep duration in milliseconds.
    ///
    /// This is useful for tests that need to verify retry behavior without
    /// actually waiting. The tracked duration can be retrieved with `total_sleep_ms()`.
    #[cfg(test)]
    pub fn with_tracking() -> (Self, Arc<std::sync::atomic::AtomicU64>) {
        let tracked = Arc::new(std::sync::atomic::AtomicU64::new(0));
        (
            Self {
                tracked: Some(tracked.clone()),
            },
            tracked,
        )
    }

    /// Get the total sleep duration in milliseconds (if tracking is enabled).
    #[cfg(test)]
    pub fn total_sleep_ms(&self) -> Option<u64> {
        self.tracked
            .as_ref()
            .map(|t| t.load(std::sync::atomic::Ordering::Relaxed))
    }
}

#[cfg(test)]
impl RetryTimerProvider for TestRetryTimer {
    fn sleep(&self, duration: Duration) {
        if let Some(tracked) = &self.tracked {
            tracked.fetch_add(
                u64::try_from(duration.as_millis()).unwrap_or(u64::MAX),
                std::sync::atomic::Ordering::Relaxed,
            );
        }
        // No actual sleep - return immediately for fast tests
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_retry_timer_returns_immediately_without_blocking() {
        let timer = TestRetryTimer::new();
        let start = std::time::Instant::now();
        timer.sleep(Duration::from_secs(10));
        let elapsed = start.elapsed();
        assert!(
            elapsed < Duration::from_millis(100),
            "Should return immediately"
        );
    }

    #[test]
    fn test_test_retry_timer_tracking() {
        let (timer, tracked) = TestRetryTimer::with_tracking();

        timer.sleep(Duration::from_millis(100));
        timer.sleep(Duration::from_millis(200));
        timer.sleep(Duration::from_millis(300));

        assert_eq!(timer.total_sleep_ms(), Some(600));
        assert_eq!(tracked.load(std::sync::atomic::Ordering::Relaxed), 600);
    }

    #[test]
    fn test_test_retry_timer_no_tracking() {
        let timer = TestRetryTimer::new();
        timer.sleep(Duration::from_millis(100));
        assert_eq!(timer.total_sleep_ms(), None);
    }
}
