// Runtime operations: sleep, timers, etc.

use std::sync::Arc;
use std::time::Duration;

/// Provider for sleep operations in retry logic.
///
/// This trait allows different sleep implementations:
/// - Production: Real `std::thread::sleep` with actual delays
/// - Testing: Immediate (no-op) sleeps for fast test execution
pub trait RetryTimerProvider: Send + Sync {
    fn sleep(&self, duration: Duration);
}

/// Production retry timer that actually sleeps.
#[derive(Debug, Clone)]
pub struct ProductionRetryTimer;

impl RetryTimerProvider for ProductionRetryTimer {
    fn sleep(&self, duration: Duration) {
        std::thread::sleep(duration);
    }
}

/// Create a new production retry timer.
pub fn production_timer() -> Arc<dyn RetryTimerProvider> {
    Arc::new(ProductionRetryTimer)
}
