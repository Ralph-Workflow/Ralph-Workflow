//! Retry timer provider for controlling sleep behavior in retry logic.
//!
//! This module provides the trait for retry timers. Production code uses
//! the `runtime` module for actual sleep implementation.

// Re-export from boundary module for convenience
pub use crate::agents::{production_timer, RetryTimerProvider, RetryTimerProviderDebug};

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    /// Test retry timer that doesn't actually sleep (immediate return).
    #[derive(Debug, Clone)]
    pub struct TestRetryTimer {
        tracked: Option<std::sync::Arc<std::sync::atomic::AtomicU64>>,
    }

    impl Default for TestRetryTimer {
        fn default() -> Self {
            Self::new()
        }
    }

    impl TestRetryTimer {
        pub fn new() -> Self {
            Self { tracked: None }
        }

        pub fn with_tracking() -> (Self, std::sync::Arc<std::sync::atomic::AtomicU64>) {
            let tracked = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
            (
                Self {
                    tracked: Some(tracked.clone()),
                },
                tracked,
            )
        }

        pub fn total_sleep_ms(&self) -> Option<u64> {
            self.tracked
                .as_ref()
                .map(|t| t.load(std::sync::atomic::Ordering::Relaxed))
        }
    }

    impl RetryTimerProvider for TestRetryTimer {
        fn sleep(&self, duration: Duration) {
            if let Some(tracked) = &self.tracked {
                tracked.fetch_add(
                    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX),
                    std::sync::atomic::Ordering::Relaxed,
                );
            }
        }
    }

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
