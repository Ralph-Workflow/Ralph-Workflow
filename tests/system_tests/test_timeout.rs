//! Test timeout enforcement for system tests.
//!
//! This module provides a mechanism to enforce maximum execution time
//! for system tests. Tests that take longer than the specified
//! timeout will be terminated and fail with a clear error message.
//!
//! # System Test Guidelines
//!
//! System tests MAY use real filesystem and git operations, but still
//! need timeout protection to prevent indefinite hangs.
//!
//! # MANDATORY Usage
//!
//! **ALL system tests MUST use either `with_default_timeout()` or `with_timeout()`
//! to wrap their test code.**
//!
//! # Usage
//!
//! ```rust,no_run
//! use crate::test_timeout::with_default_timeout;
//!
//! #[test]
//! fn test_name() {
//!     with_default_timeout(|| {
//!         // ALL test code must run inside this closure
//!         let dir = TempDir::new().unwrap();
//!         // ... rest of test
//!     });
//! }
//! ```
//!
//! # Default Timeout
//!
//! The default timeout for system tests is 30 seconds (longer than integration tests
//! because real git operations can be slower).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::Duration;

/// Default timeout for system tests (30 seconds).
///
/// System tests are allowed to perform real git operations which may take
/// longer than mocked integration tests.
pub(crate) const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

/// Error type for timeout failures.
#[derive(Debug)]
pub(crate) struct TimeoutError {
    pub timeout: Duration,
}

impl std::fmt::Display for TimeoutError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Test exceeded timeout of {:?}. \
             System tests should complete within 30 seconds even with real git operations.",
            self.timeout
        )
    }
}

impl std::error::Error for TimeoutError {}

/// Run a closure with a timeout.
///
/// If the closure does not complete within the specified duration,
/// the test will panic with a timeout error.
///
/// # Global interrupt support
///
/// System tests may spawn long-running child processes (e.g., a `ralph` CLI)
/// and block waiting for markers. If a timeout fires while a test is mid-flight,
/// we want to avoid leaving orphan child processes behind.
///
/// To support that, tests can register best-effort cleanup callbacks via
/// [`register_timeout_cleanup`]. These callbacks are invoked immediately before
/// timing out.
pub(crate) fn with_timeout<F>(f: F, timeout: Duration)
where
    F: FnOnce() + Send + 'static,
{
    let completed = Arc::new(AtomicBool::new(false));
    let completed_clone = Arc::clone(&completed);

    let handle = thread::spawn(move || {
        struct CompletionGuard(Arc<AtomicBool>);

        impl Drop for CompletionGuard {
            fn drop(&mut self) {
                self.0.store(true, Ordering::Release);
            }
        }

        let _guard = CompletionGuard(completed_clone);
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(f));
        if let Err(payload) = result {
            std::panic::resume_unwind(payload);
        }
    });

    let start = std::time::Instant::now();
    while !completed.load(Ordering::Acquire) {
        if start.elapsed() >= timeout {
            run_timeout_cleanups();
            panic!("{}", TimeoutError { timeout }.to_string());
        }
        // Small sleep to avoid busy-waiting
        thread::sleep(Duration::from_millis(50));
    }

    clear_timeout_cleanups();
    handle.join().unwrap();
}

type TimeoutCleanup = Box<dyn FnOnce() + Send + 'static>;

static TIMEOUT_CLEANUPS: OnceLock<Mutex<Vec<TimeoutCleanup>>> = OnceLock::new();

/// Register a best-effort cleanup callback to run if the test times out.
///
/// Callbacks run in the timing thread (not the test worker thread), and are
/// intended for emergency cleanup like killing child processes.
pub(crate) fn register_timeout_cleanup(cleanup: TimeoutCleanup) {
    let cleanups = TIMEOUT_CLEANUPS.get_or_init(|| Mutex::new(Vec::new()));
    let mut guard = cleanups
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    guard.push(cleanup);
}

fn run_timeout_cleanups() {
    let Some(cleanups) = TIMEOUT_CLEANUPS.get() else {
        return;
    };
    let mut guard = cleanups
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    for cleanup in guard.drain(..) {
        cleanup();
    }
}

fn clear_timeout_cleanups() {
    let Some(cleanups) = TIMEOUT_CLEANUPS.get() else {
        return;
    };
    let mut guard = cleanups
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    guard.clear();
}

/// Run a closure with the default 30-second timeout.
///
/// This is a convenience wrapper around `with_timeout` that uses
/// the standard system test timeout.
pub(crate) fn with_default_timeout<F>(f: F)
where
    F: FnOnce() + Send + 'static,
{
    with_timeout(f, DEFAULT_TIMEOUT);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_with_timeout_success() {
        with_timeout(
            || {
                assert_eq!(2 + 2, 4);
            },
            Duration::from_secs(1),
        );
    }

    #[test]
    fn test_with_default_timeout_success() {
        with_default_timeout(|| {
            assert_eq!(2 + 2, 4);
        });
    }
}
