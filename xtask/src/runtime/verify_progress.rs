use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use super::types::CheckStatus;

/// Observer interface for verification progress.
///
/// Implementors receive callbacks as each check starts and finishes.
/// The trait is `Send + Sync` so it can be shared across threads and stored in `Arc`.
pub trait ProgressReporter: Send + Sync {
    fn check_started(&self, name: &str);
    /// Called when a check passes; `elapsed` is the wall-clock duration of the check.
    fn check_passed(&self, name: &str, elapsed: Duration);
    /// Called when a check fails; `elapsed` is the wall-clock duration of the check.
    fn check_failed(&self, name: &str, elapsed: Duration, status: CheckStatus);
    /// Called periodically while a long-running check is still in progress.
    /// Default is a no-op so existing implementations compile unchanged.
    fn check_still_running(&self, _name: &str, _elapsed: Duration) {}
    /// Called with incremental status info during a long-running check
    /// (e.g., "Compiling foo" lines forwarded from cargo, or per-file scan counts).
    /// Default is a no-op so existing test fakes need no changes.
    fn check_progress(&self, _name: &str, _info: &str) {}
    /// Called for informational verify messages that must not count as diagnostics.
    fn info(&self, _message: &str) {}
    /// Called when a parallel lane finishes; `elapsed` is the total wall-clock time.
    /// Default is a no-op so existing implementations compile unchanged.
    fn lane_finished(&self, _lane_name: &str, _elapsed: Duration) {}
}

/// No-op implementation used in tests.
#[cfg(test)]
pub struct NoopProgressReporter;

#[cfg(test)]
impl ProgressReporter for NoopProgressReporter {
    fn check_started(&self, _name: &str) {}
    fn check_passed(&self, _name: &str, _elapsed: Duration) {}
    fn check_failed(&self, _name: &str, _elapsed: Duration, _status: CheckStatus) {}
}

/// Progress reporter that prints check names to stderr in real time.
///
/// Output format:
///   [N/total] checking: <name>
///   done:     <name> (<elapsed>)
///   FAILED:   <name> (<elapsed>, Error|Warning)
///   still running: <name> (<elapsed>)...  ← printed every 3 s for slow checks
///   progress: <name>: <info>              ← forwarded cargo/scan progress lines
///
/// Stderr is used so stdout can be piped without interference.
pub struct StderrProgressReporter {
    counter: AtomicUsize,
    total: usize,
}

impl StderrProgressReporter {
    pub fn new(total: usize) -> Self {
        Self {
            counter: AtomicUsize::new(0),
            total,
        }
    }

    pub(crate) fn fmt_check_started(n: usize, total: usize, name: &str) -> String {
        format!("  [{n}/{total}] checking: {name}")
    }

    pub(crate) fn fmt_check_passed(name: &str, elapsed: Duration) -> String {
        format!("  done:     {name} ({elapsed:.1?})")
    }

    pub(crate) fn fmt_check_failed(name: &str, elapsed: Duration, status: CheckStatus) -> String {
        format!("  FAILED:   {name} ({elapsed:.1?}, {status:?})")
    }

    pub(crate) fn fmt_still_running(name: &str, elapsed: Duration) -> String {
        format!("  still running: {name} ({elapsed:.0?})...")
    }

    pub(crate) fn fmt_progress(name: &str, info: &str) -> String {
        format!("  progress: {name}: {info}")
    }

    pub(crate) fn fmt_lane_finished(lane_name: &str, elapsed: Duration) -> String {
        format!("  lane done: {lane_name} ({elapsed:.1?})")
    }
}

impl ProgressReporter for StderrProgressReporter {
    fn check_started(&self, name: &str) {
        let n = self.counter.fetch_add(1, Ordering::Relaxed) + 1;
        eprintln!("{}", Self::fmt_check_started(n, self.total, name));
    }
    fn check_passed(&self, name: &str, elapsed: Duration) {
        eprintln!("{}", Self::fmt_check_passed(name, elapsed));
    }
    fn check_failed(&self, name: &str, elapsed: Duration, status: CheckStatus) {
        eprintln!("{}", Self::fmt_check_failed(name, elapsed, status));
    }
    fn check_still_running(&self, name: &str, elapsed: Duration) {
        eprintln!("{}", Self::fmt_still_running(name, elapsed));
    }
    fn check_progress(&self, name: &str, info: &str) {
        eprintln!("{}", Self::fmt_progress(name, info));
    }
    fn info(&self, message: &str) {
        eprintln!("{message}");
    }
    fn lane_finished(&self, lane_name: &str, elapsed: Duration) {
        eprintln!("{}", Self::fmt_lane_finished(lane_name, elapsed));
    }
}
