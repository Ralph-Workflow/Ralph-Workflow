//! Sleep utilities for the idle-timeout monitor.

use std::sync::atomic::Ordering;
use std::time::Duration;

/// Compute the sleep duration for one poll interval.
/// Returns `None` if the deadline has already passed.
pub(crate) fn compute_sleep_slice(
    poll_interval: Duration,
    deadline: std::time::Instant,
) -> Option<Duration> {
    let now = std::time::Instant::now();
    if now >= deadline {
        return None;
    }
    let remaining = deadline.saturating_duration_since(now);
    Some(poll_interval.min(remaining))
}

/// Outcome of one sleep step.
pub(crate) enum SleepStepOutcome {
    /// should_stop was set; should stop polling.
    Stop,
    /// Deadline was reached; check should_stop before continuing.
    DeadlineReached,
    /// Slept for the poll interval; continue polling.
    Slept,
}

/// Pure: determine the outcome of one sleep step given current state.
pub(crate) fn sleep_step_outcome(
    should_stop: &std::sync::atomic::AtomicBool,
    poll_interval: Duration,
    deadline: std::time::Instant,
) -> SleepStepOutcome {
    // Check should_stop FIRST — if it is set, return Stop immediately,
    // even when the deadline has also been reached. This ensures that a
    // should_stop signal is never missed because we happened to hit the
    // deadline on the same iteration.
    if should_stop.load(Ordering::Acquire) {
        return SleepStepOutcome::Stop;
    }
    match compute_sleep_slice(poll_interval, deadline) {
        None => SleepStepOutcome::DeadlineReached,
        Some(_) => SleepStepOutcome::Slept,
    }
}

/// Pure: determine whether to stop when deadline is reached.
/// Encapsulates the policy decision for the DeadlineReached case.
pub(crate) fn deadline_reached_should_stop(should_stop: &std::sync::atomic::AtomicBool) -> bool {
    // Re-check should_stop when deadline is reached: if it was set
    // during the deadline window, honor it and return true (stop).
    should_stop.load(Ordering::Acquire)
}

/// Pure: process one sleep iteration, returning the decision.
/// Returns None to continue looping, Some(true) to stop because should_stop was set,
/// Some(false) to stop because deadline was reached.
fn process_sleep_iteration(
    should_stop: &std::sync::atomic::AtomicBool,
    poll_interval: Duration,
    deadline: std::time::Instant,
) -> Option<bool> {
    match sleep_step_outcome(should_stop, poll_interval, deadline) {
        SleepStepOutcome::Stop => Some(true),
        SleepStepOutcome::DeadlineReached => Some(deadline_reached_should_stop(should_stop)),
        SleepStepOutcome::Slept => None,
    }
}

/// Sleep until the next check interval or until should_stop is set.
/// Returns `true` if should_stop was set, `false` if the check interval elapsed.
pub(crate) fn sleep_until_next_check_or_stop(
    should_stop: &std::sync::atomic::AtomicBool,
    check_interval: Duration,
) -> bool {
    let poll_interval = check_interval.min(Duration::from_millis(100));
    let deadline = std::time::Instant::now() + check_interval;
    loop {
        if let Some(result) = process_sleep_iteration(should_stop, poll_interval, deadline) {
            return result;
        }
    }
}
