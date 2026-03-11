//! Idle-timeout monitor thread.

use super::kill::{
    force_kill_best_effort, kill_process, KillConfig, KillResult, DEFAULT_KILL_CONFIG,
};
use super::{is_idle_timeout_exceeded, SharedActivityTimestamp, SharedFileActivityTracker};
use crate::executor::{AgentChild, ProcessExecutor};
use crate::workspace::Workspace;
use std::sync::Arc;
use std::time::Duration;

/// Configuration for file activity monitoring during timeout detection.
///
/// When provided, the monitor will check for recent AI-generated file updates
/// in addition to stdout/stderr activity.
pub struct FileActivityConfig {
    /// Shared file activity tracker.
    pub tracker: SharedFileActivityTracker,
    /// Workspace for reading file metadata.
    pub workspace: Arc<dyn Workspace>,
}

/// Configuration for the idle timeout monitor.
#[derive(Debug, Clone, Copy)]
pub struct MonitorConfig {
    /// Timeout duration.
    pub timeout: Duration,
    /// Check interval for the monitor loop.
    pub check_interval: Duration,
    /// Kill configuration for process termination.
    pub kill_config: KillConfig,
    /// Number of consecutive idle observations required before killing the process.
    ///
    /// Requiring more than one confirmation prevents false kills when the agent is
    /// transiently quiet (e.g., waiting for an LLM API response, running a slow
    /// compilation, or transitioning between work phases). Each additional
    /// confirmation adds one `check_interval` of grace time before enforcement.
    ///
    /// Default: 2 (one extra `check_interval` of confirmation before kill).
    pub required_idle_confirmations: u32,
    /// Whether to check for active child processes before declaring the agent idle.
    ///
    /// When `true` (the default), the monitor queries the `ProcessExecutor` for
    /// active child processes of the agent. If any are found the idle counter is
    /// reset, preventing false kills when the agent is running a long subprocess
    /// (e.g. `cargo test`, `npm install`, `cargo build`).
    ///
    /// Set to `false` in tests that deliberately do not want this check to run.
    pub check_child_processes: bool,
}

impl Default for MonitorConfig {
    fn default() -> Self {
        Self {
            timeout: Duration::from_secs(super::IDLE_TIMEOUT_SECS),
            check_interval: DEFAULT_CHECK_INTERVAL,
            kill_config: DEFAULT_KILL_CONFIG,
            required_idle_confirmations: 2,
            check_child_processes: true,
        }
    }
}

/// Result of idle timeout monitoring.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MonitorResult {
    /// Process completed normally (not killed by monitor).
    ProcessCompleted,
    /// Idle timeout was exceeded and termination was initiated.
    ///
    /// In the common case the subprocess exits promptly after SIGTERM/SIGKILL,
    /// and by the time this result is returned the process is already gone.
    ///
    /// In pathological cases (e.g. a stuck/unresponsive subprocess or one that
    /// does not terminate even after repeated SIGKILL attempts), the monitor may
    /// return `TimedOut` after a bounded enforcement window so the pipeline can
    /// regain control. When that happens, a background reaper continues best-effort
    /// SIGKILL attempts until the process is observed dead.
    ///
    /// The `escalated` flag indicates whether SIGKILL/taskkill was required:
    /// - `false`: Process terminated after SIGTERM within grace period
    /// - `true`: Process did not respond to SIGTERM, required SIGKILL/taskkill
    TimedOut { escalated: bool },
}

/// Default check interval for the idle monitor (30 seconds).
const DEFAULT_CHECK_INTERVAL: Duration = Duration::from_secs(30);

fn sleep_until_next_check_or_stop(
    should_stop: &std::sync::atomic::AtomicBool,
    check_interval: Duration,
) -> bool {
    use std::cmp;
    use std::sync::atomic::Ordering;

    let poll_interval = cmp::min(check_interval, Duration::from_millis(100));
    let deadline = std::time::Instant::now() + check_interval;

    loop {
        if should_stop.load(Ordering::Acquire) {
            return true;
        }

        let now = std::time::Instant::now();
        if now >= deadline {
            return false;
        }

        let remaining = deadline.saturating_duration_since(now);
        std::thread::sleep(cmp::min(poll_interval, remaining));
    }
}

/// Monitors activity and kills a process if idle timeout is exceeded.
pub fn monitor_idle_timeout(
    activity_timestamp: &SharedActivityTimestamp,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    timeout: Duration,
    should_stop: &Arc<std::sync::atomic::AtomicBool>,
    executor: &Arc<dyn ProcessExecutor>,
) -> MonitorResult {
    monitor_idle_timeout_with_interval_and_kill_config(
        activity_timestamp,
        None, // No file activity config
        child,
        should_stop,
        executor,
        MonitorConfig {
            timeout,
            check_interval: DEFAULT_CHECK_INTERVAL,
            kill_config: DEFAULT_KILL_CONFIG,
            ..Default::default()
        },
    )
}

/// Like [`monitor_idle_timeout`] but with a configurable check interval.
pub fn monitor_idle_timeout_with_interval(
    activity_timestamp: &SharedActivityTimestamp,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    timeout: Duration,
    should_stop: &Arc<std::sync::atomic::AtomicBool>,
    executor: &Arc<dyn ProcessExecutor>,
    check_interval: Duration,
) -> MonitorResult {
    monitor_idle_timeout_with_interval_and_kill_config(
        activity_timestamp,
        None, // No file activity config
        child,
        should_stop,
        executor,
        MonitorConfig {
            timeout,
            check_interval,
            kill_config: DEFAULT_KILL_CONFIG,
            ..Default::default()
        },
    )
}

/// # Panics
///
/// May panic if internal synchronization primitives (mutex, atomic) are in an invalid state.
pub fn monitor_idle_timeout_with_interval_and_kill_config(
    activity_timestamp: &SharedActivityTimestamp,
    file_activity_config: Option<&FileActivityConfig>,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    should_stop: &Arc<std::sync::atomic::AtomicBool>,
    executor: &Arc<dyn ProcessExecutor>,
    config: MonitorConfig,
) -> MonitorResult {
    use std::sync::atomic::Ordering;

    #[derive(Debug, Clone, Copy)]
    struct TimeoutEnforcementState {
        pid: u32,
        escalated: bool,
        last_sigkill_sent_at: Option<std::time::Instant>,
        triggered_at: std::time::Instant,
    }

    let timeout = config.timeout;
    let check_interval = config.check_interval;
    let kill_config = config.kill_config;
    let required_idle_confirmations = config.required_idle_confirmations;
    let check_child_processes = config.check_child_processes;

    let mut timeout_triggered: Option<TimeoutEnforcementState> = None;
    let mut last_file_activity: Option<std::time::Instant> = None;
    let mut consecutive_idle_count: u32 = 0;
    let mut last_child_cpu_time_ms: Option<u64> = None;

    loop {
        // Fast-path teardown: if the process completed and we have not already
        // triggered idle-timeout enforcement, stop immediately.
        if timeout_triggered.is_none() && should_stop.load(Ordering::Acquire) {
            return MonitorResult::ProcessCompleted;
        }

        if timeout_triggered.is_none()
            && sleep_until_next_check_or_stop(should_stop.as_ref(), check_interval)
        {
            return MonitorResult::ProcessCompleted;
        }

        if let Some(mut state) = timeout_triggered.take() {
            let status = {
                let mut locked_child = child
                    .lock()
                    .expect("child process mutex poisoned - indicates panic in another thread");
                locked_child.try_wait()
            };

            if let Ok(Some(_)) = status {
                return MonitorResult::TimedOut {
                    escalated: state.escalated,
                };
            }

            let now = std::time::Instant::now();

            // Be robust to future changes: if we ever enter the enforcement state
            // without having escalated yet, force escalation now.
            if state.escalated {
                let should_resend = state
                    .last_sigkill_sent_at
                    .is_none_or(|t| now.duration_since(t) >= kill_config.sigkill_resend_interval());
                if should_resend {
                    let _ = force_kill_best_effort(state.pid, executor.as_ref());
                    state.last_sigkill_sent_at = Some(now);
                }
            } else {
                let _ = force_kill_best_effort(state.pid, executor.as_ref());
                state.escalated = true;
                state.last_sigkill_sent_at = Some(now);
            }

            // After a bounded enforcement window, return TimedOut so the
            // main pipeline can regain control. A detached reaper keeps
            // trying to kill until the process is observed dead.
            if now.duration_since(state.triggered_at) >= kill_config.post_sigkill_hard_cap()
                && state.escalated
            {
                let child_for_reaper = Arc::clone(child);
                let executor_for_reaper = Arc::clone(executor);
                let should_stop_for_reaper = Arc::clone(should_stop);
                let config_for_reaper = kill_config;
                let pid = state.pid;
                std::thread::spawn(move || {
                    // Bound the reaper's lifetime to avoid leaking threads across
                    // repeated timeouts. If the process is truly unkillable, a bounded
                    // best-effort reaper is the least-bad option.
                    let deadline =
                        std::time::Instant::now() + config_for_reaper.post_sigkill_hard_cap();
                    let mut last_kill_sent_at: Option<std::time::Instant> = None;

                    while std::time::Instant::now() < deadline {
                        if should_stop_for_reaper.load(Ordering::Acquire) {
                            return;
                        }

                        let status = {
                            let mut locked_child = child_for_reaper.lock().expect(
                                "child process mutex poisoned - indicates panic in another thread",
                            );
                            locked_child.try_wait()
                        };

                        if let Ok(Some(_)) = status {
                            return;
                        }
                        let now = std::time::Instant::now();
                        let should_resend = last_kill_sent_at.is_none_or(|t| {
                            now.duration_since(t) >= config_for_reaper.sigkill_resend_interval()
                        });
                        if should_resend {
                            let _ = force_kill_best_effort(pid, executor_for_reaper.as_ref());
                            last_kill_sent_at = Some(now);
                        }
                        std::thread::sleep(config_for_reaper.poll_interval());
                    }
                });

                return MonitorResult::TimedOut {
                    escalated: state.escalated,
                };
            }

            timeout_triggered = Some(state);
            continue;
        }

        if !is_idle_timeout_exceeded(activity_timestamp, timeout) {
            consecutive_idle_count = 0;
            continue;
        }

        // Log diagnostic information about timeout trigger
        let time_since_output = super::time_since_activity(activity_timestamp);
        eprintln!(
            "Idle timeout exceeded: no output activity for {} seconds",
            time_since_output.as_secs()
        );

        // Check file activity if config provided
        if let Some(config) = file_activity_config {
            // Fast path: if we confirmed file activity recently (monotonic clock),
            // skip an expensive filesystem re-scan. This prevents the multi-iteration
            // false positive where the same file falls outside the window on the next
            // check because check_interval has elapsed.
            if last_file_activity.is_some_and(|t| t.elapsed() < timeout) {
                consecutive_idle_count = 0;
                eprintln!(
                    "Continuing monitoring: file activity was confirmed within the last timeout window"
                );
                continue;
            }

            // Widen the scan window to cover check_interval jitter plus scan overhead:
            //   - A file written just before output stopped will be ~(timeout + check_interval)
            //     old when the monitor first fires.
            //   - The file scan itself takes time, so `actual_idle` computed before the scan
            //     is slightly smaller than the true elapsed time at comparison; adding
            //     `scan_overhead_buffer` compensates for that.
            //   - `cap` bounds the maximum window so that after `last_file_activity` expires
            //     and we re-scan, old files written long before output stopped do not
            //     indefinitely prevent a correct kill.
            let scan_overhead_buffer = Duration::from_secs(1);
            let cap = timeout + check_interval + scan_overhead_buffer;
            let actual_idle = super::time_since_activity(activity_timestamp);
            let file_window = (actual_idle + scan_overhead_buffer).min(cap);

            let locked_tracker = config
                .tracker
                .lock()
                .expect("file activity tracker mutex poisoned - indicates panic in another thread");

            match locked_tracker.check_for_recent_activity(config.workspace.as_ref(), file_window) {
                Ok(true) => {
                    consecutive_idle_count = 0;
                    last_file_activity = Some(std::time::Instant::now());
                    eprintln!("AI-generated files were updated recently, continuing monitoring");
                    continue;
                }
                Ok(false) => {
                    eprintln!(
                        "No AI-generated file updates in the last {file_window:?}, proceeding with timeout"
                    );
                }
                Err(e) => {
                    eprintln!(
                        "Warning: file activity check failed (treating as no recent file activity, proceeding with timeout enforcement): {e}"
                    );
                }
            }
        }

        // Re-check output timestamp: the agent may have produced output during the
        // file scan. This closes the race window between "scan said no activity" and
        // "kill is sent".
        if !is_idle_timeout_exceeded(activity_timestamp, timeout) {
            consecutive_idle_count = 0;
            eprintln!("Output activity detected after file scan; continuing monitoring");
            continue;
        }

        // Check for active child processes: the agent may have spawned a subprocess
        // (e.g. cargo test, cargo build, npm install) that is still running even
        // though there is no stdout/stderr output and no file-system activity in
        // tracked locations. Children only suppress the idle counter when their
        // cumulative CPU time is advancing between checks — mere existence of
        // child processes (e.g. zombies, idle daemons) is not sufficient.
        if check_child_processes {
            let child_pid = {
                let locked_child = child.lock().expect("child process mutex poisoned");
                locked_child.id()
            };
            let info = executor.get_child_process_info(child_pid);
            if info.has_children() {
                let cpu_advanced =
                    last_child_cpu_time_ms.is_none_or(|prev| info.cpu_time_ms > prev);
                last_child_cpu_time_ms = Some(info.cpu_time_ms);

                if cpu_advanced {
                    consecutive_idle_count = 0;
                    eprintln!(
                        "Agent has active child processes (pid {child_pid}, \
                         {} children, cpu {}ms); continuing monitoring",
                        info.child_count, info.cpu_time_ms
                    );
                    continue;
                }
                // Children exist but CPU hasn't advanced — treat as idle.
                eprintln!(
                    "Agent has child processes (pid {child_pid}, {} children) \
                     but CPU time unchanged ({}ms); treating as idle",
                    info.child_count, info.cpu_time_ms
                );
            } else {
                last_child_cpu_time_ms = None;
            }
        }

        // Require multiple consecutive idle observations before killing to avoid
        // false positives during transient quiet periods (LLM API waits, slow
        // compilations, transitions between work phases, etc.).
        consecutive_idle_count += 1;
        if consecutive_idle_count < required_idle_confirmations {
            eprintln!(
                "Idle confirmed {consecutive_idle_count}/{required_idle_confirmations} times; waiting for next check interval before kill"
            );
            continue;
        }

        let child_id = {
            let mut locked_child = child
                .lock()
                .expect("child process mutex poisoned - indicates panic in another thread");
            if let Ok(Some(_)) = locked_child.try_wait() {
                return MonitorResult::ProcessCompleted;
            }
            locked_child.id()
        };

        let kill_result = kill_process(child_id, executor.as_ref(), Some(child), kill_config);
        match kill_result {
            KillResult::TerminatedByTerm => return MonitorResult::TimedOut { escalated: false },
            KillResult::TerminatedByKill => return MonitorResult::TimedOut { escalated: true },
            KillResult::SignalsSentAwaitingExit { escalated } => {
                timeout_triggered = Some(TimeoutEnforcementState {
                    pid: child_id,
                    escalated,
                    triggered_at: std::time::Instant::now(),
                    last_sigkill_sent_at: escalated.then_some(std::time::Instant::now()),
                });
            }
            KillResult::Failed => {
                if should_stop.load(Ordering::Acquire) {
                    return MonitorResult::ProcessCompleted;
                }
            }
        }
    }
}
