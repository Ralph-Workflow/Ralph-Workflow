//! Idle-timeout monitor thread.

use super::io::{
    force_kill_best_effort, kill_process, KillConfig, KillResult, DEFAULT_KILL_CONFIG,
};
use crate::executor::{AgentChild, ChildProcessInfo, ProcessExecutor};
use crate::pipeline::idle_timeout::{
    is_idle_timeout_exceeded, time_since_activity, SharedActivityTimestamp,
    SharedFileActivityTracker, IDLE_TIMEOUT_SECS,
};
use crate::workspace::Workspace;
use std::sync::Arc;
use std::time::{Duration, SystemTime};

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
#[derive(Clone)]
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
    /// Optional callback to check if output is complete.
    ///
    /// When provided, this callback is invoked when the idle timeout is exceeded
    /// AND the process has already exited. If it returns `true`, the monitor
    /// returns `MonitorResult::CompleteButWaiting` instead of treating the exit
    /// as a timeout, allowing the pipeline to advance as success.
    pub completion_check: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
}

impl Default for MonitorConfig {
    fn default() -> Self {
        Self {
            timeout: Duration::from_secs(IDLE_TIMEOUT_SECS),
            check_interval: DEFAULT_CHECK_INTERVAL,
            kill_config: DEFAULT_KILL_CONFIG,
            required_idle_confirmations: 2,
            check_child_processes: true,
            completion_check: None,
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
    ///
    /// `child_status_at_timeout` records the child-process state when the timeout
    /// was enforced, enabling observability (AC #9):
    /// - `None`: no children existed (or child-process checking was disabled)
    /// - `Some(info)`: children were present (with stalled CPU) at kill time
    TimedOut {
        escalated: bool,
        child_status_at_timeout: Option<ChildProcessInfo>,
    },
    /// Process exited during idle timeout window but output is ready.
    ///
    /// This occurs when the idle timeout is exceeded, the process has already
    /// exited (via `try_wait`), and a `completion_check` callback confirms that
    /// the output is ready. The monitor kills any remaining state and returns
    /// success, allowing the pipeline to advance.
    CompleteButWaiting,
}

/// Default check interval for the idle monitor (30 seconds).
const DEFAULT_CHECK_INTERVAL: Duration = Duration::from_secs(30);

fn compute_sleep_slice(poll_interval: Duration, deadline: std::time::Instant) -> Option<Duration> {
    let now = std::time::Instant::now();
    if now >= deadline {
        return None;
    }
    let remaining = deadline.saturating_duration_since(now);
    Some(poll_interval.min(remaining))
}

enum SleepStepOutcome {
    Stop,
    DeadlineReached,
    Slept,
}

fn sleep_step(
    should_stop: &std::sync::atomic::AtomicBool,
    poll_interval: Duration,
    deadline: std::time::Instant,
) -> SleepStepOutcome {
    use std::sync::atomic::Ordering;
    // Check should_stop FIRST — if it is set, return Stop immediately,
    // even when the deadline has also been reached. This ensures that a
    // should_stop signal is never missed because we happened to hit the
    // deadline on the same iteration.
    if should_stop.load(Ordering::Acquire) {
        return SleepStepOutcome::Stop;
    }
    match compute_sleep_slice(poll_interval, deadline) {
        None => SleepStepOutcome::DeadlineReached,
        Some(slice) => {
            std::thread::sleep(slice);
            SleepStepOutcome::Slept
        }
    }
}

fn sleep_until_next_check_or_stop(
    should_stop: &std::sync::atomic::AtomicBool,
    check_interval: Duration,
) -> bool {
    let poll_interval = check_interval.min(Duration::from_millis(100));
    let deadline = std::time::Instant::now() + check_interval;
    loop {
        match sleep_step(should_stop, poll_interval, deadline) {
            SleepStepOutcome::Stop => return true,
            SleepStepOutcome::DeadlineReached => {
                // Re-check should_stop when deadline is reached: if it was set
                // during the deadline window, honor it and return true (stop).
                use std::sync::atomic::Ordering;
                if should_stop.load(Ordering::Acquire) {
                    return true;
                }
                return false;
            }
            SleepStepOutcome::Slept => {}
        }
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
        None,
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
        None,
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

pub fn monitor_idle_timeout_with_interval_and_kill_config(
    activity_timestamp: &SharedActivityTimestamp,
    file_activity_config: Option<&FileActivityConfig>,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    should_stop: &Arc<std::sync::atomic::AtomicBool>,
    executor: &Arc<dyn ProcessExecutor>,
    config: MonitorConfig,
) -> MonitorResult {
    monitor_idle_timeout_with_interval_and_kill_config_and_observer(
        activity_timestamp,
        file_activity_config,
        child,
        should_stop,
        executor,
        config,
        None,
    )
}

#[derive(Debug, Clone, Copy)]
struct TimeoutEnforcementState {
    pid: u32,
    escalated: bool,
    last_sigkill_sent_at: Option<std::time::Instant>,
    triggered_at: std::time::Instant,
}

fn try_wait_child(child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>) -> bool {
    let mut locked_child = child
        .lock()
        .expect("child process mutex poisoned - indicates panic in another thread");
    matches!(locked_child.try_wait(), Ok(Some(_)))
}

fn maybe_resend_kill(
    pid: u32,
    executor: &dyn ProcessExecutor,
    kill_config: KillConfig,
    last_kill_sent_at: &mut Option<std::time::Instant>,
) {
    let now = std::time::Instant::now();
    let should_resend = last_kill_sent_at
        .is_none_or(|t| now.duration_since(t) >= kill_config.sigkill_resend_interval());
    if should_resend {
        let _ = force_kill_best_effort(pid, executor);
        *last_kill_sent_at = Some(now);
    }
}

struct ReaperArgs {
    pid: u32,
    child: Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    executor: Arc<dyn ProcessExecutor>,
    should_stop: Arc<std::sync::atomic::AtomicBool>,
    kill_config: KillConfig,
}

fn reaper_should_stop(args: &ReaperArgs) -> bool {
    use std::sync::atomic::Ordering;
    args.should_stop.load(Ordering::Acquire) || try_wait_child(&args.child)
}

/// One reaper loop step: check stop/exit, resend kill, sleep.
/// Returns `true` if the reaper should stop.
fn reaper_step(args: &ReaperArgs, last_kill_sent_at: &mut Option<std::time::Instant>) -> bool {
    if reaper_should_stop(args) {
        return true;
    }
    maybe_resend_kill(
        args.pid,
        args.executor.as_ref(),
        args.kill_config,
        last_kill_sent_at,
    );
    std::thread::sleep(args.kill_config.poll_interval());
    false
}

fn run_reaper_loop(args: &ReaperArgs, deadline: std::time::Instant) {
    let mut last_kill_sent_at = None;
    while std::time::Instant::now() < deadline {
        if reaper_step(args, &mut last_kill_sent_at) {
            return;
        }
    }
}

fn run_reaper_thread(
    pid: u32,
    child: Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    executor: Arc<dyn ProcessExecutor>,
    should_stop: Arc<std::sync::atomic::AtomicBool>,
    kill_config: KillConfig,
) {
    let args = ReaperArgs {
        pid,
        child,
        executor,
        should_stop,
        kill_config,
    };
    run_reaper_loop(
        &args,
        std::time::Instant::now() + args.kill_config.post_sigkill_hard_cap(),
    );
}

enum TimeoutEnforcementContinuation {
    Exited,
    HardCapReached,
    Continue(TimeoutEnforcementState),
}

enum KillResultContinuation {
    TimedOut { escalated: bool },
    AwaitingExit(TimeoutEnforcementState),
    ProcessCompleted,
    Continue,
}

fn escalate_kill(
    state: &mut TimeoutEnforcementState,
    executor: &dyn ProcessExecutor,
    kill_config: KillConfig,
) {
    let now = std::time::Instant::now();
    if state.escalated {
        maybe_resend_kill(
            state.pid,
            executor,
            kill_config,
            &mut state.last_sigkill_sent_at,
        );
    } else {
        let _ = force_kill_best_effort(state.pid, executor);
        state.escalated = true;
        state.last_sigkill_sent_at = Some(now);
    }
}

fn advance_timeout_enforcement(
    mut state: TimeoutEnforcementState,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    executor: &Arc<dyn ProcessExecutor>,
    kill_config: KillConfig,
) -> TimeoutEnforcementContinuation {
    if try_wait_child(child) {
        return TimeoutEnforcementContinuation::Exited;
    }
    escalate_kill(&mut state, executor.as_ref(), kill_config);
    let hard_cap_exceeded =
        state.triggered_at.elapsed() >= kill_config.post_sigkill_hard_cap() && state.escalated;
    if hard_cap_exceeded {
        return TimeoutEnforcementContinuation::HardCapReached;
    }
    TimeoutEnforcementContinuation::Continue(state)
}

struct MonitorLoopState {
    timeout_triggered: Option<TimeoutEnforcementState>,
    last_file_activity: Option<std::time::Instant>,
    consecutive_idle_count: u32,
    last_child_observation: Option<ChildProcessInfo>,
    last_child_info: Option<ChildProcessInfo>,
    child_startup_grace_available: bool,
}

impl MonitorLoopState {
    fn new() -> Self {
        Self {
            timeout_triggered: None,
            last_file_activity: None,
            consecutive_idle_count: 0,
            last_child_observation: None,
            last_child_info: None,
            child_startup_grace_available: true,
        }
    }

    fn reset_idle(&mut self) {
        self.consecutive_idle_count = 0;
        self.last_child_observation = None;
        self.last_child_info = None;
        self.child_startup_grace_available = true;
    }
}

fn replacement_subtree_needs_grace(
    previous_observation: Option<ChildProcessInfo>,
    info: ChildProcessInfo,
) -> bool {
    previous_observation.is_some_and(|prev| {
        info.has_currently_active_children()
            && info.descendant_pid_signature != prev.descendant_pid_signature
            && info.cpu_time_ms <= prev.cpu_time_ms
    })
}

#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn grant_startup_grace(child_pid: u32, info: ChildProcessInfo, s: &mut MonitorLoopState) {
    s.child_startup_grace_available = false;
    s.consecutive_idle_count = 0;
    s.last_child_observation = Some(info);
    eprintln!(
        "Agent has currently active child processes for the first time during idle timeout \
         (pid {child_pid}, {} active of {} children, cpu {}ms, signature {}); granting startup grace",
        info.active_child_count, info.child_count, info.cpu_time_ms, info.descendant_pid_signature
    );
}

fn record_child_activity_observation(
    observed_activity: &Arc<std::sync::Mutex<Option<ChildProcessInfo>>>,
    info: ChildProcessInfo,
) {
    *observed_activity
        .lock()
        .expect("child activity observer mutex poisoned") = Some(info);
}

#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn apply_fresh_progress(
    child_pid: u32,
    info: ChildProcessInfo,
    previous_observation: Option<ChildProcessInfo>,
    s: &mut MonitorLoopState,
    child_activity_suppressed: Option<&Arc<std::sync::Mutex<Option<ChildProcessInfo>>>>,
) {
    s.last_child_observation = if replacement_subtree_needs_grace(previous_observation, info) {
        None
    } else {
        Some(info)
    };
    if let Some(observer) = child_activity_suppressed {
        record_child_activity_observation(observer, info);
    }
    s.consecutive_idle_count = 0;
    s.child_startup_grace_available = true;
    eprintln!(
        "Agent has currently active child processes (pid {child_pid}, \
         {} active of {} children, cpu {}ms, signature {}); continuing monitoring",
        info.active_child_count, info.child_count, info.cpu_time_ms, info.descendant_pid_signature
    );
}

#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn log_idle_child_state(child_pid: u32, info: ChildProcessInfo) {
    if info.has_stalled_children() {
        eprintln!(
            "Agent has child processes (pid {child_pid}, {} total, 0 currently active, cpu {}ms, signature {}) \
             but none show current work; treating as idle",
            info.child_count, info.cpu_time_ms, info.descendant_pid_signature
        );
    } else if info.has_currently_active_children() {
        eprintln!(
            "Agent has child processes (pid {child_pid}, {} active of {} total, cpu {}ms, signature {}) \
             but they showed no fresh progress since the last idle check; treating as idle",
            info.active_child_count, info.child_count, info.cpu_time_ms, info.descendant_pid_signature
        );
    }
}

fn check_child_progress(
    child_pid: u32,
    info: ChildProcessInfo,
    previous_observation: Option<ChildProcessInfo>,
    s: &mut MonitorLoopState,
    child_activity_suppressed: Option<&Arc<std::sync::Mutex<Option<ChildProcessInfo>>>>,
) -> bool {
    if previous_observation.is_some_and(|prev| info.shows_fresh_progress_since(prev)) {
        apply_fresh_progress(
            child_pid,
            info,
            previous_observation,
            s,
            child_activity_suppressed,
        );
        return true;
    }
    log_idle_child_state(child_pid, info);
    s.last_child_observation = Some(info);
    false
}

fn is_first_active_child(
    previous_observation: Option<ChildProcessInfo>,
    grace_available: bool,
    info: ChildProcessInfo,
) -> bool {
    previous_observation.is_none() && grace_available && info.has_currently_active_children()
}

fn handle_child_with_children(
    child_pid: u32,
    info: ChildProcessInfo,
    s: &mut MonitorLoopState,
    child_activity_suppressed: Option<&Arc<std::sync::Mutex<Option<ChildProcessInfo>>>>,
) -> bool {
    s.last_child_info = Some(info);
    let previous_observation = s.last_child_observation;

    if is_first_active_child(previous_observation, s.child_startup_grace_available, info) {
        grant_startup_grace(child_pid, info, s);
        return true;
    }

    check_child_progress(
        child_pid,
        info,
        previous_observation,
        s,
        child_activity_suppressed,
    )
}

fn kill_failed_continuation(should_stop: &std::sync::atomic::AtomicBool) -> KillResultContinuation {
    use std::sync::atomic::Ordering;
    if should_stop.load(Ordering::Acquire) {
        KillResultContinuation::ProcessCompleted
    } else {
        KillResultContinuation::Continue
    }
}

fn awaiting_exit_state(child_id: u32, escalated: bool) -> TimeoutEnforcementState {
    TimeoutEnforcementState {
        pid: child_id,
        escalated,
        triggered_at: std::time::Instant::now(),
        last_sigkill_sent_at: escalated.then_some(std::time::Instant::now()),
    }
}

fn process_kill_result(
    kill_result: KillResult,
    child_id: u32,
    should_stop: &std::sync::atomic::AtomicBool,
) -> KillResultContinuation {
    match kill_result {
        KillResult::TerminatedByTerm => KillResultContinuation::TimedOut { escalated: false },
        KillResult::TerminatedByKill => KillResultContinuation::TimedOut { escalated: true },
        KillResult::SignalsSentAwaitingExit { escalated } => {
            KillResultContinuation::AwaitingExit(awaiting_exit_state(child_id, escalated))
        }
        KillResult::Failed => kill_failed_continuation(should_stop),
    }
}

fn try_get_child_id(child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>) -> Option<u32> {
    let mut locked_child = child
        .lock()
        .expect("child process mutex poisoned - indicates panic in another thread");
    if let Ok(Some(_)) = locked_child.try_wait() {
        return None;
    }
    Some(locked_child.id())
}

enum EnforcementStep {
    ReturnResult(MonitorResult),
    Continue,
}

fn handle_enforcement_phase(
    state: TimeoutEnforcementState,
    last_child_info: Option<ChildProcessInfo>,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    executor: &Arc<dyn ProcessExecutor>,
    should_stop: &Arc<std::sync::atomic::AtomicBool>,
    kill_config: KillConfig,
) -> (EnforcementStep, Option<TimeoutEnforcementState>) {
    match advance_timeout_enforcement(state, child, executor, kill_config) {
        TimeoutEnforcementContinuation::Exited => (
            EnforcementStep::ReturnResult(MonitorResult::TimedOut {
                escalated: state.escalated,
                child_status_at_timeout: last_child_info,
            }),
            None,
        ),
        TimeoutEnforcementContinuation::HardCapReached => {
            let pid = state.pid;
            std::thread::spawn({
                let child = Arc::clone(child);
                let executor = Arc::clone(executor);
                let should_stop = Arc::clone(should_stop);
                move || run_reaper_thread(pid, child, executor, should_stop, kill_config)
            });
            (
                EnforcementStep::ReturnResult(MonitorResult::TimedOut {
                    escalated: state.escalated,
                    child_status_at_timeout: last_child_info,
                }),
                None,
            )
        }
        TimeoutEnforcementContinuation::Continue(new_state) => {
            (EnforcementStep::Continue, Some(new_state))
        }
    }
}

#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn apply_file_activity_scan_result(
    result: Result<bool, impl std::fmt::Display>,
    file_window: Duration,
    s: &mut MonitorLoopState,
) -> bool {
    match result {
        Ok(true) => {
            s.last_file_activity = Some(std::time::Instant::now());
            s.reset_idle();
            eprintln!("AI-generated files were updated recently, continuing monitoring");
            true
        }
        Ok(false) => {
            eprintln!(
                "No AI-generated file updates in the last {file_window:?}, proceeding with timeout"
            );
            false
        }
        Err(e) => {
            eprintln!(
                "Warning: file activity check failed (treating as no recent file activity, proceeding with timeout enforcement): {e}"
            );
            false
        }
    }
}

#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn check_file_activity(
    fac: &FileActivityConfig,
    activity_timestamp: &SharedActivityTimestamp,
    timeout: Duration,
    check_interval: Duration,
    s: &mut MonitorLoopState,
) -> bool {
    if s.last_file_activity.is_some_and(|t| t.elapsed() < timeout) {
        s.reset_idle();
        eprintln!(
            "Continuing monitoring: file activity was confirmed within the last timeout window"
        );
        return true;
    }

    let scan_overhead_buffer = Duration::from_secs(1);
    let cap = timeout + check_interval + scan_overhead_buffer;
    let actual_idle = time_since_activity(activity_timestamp);
    let file_window = (actual_idle + scan_overhead_buffer).min(cap);
    let locked_tracker = fac.tracker.lock();
    let result = locked_tracker.check_for_recent_activity(
        fac.workspace.as_ref(),
        file_window,
        SystemTime::now(),
    );
    apply_file_activity_scan_result(result, file_window, s)
}

fn check_child_processes_activity(
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    executor: &Arc<dyn ProcessExecutor>,
    s: &mut MonitorLoopState,
    child_activity_suppressed: Option<&Arc<std::sync::Mutex<Option<ChildProcessInfo>>>>,
) -> bool {
    let child_pid = {
        let locked_child = child.lock().expect("child process mutex poisoned");
        locked_child.id()
    };
    let info = executor.get_child_process_info(child_pid);
    if info.has_children() {
        handle_child_with_children(child_pid, info, s, child_activity_suppressed)
    } else {
        s.last_child_observation = None;
        s.last_child_info = None;
        s.child_startup_grace_available = true;
        false
    }
}

fn apply_kill_result(
    kill_result: KillResult,
    child_id: u32,
    last_child_info: Option<ChildProcessInfo>,
    should_stop: &Arc<std::sync::atomic::AtomicBool>,
    s: &mut MonitorLoopState,
) -> Option<MonitorResult> {
    match process_kill_result(kill_result, child_id, should_stop.as_ref()) {
        KillResultContinuation::TimedOut { escalated } => Some(MonitorResult::TimedOut {
            escalated,
            child_status_at_timeout: last_child_info,
        }),
        KillResultContinuation::AwaitingExit(state) => {
            s.timeout_triggered = Some(state);
            None
        }
        KillResultContinuation::ProcessCompleted => Some(MonitorResult::ProcessCompleted),
        KillResultContinuation::Continue => None,
    }
}

enum MonitorLoopAction {
    Return(MonitorResult),
    Continue,
}

struct MonitorParams<'a> {
    activity_timestamp: &'a SharedActivityTimestamp,
    file_activity_config: Option<&'a FileActivityConfig>,
    child: &'a Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    should_stop: &'a Arc<std::sync::atomic::AtomicBool>,
    executor: &'a Arc<dyn ProcessExecutor>,
    child_activity_suppressed: Option<&'a Arc<std::sync::Mutex<Option<ChildProcessInfo>>>>,
    timeout: Duration,
    check_interval: Duration,
    kill_config: KillConfig,
    required_idle_confirmations: u32,
    check_child_processes: bool,
    completion_check: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
}

fn kill_child_and_apply(
    child_id: u32,
    params: &MonitorParams<'_>,
    s: &mut MonitorLoopState,
) -> MonitorLoopAction {
    // If the child already exited, don't bother sending signals — return success.
    if try_wait_child(params.child) {
        return MonitorLoopAction::Return(MonitorResult::ProcessCompleted);
    }
    let kill_result = kill_process(
        child_id,
        params.executor.as_ref(),
        Some(params.child),
        params.kill_config,
    );
    match apply_kill_result(
        kill_result,
        child_id,
        s.last_child_info,
        params.should_stop,
        s,
    ) {
        Some(result) => MonitorLoopAction::Return(result),
        None => MonitorLoopAction::Continue,
    }
}

#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn log_idle_progress(consecutive: u32, required: u32) {
    eprintln!(
        "Idle confirmed {consecutive}/{required} times; waiting for next check interval before kill"
    );
}

/// Result of the idle-confirmed policy computation.
enum IdleConfirmedAction {
    /// Not enough idle confirmations yet; continue polling.
    Continue,
    /// Child already exited or completion check passed; return the given action.
    Return(MonitorLoopAction),
    /// Agent is stuck; kill and return.
    KillAndReturn(u32),
    /// Completion check passed; kill process and return success.
    CompleteAndKill(u32),
}

/// Compute the idle-confirmed policy — pure function, no side effects.
/// Encapsulates all branching so `handle_idle_confirmed` stays thin.
fn compute_idle_confirmed_action(
    params: &MonitorParams<'_>,
    s: &MonitorLoopState,
) -> IdleConfirmedAction {
    let consecutive = s.consecutive_idle_count.saturating_add(1);
    if consecutive < params.required_idle_confirmations {
        return IdleConfirmedAction::Continue;
    }

    let Some(child_id) = try_get_child_id(params.child) else {
        return IdleConfirmedAction::Return(determine_result_on_child_exit(
            params.completion_check.as_ref(),
        ));
    };

    // completion_check_passes is pure — just checks the callback
    if completion_check_passes(params.completion_check.as_ref()) {
        return IdleConfirmedAction::CompleteAndKill(child_id);
    }

    IdleConfirmedAction::KillAndReturn(child_id)
}

fn handle_idle_confirmed(
    params: &MonitorParams<'_>,
    s: &mut MonitorLoopState,
) -> MonitorLoopAction {
    let consecutive = s.consecutive_idle_count.saturating_add(1);
    s.consecutive_idle_count = consecutive;

    let idle_action = compute_idle_confirmed_action(params, s);
    match idle_action {
        IdleConfirmedAction::Continue => {
            log_idle_progress(consecutive, params.required_idle_confirmations);
            MonitorLoopAction::Continue
        }
        IdleConfirmedAction::Return(action) => action,
        IdleConfirmedAction::KillAndReturn(child_id) => kill_child_and_apply(child_id, params, s),
        IdleConfirmedAction::CompleteAndKill(child_id) => {
            // Side effect: kill the process, then return success
            try_complete_but_waiting_and_kill(child_id, params);
            MonitorLoopAction::Return(MonitorResult::CompleteButWaiting)
        }
    }
}

/// Pure: check if the completion callback returns true.
fn completion_check_passes(completion_check: Option<&Arc<dyn Fn() -> bool + Send + Sync>>) -> bool {
    completion_check.is_some_and(|c| c())
}

/// Execute the completion-check kill: log, kill the process (best-effort),
/// and return CompleteButWaiting.
#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn try_complete_but_waiting_and_kill(child_id: u32, params: &MonitorParams<'_>) {
    eprintln!(
        "Idle timeout: completion check passed (output file ready); \
         killing process and treating as CompleteButWaiting"
    );
    let kill_result = kill_process(
        child_id,
        params.executor.as_ref(),
        Some(params.child),
        params.kill_config,
    );
    // best-effort kill; regardless of kill result return CompleteButWaiting
    let _ = kill_result;
}

fn determine_result_on_child_exit(
    completion_check: Option<&Arc<dyn Fn() -> bool + Send + Sync>>,
) -> MonitorLoopAction {
    if completion_check.is_some_and(|c| c()) {
        MonitorLoopAction::Return(MonitorResult::CompleteButWaiting)
    } else {
        MonitorLoopAction::Return(MonitorResult::ProcessCompleted)
    }
}

fn check_file_activity_suppression(params: &MonitorParams<'_>, s: &mut MonitorLoopState) -> bool {
    params.file_activity_config.is_some_and(|fac| {
        check_file_activity(
            fac,
            params.activity_timestamp,
            params.timeout,
            params.check_interval,
            s,
        )
    })
}

#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn activity_resumed_after_file_scan(params: &MonitorParams<'_>, s: &mut MonitorLoopState) -> bool {
    if is_idle_timeout_exceeded(params.activity_timestamp, params.timeout) {
        return false;
    }
    s.reset_idle();
    eprintln!("Output activity detected after file scan; continuing monitoring");
    true
}

fn child_processes_still_active(params: &MonitorParams<'_>, s: &mut MonitorLoopState) -> bool {
    params.check_child_processes
        && check_child_processes_activity(
            params.child,
            params.executor,
            s,
            params.child_activity_suppressed,
        )
}

fn check_timeout_suppressors(
    params: &MonitorParams<'_>,
    s: &mut MonitorLoopState,
) -> Option<MonitorLoopAction> {
    if check_file_activity_suppression(params, s) {
        return Some(MonitorLoopAction::Continue);
    }
    if activity_resumed_after_file_scan(params, s) {
        return Some(MonitorLoopAction::Continue);
    }
    if child_processes_still_active(params, s) {
        return Some(MonitorLoopAction::Continue);
    }
    None
}

#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn handle_timeout_exceeded(
    params: &MonitorParams<'_>,
    s: &mut MonitorLoopState,
) -> MonitorLoopAction {
    eprintln!(
        "Idle timeout exceeded: no output activity for {} seconds",
        time_since_activity(params.activity_timestamp).as_secs()
    );
    check_timeout_suppressors(params, s).unwrap_or_else(|| handle_idle_confirmed(params, s))
}

fn should_stop_before_timeout(params: &MonitorParams<'_>, s: &MonitorLoopState) -> bool {
    use std::sync::atomic::Ordering;
    s.timeout_triggered.is_none() && params.should_stop.load(Ordering::Acquire)
}

fn sleep_check_stops_early(params: &MonitorParams<'_>, s: &MonitorLoopState) -> bool {
    s.timeout_triggered.is_none()
        && sleep_until_next_check_or_stop(params.should_stop.as_ref(), params.check_interval)
}

fn enforcement_step_to_action(step: EnforcementStep) -> MonitorLoopAction {
    match step {
        EnforcementStep::ReturnResult(r) => MonitorLoopAction::Return(r),
        EnforcementStep::Continue => MonitorLoopAction::Continue,
    }
}

fn dispatch_enforcement_phase(
    params: &MonitorParams<'_>,
    s: &mut MonitorLoopState,
) -> MonitorLoopAction {
    // If should_stop is set while in enforcement, return ProcessCompleted
    // instead of continuing enforcement (which could return TimedOut via HardCapReached).
    use std::sync::atomic::Ordering;
    if params.should_stop.load(Ordering::Acquire) {
        return MonitorLoopAction::Return(MonitorResult::ProcessCompleted);
    }
    let state = match s.timeout_triggered.take() {
        Some(st) => st,
        None => return MonitorLoopAction::Continue,
    };
    let (step, next_state) = handle_enforcement_phase(
        state,
        s.last_child_info,
        params.child,
        params.executor,
        params.should_stop,
        params.kill_config,
    );
    s.timeout_triggered = next_state;
    enforcement_step_to_action(step)
}

/// Policy decision for one enforcement tick — pure, no side effects.
enum TickPolicy {
    /// Child already exited; return immediately with ProcessCompleted.
    ChildAlreadyExited,
    /// Stop conditions met (user interrupt or external stop); return the given action.
    StopConditionsMet,
    /// Enforcement phase already active; dispatch to enforcement handler.
    EnforcementPhase,
    /// Not idle yet; reset idle tracking and continue polling.
    NotIdle,
    /// Idle timeout exceeded; handle escalation.
    IdleTimeoutExceeded,
}

/// Compute the policy decision for this tick — pure function, no side effects.
/// All branching lives here so the boundary function stays thin.
fn compute_tick_policy(
    timeout_triggered: bool,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    activity_timestamp: &SharedActivityTimestamp,
    timeout: Duration,
    params: &MonitorParams<'_>,
    s: &MonitorLoopState,
) -> TickPolicy {
    if !timeout_triggered && try_wait_child(child) {
        return TickPolicy::ChildAlreadyExited;
    }
    if should_stop_before_timeout(params, s) || sleep_check_stops_early(params, s) {
        return TickPolicy::StopConditionsMet;
    }
    if timeout_triggered {
        return TickPolicy::EnforcementPhase;
    }
    if !is_idle_timeout_exceeded(activity_timestamp, timeout) {
        return TickPolicy::NotIdle;
    }
    TickPolicy::IdleTimeoutExceeded
}

fn handle_enforcement_tick(
    params: &MonitorParams<'_>,
    s: &mut MonitorLoopState,
) -> MonitorLoopAction {
    let policy = compute_tick_policy(
        s.timeout_triggered.is_some(),
        params.child,
        params.activity_timestamp,
        params.timeout,
        params,
        s,
    );
    match policy {
        TickPolicy::ChildAlreadyExited => {
            MonitorLoopAction::Return(MonitorResult::ProcessCompleted)
        }
        TickPolicy::StopConditionsMet => MonitorLoopAction::Return(MonitorResult::ProcessCompleted),
        TickPolicy::EnforcementPhase => dispatch_enforcement_phase(params, s),
        TickPolicy::NotIdle => {
            s.reset_idle();
            MonitorLoopAction::Continue
        }
        TickPolicy::IdleTimeoutExceeded => handle_timeout_exceeded(params, s),
    }
}

fn run_monitor_loop(params: &MonitorParams<'_>) -> MonitorResult {
    let mut s = MonitorLoopState::new();
    loop {
        if let MonitorLoopAction::Return(r) = handle_enforcement_tick(params, &mut s) {
            return r;
        }
    }
}

pub fn monitor_idle_timeout_with_interval_and_kill_config_and_observer(
    activity_timestamp: &SharedActivityTimestamp,
    file_activity_config: Option<&FileActivityConfig>,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    should_stop: &Arc<std::sync::atomic::AtomicBool>,
    executor: &Arc<dyn ProcessExecutor>,
    config: MonitorConfig,
    child_activity_suppressed: Option<&Arc<std::sync::Mutex<Option<ChildProcessInfo>>>>,
) -> MonitorResult {
    let params = MonitorParams {
        activity_timestamp,
        file_activity_config,
        child,
        should_stop,
        executor,
        child_activity_suppressed,
        timeout: config.timeout,
        check_interval: config.check_interval,
        kill_config: config.kill_config,
        required_idle_confirmations: config.required_idle_confirmations,
        check_child_processes: config.check_child_processes,
        completion_check: config.completion_check,
    };
    run_monitor_loop(&params)
}
