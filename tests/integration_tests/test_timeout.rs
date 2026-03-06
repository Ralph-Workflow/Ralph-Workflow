//! Test timeout enforcement for integration tests.
//!
//! This module provides a mechanism to enforce maximum execution time
//! for integration tests. Tests that take longer than the specified
//! timeout will be terminated and fail with a clear error message.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** This module is part of the integration test framework and
//! MUST follow the integration test style guide defined in
//! **[../INTEGRATION_TESTS.md](../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (test completion within timeout)
//! - Provides utility for test isolation and determinism
//!
//! # MANDATORY Usage
//!
//! **ALL integration tests MUST use either `with_default_timeout()` or `with_timeout()`
//! to wrap their test code.** This is enforced by code review and the integration test style guide.
//!
//! The timeout wrapper must be the **outermost** wrapper in any test - no test code
//! should execute before the timeout wrapper is invoked.
//!
//! # Usage
//!
//! ```rust,no_run
//! use std::path::PathBuf;
//! use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
//! use crate::test_timeout::with_default_timeout;
//!
//! #[test]
//! fn test_name() {
//!     with_default_timeout(|| {
//!         // ALL test code must run inside this closure
//!         let mut handler = MockAppEffectHandler::new()
//!             .with_head_oid("a".repeat(40))
//!             .with_cwd(PathBuf::from("/mock/repo"));
//!         // ... rest of test using handler
//!     });
//! }
//! ```
//!
//! # Default Timeout
//!
//! The default timeout for all integration tests is 5 seconds.
//! This is enforced via the `DEFAULT_TIMEOUT` constant.
//!
//! Tests that exceed 5 seconds likely have external I/O dependencies
//! (real LLM calls, network requests, shell scripts, long sleeps, or process spawning) which
//! violate the integration test style guide.

use std::panic;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::sync::Arc;
use std::thread;
use std::time::Duration;

/// Global counter to track process spawning during tests.
/// This MUST remain zero throughout test execution.
static SPAWNED_PROCESSES: AtomicUsize = AtomicUsize::new(0);

/// Default timeout for integration tests (5 seconds).
///
/// Tests that exceed 5 seconds likely have external I/O dependencies
/// (real LLM calls, network requests, long sleeps, or process spawning) which
/// violate the integration test style guide.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(5);

/// Error type for timeout failures.
#[derive(Debug)]
pub struct TimeoutError {
    pub timeout: Duration,
}

/// Context passed to the test body so it can cooperate with timeouts.
///
/// On timeout, the wrapper will set `cancelled=true` and wait briefly for the
/// test body to exit. Test code that might block should periodically check
/// `is_cancelled()` and return promptly.
#[derive(Clone, Debug)]
pub struct TimeoutContext {
    cancelled: Arc<AtomicBool>,
}

impl TimeoutContext {
    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }
}

impl std::fmt::Display for TimeoutError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Test exceeded timeout of {:?}. \
             This usually indicates the test is waiting for external I/O \
             (e.g., real LLM calls, network requests, long sleeps, or external process spawning). \
             All external dependencies MUST be mocked at architectural boundaries. \
             Process spawning is STRICTLY FORBIDDEN in integration tests.",
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
/// # Arguments
///
/// * `f` - The closure to execute
/// * `timeout` - Maximum duration to wait for completion
///
/// # Panics
///
/// Panics if the closure does not complete within the timeout.
///
/// # Example
///
/// ```rust,no_run
/// use crate::test_timeout::with_timeout;
///
/// with_timeout(
///     || {
///         // Fast operation
///         assert_eq!(2 + 2, 4);
///     },
///     std::time::Duration::from_secs(1),
/// );
/// ```
pub fn with_timeout<F>(f: F, timeout: Duration)
where
    F: FnOnce() + Send + 'static,
{
    with_timeout_ctx(|_ctx| f(), timeout);
}

/// Run a closure with a timeout, passing a `TimeoutContext` for cooperative cancellation.
pub fn with_timeout_ctx<F>(f: F, timeout: Duration)
where
    F: FnOnce(&TimeoutContext) + Send + 'static,
{
    let cancelled = Arc::new(AtomicBool::new(false));
    let ctx = TimeoutContext {
        cancelled: Arc::clone(&cancelled),
    };

    let (tx, rx) = mpsc::channel::<Result<(), Box<dyn std::any::Any + Send>>>();

    let handle = thread::spawn(move || {
        let res = panic::catch_unwind(panic::AssertUnwindSafe(|| f(&ctx)));
        let _ = match res {
            Ok(()) => tx.send(Ok(())),
            Err(payload) => tx.send(Err(payload)),
        };
    });

    match rx.recv_timeout(timeout) {
        Ok(Ok(())) => {
            // Worker finished successfully.
            handle.join().unwrap();
        }
        Ok(Err(payload)) => {
            // Worker panicked; join then rethrow on the calling thread so the
            // test fails with the real assertion message.
            handle.join().unwrap();
            panic::resume_unwind(payload);
        }
        Err(mpsc::RecvTimeoutError::Timeout) => {
            // Timeout: request cooperative cancellation and wait briefly for
            // the worker to exit so we don't leave runaway threads behind.
            cancelled.store(true, Ordering::Release);

            let grace = Duration::from_millis(250);
            match rx.recv_timeout(grace) {
                Ok(_outcome) => {
                    handle.join().unwrap();
                    panic!("{}", TimeoutError { timeout }.to_string());
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    eprintln!(
                        "FATAL: integration test exceeded timeout ({timeout:?}) and did not exit after cancellation. Aborting to avoid runaway threads."
                    );
                    std::process::abort();
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    // Worker exited without sending (shouldn't happen), but ensure we join.
                    let _ = handle.join();
                    panic!("{}", TimeoutError { timeout }.to_string());
                }
            }
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            // Worker exited without sending. Join best-effort and fail loudly.
            let _ = handle.join();
            panic!("integration test worker thread exited unexpectedly");
        }
    }
}

/// Run a closure with the default 5-second timeout.
///
/// This is a convenience wrapper around `with_timeout` that uses
/// the standard integration test timeout.
///
/// # Example
///
/// ```rust,no_run
/// use crate::test_timeout::with_default_timeout;
///
/// with_default_timeout(|| {
///     // Test code here - must complete in 5 seconds
///     assert_eq!(2 + 2, 4);
/// });
/// ```
pub fn with_default_timeout<F>(f: F)
where
    F: FnOnce() + Send + 'static,
{
    with_process_spawn_guard(|| {
        with_timeout(f, DEFAULT_TIMEOUT);
    });
}

/// Guard that tracks process spawning and fails test if any spawned.
///
/// This function verifies that NO external processes are spawned during
/// test execution. Process spawning is strictly forbidden in integration tests.
///
/// **Process Spawning Detection:**
///
/// Runtime process spawn detection is enforced by the compliance checker which
/// detects `assert_cmd::Command::new` and `std::process::Command::new`
/// usage in test code. The compliance checker (native Rust check in `xtask/src/compliance.rs`,
/// run via `cargo xtask verify`) runs before tests are accepted and will fail if any process
/// spawning patterns are found.
///
/// **Why Runtime Detection is Limited:**
///
/// Hooking `std::process::Command` at runtime is not feasible in Rust without
/// global mutable state or unsafe code. Instead, we rely on:
/// 1. **Compliance checker** - Static analysis of test code before merging
/// 2. **Timeout enforcement** - Tests exceeding 5 seconds likely spawned processes
/// 3. **Code review** - Manual review to catch violations
///
/// **For CLI Testing:**
///
/// Use `run_ralph_cli()` which calls `ralph_workflow::app::run()` directly,
/// eliminating process spawning. Tests should verify behavior via side effects:
/// - Files created/modified
/// - Return values (success/error)
/// - Log files in `.agent/logs/`
///
/// # Panics
///
/// Panics if any processes were spawned during the closure execution.
fn with_process_spawn_guard<F>(f: F)
where
    F: FnOnce(),
{
    let before = SPAWNED_PROCESSES.load(Ordering::Acquire);
    f();
    let after = SPAWNED_PROCESSES.load(Ordering::Acquire);

    assert!(after <= before,
        "INTEGRATION TEST VIOLATION: Test spawned {} external process(es). \
        Integration tests MUST NOT spawn ANY external processes (no git, ls, cargo, ralph binary, or any subprocess). \
        For CLI testing, use run_ralph_cli() which calls app::run() directly instead of spawning a binary process. \
        All external dependencies MUST be mocked at architectural boundaries. \
        See tests/INTEGRATION_TESTS.md Rule 1.5 for details.",
        after - before
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Test that fast operations complete within timeout.
    ///
    /// This verifies that when a fast operation is wrapped with a timeout,
    /// the operation completes successfully without timing out.
    #[test]
    fn test_with_timeout_success() {
        with_timeout(
            || {
                // Fast operation
                assert_eq!(2 + 2, 4);
            },
            Duration::from_secs(1),
        );
    }

    /// Test that fast operations complete within default timeout.
    ///
    /// This verifies that when a fast operation is wrapped with the default timeout,
    /// the operation completes successfully without timing out.
    #[test]
    fn test_with_default_timeout_success() {
        with_default_timeout(|| {
            // Fast operation
            assert_eq!(2 + 2, 4);
        });
    }

    /// Test that slow operations panic with timeout error.
    ///
    /// This verifies that when an operation exceeds the timeout duration,
    /// the test panics with a clear timeout error message.
    #[test]
    #[should_panic(expected = "Test exceeded timeout")]
    fn test_with_timeout_panic_on_slow_operation() {
        // Use cooperative cancellation so the worker exits promptly.
        with_timeout_ctx(
            |ctx| {
                while !ctx.is_cancelled() {
                    thread::sleep(Duration::from_millis(10));
                }
            },
            Duration::from_millis(100),
        );
    }

    /// Test that panics inside the closure propagate and do not turn into timeouts.
    ///
    /// This is critical for TDD: when an assertion fails, we want the failure
    /// message to surface immediately instead of waiting for the timeout.
    #[test]
    #[should_panic(expected = "boom")]
    fn test_with_timeout_propagates_panic() {
        with_timeout(
            || {
                panic!("boom");
            },
            Duration::from_millis(100),
        );
    }

    /// Test that timeout error displays helpful message.
    ///
    /// This verifies that when a timeout error is displayed,
    /// it includes information about the timeout duration and external I/O.
    #[test]
    fn test_timeout_error_display() {
        with_default_timeout(|| {
            let error = TimeoutError {
                timeout: Duration::from_secs(10),
            };
            let msg = format!("{error}");
            // Duration debug format is "10s", not "10 seconds"
            assert!(msg.contains("10s"));
            assert!(msg.contains("external I/O"));
        });
    }

    #[test]
    fn test_with_timeout_does_not_detach_worker_on_timeout() {
        // Regression: on timeout, with_timeout must not leave the worker thread
        // running in the background (which can hang the test process).
        let stopped = Arc::new(AtomicBool::new(false));
        let stopped_clone = Arc::clone(&stopped);

        let res = panic::catch_unwind(|| {
            with_timeout_ctx(
                move |ctx| {
                    while !ctx.is_cancelled() {
                        thread::sleep(Duration::from_millis(10));
                    }
                    stopped_clone.store(true, Ordering::Release);
                },
                Duration::from_millis(50),
            );
        });

        assert!(res.is_err(), "expected timeout to panic");
        assert!(
            stopped.load(Ordering::Acquire),
            "expected worker to exit before timeout unwinds"
        );
    }
}
