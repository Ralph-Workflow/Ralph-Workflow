//! Sleep utilities for the idle-timeout monitor.

use std::sync::atomic::Ordering;
use std::time::Duration;

/// Policy decision for one sleep-step iteration.
#[derive(Debug, Clone, Copy)]
enum SleepStepPolicy {
    /// Stop sleeping — return the given value.
    Stop(bool),
    /// Continue sleeping for this duration.
    Continue { slice: Duration },
}

/// Pure: determine the policy for this sleep-step iteration.
///
/// Takes the raw inputs and returns a policy decision, keeping all branching
/// logic in the pure layer so the boundary stays thin.
fn compute_sleep_policy(
    should_stop: bool,
    now: std::time::Instant,
    deadline: std::time::Instant,
    poll_interval: Duration,
) -> SleepStepPolicy {
    if should_stop {
        SleepStepPolicy::Stop(true)
    } else if now >= deadline {
        SleepStepPolicy::Stop(false)
    } else {
        let remaining = deadline.saturating_duration_since(now);
        let slice = poll_interval.min(remaining);
        SleepStepPolicy::Continue { slice }
    }
}

/// One iteration of the sleep-poll loop.
///
/// Gathers inputs, calls pure policy, executes the sleep effect.
/// Returns `Some(stopped)` to exit the loop, `None` to continue polling.
fn sleep_poll_step(
    should_stop: &std::sync::atomic::AtomicBool,
    deadline: std::time::Instant,
    poll_interval: Duration,
) -> Option<bool> {
    let stop_flag = should_stop.load(Ordering::Acquire);
    let now = std::time::Instant::now();
    match compute_sleep_policy(stop_flag, now, deadline, poll_interval) {
        SleepStepPolicy::Stop(ret) => Some(ret),
        SleepStepPolicy::Continue { slice } => {
            std::thread::sleep(slice);
            None
        }
    }
}

/// Sleep until the next check interval or until should_stop is set.
/// Returns `true` if should_stop was set, `false` if the check interval elapsed.
///
/// Polls `should_stop` every `poll_interval` (capped at 100ms) and sleeps
/// between polls so the thread does not busy-wait.
///
/// Thin boundary: delegates each iteration to [`sleep_poll_step`].
pub fn sleep_until_next_check_or_stop(
    should_stop: &std::sync::atomic::AtomicBool,
    check_interval: Duration,
) -> bool {
    let poll_interval = check_interval.min(Duration::from_millis(100));
    let deadline = std::time::Instant::now() + check_interval;
    loop {
        if let Some(result) = sleep_poll_step(should_stop, deadline, poll_interval) {
            return result;
        }
    }
}
