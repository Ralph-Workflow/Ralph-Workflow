//! Idle timeout detection for agent subprocess execution.
//!
//! This module provides infrastructure to detect when an agent subprocess
//! has stopped producing output, indicating it may be stuck (e.g., waiting
//! for user input in unattended mode).
//!
//! # Design
//!
//! The idle timeout system tracks two types of activity to detect whether an
//! agent is making progress:
//!
//! 1. **Output Activity**: A shared atomic timestamp gets updated whenever
//!    data is read from subprocess stdout OR stderr.
//! 2. **File Activity**: A tracker monitors AI-generated files in `.agent/`
//!    (PLAN.md, ISSUES.md, NOTES.md, commit-message.txt, .agent/tmp/*.xml)
//!    to detect file updates that indicate ongoing work.
//!
//! A monitor thread periodically checks both signals (by default every 30 seconds)
//! and kills the subprocess only if BOTH output and file activity have been idle
//! for longer than the configured timeout (300 seconds).
//!
//! Both stdout and stderr activity are tracked because some agents (e.g., opencode
//! with `--print-logs`) output verbose progress information to stderr while
//! processing, and only produce stdout when complete. Without tracking stderr,
//! such agents would be incorrectly killed as idle.
//!
//! File activity tracking prevents false timeouts when agents produce sparse
//! output but are actively writing files, which is common during planning,
//! commit message generation, and other file-intensive phases.
//!
//! 3. **Child-Process Activity**: Descendant processes only suppress an idle
//!    timeout when the current idle spell contains fresh cross-poll evidence of
//!    relevant child work. A first active snapshot earns a one-poll startup
//!    grace so new subprocess work can be confirmed, but continued suppression
//!    requires the same descendant subtree to keep advancing. Mere descendant
//!    existence, historical CPU usage, or an "active-looking" snapshot that no
//!    longer changes is not enough. If child work goes stale, exits, detaches,
//!    or is replaced without showing fresh current activity, the monitor resumes
//!    normal idle-timeout enforcement.
//!
//! # Timeout Value
//!
//! The default timeout is 5 minutes (300 seconds), which is:
//! - Long enough for complex tool operations and LLM reasoning
//! - Short enough to detect truly stuck agents
//! - Aligned with typical CI/CD step timeouts
//!
//! # Edge Cases and Behavior Notes
//!
//! ## Sparse File Updates
//! If an agent updates files very infrequently (e.g., once every 4 minutes),
//! the timeout detection will correctly recognize this as ongoing activity.
//! The 300-second recency window ensures that any modification within the
//! last 5 minutes counts as "active".
//!
//! ## Monitoring Cadence
//! The default check interval is 30 seconds, meaning file activity is
//! sampled every 30 seconds. This is much faster than the 300-second timeout
//! window, ensuring timely detection while remaining resource-efficient.
//! The check interval can be adjusted via `MonitorConfig` for testing or
//! special operational requirements.
//!
//! ## Performance Characteristics
//! File activity checking uses targeted directory scanning:
//! - Only `.agent/` and `.agent/tmp/` are scanned (no workspace-wide scan)
//! - Excluded files (logs, system artifacts) are filtered early
//! - Impact on monitor overhead: typically <1ms per check on modern systems
//!
//! ## Timeout Reporting
//! When a timeout occurs, logs indicate whether it was due to lack of
//! output activity, file activity, or both. This helps users understand
//! whether the agent was truly stuck or if the timeout threshold needs
//! adjustment.
//! When child processes keep a run alive, observability also distinguishes
//! currently active child work from timeouts that happened while stalled
//! children were still present.

mod clock;
mod file_activity;
pub(crate) mod io;
mod readers;
mod runtime;

pub use clock::{
    is_idle_timeout_exceeded, new_activity_timestamp, new_file_activity_tracker,
    time_since_activity, touch_activity, SharedActivityTimestamp, SharedFileActivityTracker,
    IDLE_TIMEOUT_SECS,
};
#[cfg(test)]
pub use clock::{
    is_idle_timeout_exceeded_with_clock, new_activity_timestamp_with_clock,
    time_since_activity_with_clock, touch_activity_with_clock, Clock, MonotonicClock,
};
pub use file_activity::FileActivityTracker;
pub use readers::ActivityTrackingReader;
pub type KillConfig = self::io::KillConfig;
pub const DEFAULT_KILL_CONFIG: KillConfig = self::io::DEFAULT_KILL_CONFIG;

pub type SharedAgentChild = self::io::SharedAgentChild;
pub(crate) type SharedChildActivityObserver = self::io::SharedChildActivityObserver;

pub type FileActivityConfig = self::runtime::FileActivityConfig;
pub type MonitorConfig = self::runtime::MonitorConfig;
pub type MonitorResult = self::runtime::MonitorResult;

pub fn monitor_idle_timeout_with_interval(
    activity_timestamp: &SharedActivityTimestamp,
    child: &SharedAgentChild,
    timeout: std::time::Duration,
    should_stop: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    executor: &std::sync::Arc<dyn crate::executor::ProcessExecutor>,
    check_interval: std::time::Duration,
) -> MonitorResult {
    self::runtime::monitor_idle_timeout_with_interval(
        activity_timestamp,
        child,
        timeout,
        should_stop,
        executor,
        check_interval,
    )
}

pub fn monitor_idle_timeout_with_interval_and_kill_config(
    activity_timestamp: &SharedActivityTimestamp,
    file_activity: Option<&FileActivityConfig>,
    child: &SharedAgentChild,
    should_stop: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    executor: &std::sync::Arc<dyn crate::executor::ProcessExecutor>,
    config: MonitorConfig,
) -> MonitorResult {
    self::runtime::monitor_idle_timeout_with_interval_and_kill_config(
        activity_timestamp,
        file_activity,
        child,
        should_stop,
        executor,
        config,
    )
}

pub(crate) fn monitor_idle_timeout_with_interval_and_kill_config_and_observer(
    activity_timestamp: &SharedActivityTimestamp,
    file_activity: Option<&FileActivityConfig>,
    child: &SharedAgentChild,
    should_stop: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    executor: &std::sync::Arc<dyn crate::executor::ProcessExecutor>,
    monitor_config: MonitorConfig,
    child_activity_observer: Option<&SharedChildActivityObserver>,
) -> MonitorResult {
    self::runtime::monitor_idle_timeout_with_interval_and_kill_config_and_observer(
        activity_timestamp,
        file_activity,
        child,
        should_stop,
        executor,
        monitor_config,
        child_activity_observer,
    )
}

#[cfg(test)]
mod tests;
