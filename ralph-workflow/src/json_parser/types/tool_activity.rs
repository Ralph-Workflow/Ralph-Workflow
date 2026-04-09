//! Shared tool-activity tracker for idle-timeout suppression.
//!
//! All three parsers (Claude, Codex, OpenCode) track active tool executions via
//! a shared `AtomicU32` counter that the idle-timeout monitor polls. This module
//! provides a single implementation of the increment/decrement/reset logic,
//! eliminating triplication and ensuring uniform semantics.

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

/// Tracks whether parser-observed tool executions are in progress.
///
/// Wraps an optional shared `AtomicU32` counter. When present, the counter is:
/// - **incremented** when a tool execution starts (e.g. `ToolUse` content block,
///   `ItemStarted`, or `tool_use` with status `"pending"`),
/// - **saturating-decremented** when a tool execution completes (e.g. `MessageStart`
///   delivering the tool result, `ItemCompleted`, or status `"completed"`/`"error"`),
/// - **hard-reset to 0** at stream end or turn boundaries as defense-in-depth.
///
/// The idle-timeout monitor reads the counter to suppress spurious kills during
/// long tool operations that produce no stdout.
pub(crate) struct ToolActivityTracker {
    inner: Option<Arc<AtomicU32>>,
}

impl ToolActivityTracker {
    /// Create a tracker with no backing counter (tool-activity tracking disabled).
    pub(crate) fn new() -> Self {
        Self { inner: None }
    }

    /// Create a tracker backed by the given shared counter.
    pub(crate) fn with_tracker(tracker: Arc<AtomicU32>) -> Self {
        Self {
            inner: Some(tracker),
        }
    }

    /// Increment the active-tool counter (a new tool execution started).
    pub(crate) fn set_active(&self) {
        if let Some(ref tracker) = self.inner {
            tracker
                .fetch_update(Ordering::Release, Ordering::Acquire, |n| {
                    Some(n.saturating_add(1))
                })
                .ok();
        }
    }

    /// Saturating-decrement the active-tool counter (a tool execution completed).
    pub(crate) fn clear_active(&self) {
        if let Some(ref tracker) = self.inner {
            tracker
                .fetch_update(Ordering::Release, Ordering::Acquire, |n| {
                    Some(n.saturating_sub(1))
                })
                .ok();
        }
    }

    /// Hard-reset the counter to 0 (stream end / turn boundary).
    ///
    /// Defense-in-depth: ensures the counter is 0 when the monitor checks after the
    /// stream closes, even if individual clear_active calls were missed.
    pub(crate) fn reset(&self) {
        if let Some(ref tracker) = self.inner {
            tracker.store(0, Ordering::Release);
        }
    }

    /// Returns `true` if at least one tool execution is currently in progress.
    #[cfg(test)]
    pub(crate) fn is_active(&self) -> bool {
        self.inner
            .as_ref()
            .is_some_and(|t| t.load(Ordering::Acquire) > 0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_tracker_operations_are_no_ops() {
        let tracker = ToolActivityTracker::new();
        // All operations should succeed silently with no backing counter.
        tracker.set_active();
        tracker.clear_active();
        tracker.reset();
        assert!(!tracker.is_active());
    }

    #[test]
    fn set_active_increments_and_clear_active_decrements() {
        let counter = Arc::new(AtomicU32::new(0));
        let tracker = ToolActivityTracker::with_tracker(Arc::clone(&counter));

        tracker.set_active();
        assert_eq!(counter.load(Ordering::Acquire), 1);
        assert!(tracker.is_active());

        tracker.set_active();
        assert_eq!(counter.load(Ordering::Acquire), 2);

        tracker.clear_active();
        assert_eq!(counter.load(Ordering::Acquire), 1);
        assert!(tracker.is_active());

        tracker.clear_active();
        assert_eq!(counter.load(Ordering::Acquire), 0);
        assert!(!tracker.is_active());
    }

    #[test]
    fn clear_active_saturates_at_zero() {
        let counter = Arc::new(AtomicU32::new(0));
        let tracker = ToolActivityTracker::with_tracker(Arc::clone(&counter));

        tracker.clear_active();
        assert_eq!(counter.load(Ordering::Acquire), 0);
    }

    #[test]
    fn reset_zeroes_counter() {
        let counter = Arc::new(AtomicU32::new(0));
        let tracker = ToolActivityTracker::with_tracker(Arc::clone(&counter));

        tracker.set_active();
        tracker.set_active();
        tracker.set_active();
        assert_eq!(counter.load(Ordering::Acquire), 3);

        tracker.reset();
        assert_eq!(counter.load(Ordering::Acquire), 0);
        assert!(!tracker.is_active());
    }

    #[test]
    fn concurrent_increment_decrement_correctness() {
        let counter = Arc::new(AtomicU32::new(0));
        let tracker = ToolActivityTracker::with_tracker(Arc::clone(&counter));

        // Simulate 3 concurrent tool calls starting, then 3 completing.
        tracker.set_active();
        tracker.set_active();
        tracker.set_active();
        assert_eq!(counter.load(Ordering::Acquire), 3);

        tracker.clear_active();
        tracker.clear_active();
        tracker.clear_active();
        assert_eq!(counter.load(Ordering::Acquire), 0);
    }

    #[test]
    fn set_active_saturates_at_u32_max() {
        let counter = Arc::new(AtomicU32::new(u32::MAX));
        let tracker = ToolActivityTracker::with_tracker(Arc::clone(&counter));

        // saturating_add(1) at u32::MAX must stay at u32::MAX, not wrap to 0.
        tracker.set_active();
        assert_eq!(counter.load(Ordering::Acquire), u32::MAX);
        assert!(tracker.is_active());
    }

    #[test]
    fn multi_threaded_concurrent_increment_decrement() {
        use std::thread;

        let counter = Arc::new(AtomicU32::new(0));
        let num_threads = 8;
        let ops_per_thread = 100;

        // Spawn threads that each increment then decrement ops_per_thread times.
        (0..num_threads)
            .map(|_| {
                let c = Arc::clone(&counter);
                thread::spawn(move || {
                    let t = ToolActivityTracker::with_tracker(c);
                    for _ in 0..ops_per_thread {
                        t.set_active();
                    }
                    for _ in 0..ops_per_thread {
                        t.clear_active();
                    }
                })
            })
            .for_each(|h| h.join().expect("thread panicked"));

        // Every increment has a matching decrement, so the final counter must be 0.
        assert_eq!(counter.load(Ordering::Acquire), 0);
    }
}
