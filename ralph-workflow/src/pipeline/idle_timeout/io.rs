// Subprocess termination helpers for idle-timeout enforcement.

use crate::executor::{AgentChild, ChildProcessInfo, ProcessExecutor};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Shared agent child handle (Arc-wrapped Mutex over a boxed AgentChild).
pub(crate) type SharedAgentChild = Arc<Mutex<Box<dyn AgentChild>>>;
/// Shared child-activity observer (Arc-wrapped Mutex over an optional ChildProcessInfo snapshot).
pub(crate) type SharedChildActivityObserver = Arc<Mutex<Option<ChildProcessInfo>>>;

/// Result of attempting to kill a process.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum KillResult {
    /// Process was successfully killed with SIGTERM.
    TerminatedByTerm,
    /// Process required SIGKILL/taskkill escalation.
    TerminatedByKill,
    /// Kill signals were sent successfully, but the process was not confirmed exited yet.
    ///
    /// The monitor should continue polling for exit. It may return `TimedOut`
    /// after a bounded enforcement window so the pipeline can regain control,
    /// but it must not silently stop enforcing termination; a background reaper
    /// should continue best-effort SIGKILL/taskkill attempts until exit is observed.
    SignalsSentAwaitingExit { escalated: bool },
    /// Kill attempt failed (process may have already exited).
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct KillConfig {
    sigterm_grace: Duration,
    poll_interval: Duration,
    sigkill_confirm_timeout: Duration,
    post_sigkill_hard_cap: Duration,
    sigkill_resend_interval: Duration,
}

impl KillConfig {
    #[must_use]
    pub const fn new(
        sigterm_grace: Duration,
        poll_interval: Duration,
        sigkill_confirm_timeout: Duration,
        post_sigkill_hard_cap: Duration,
        sigkill_resend_interval: Duration,
    ) -> Self {
        Self {
            sigterm_grace,
            poll_interval,
            sigkill_confirm_timeout,
            post_sigkill_hard_cap,
            sigkill_resend_interval,
        }
    }

    #[must_use]
    pub const fn sigterm_grace(&self) -> Duration {
        self.sigterm_grace
    }

    #[must_use]
    pub const fn poll_interval(&self) -> Duration {
        self.poll_interval
    }

    #[must_use]
    pub const fn sigkill_confirm_timeout(&self) -> Duration {
        self.sigkill_confirm_timeout
    }

    #[must_use]
    pub const fn post_sigkill_hard_cap(&self) -> Duration {
        self.post_sigkill_hard_cap
    }

    #[must_use]
    pub const fn sigkill_resend_interval(&self) -> Duration {
        self.sigkill_resend_interval
    }
}

/// Default kill configuration.
///
/// - SIGTERM grace: 5s
/// - Poll interval: 100ms
/// - SIGKILL confirm timeout: 500ms
/// - Post-SIGKILL hard cap: 5s
/// - SIGKILL resend interval: 1s
pub(crate) const DEFAULT_KILL_CONFIG: KillConfig = KillConfig::new(
    Duration::from_secs(5),
    Duration::from_millis(100),
    Duration::from_millis(500),
    Duration::from_secs(5),
    Duration::from_secs(1),
);

#[cfg(unix)]
pub(crate) fn force_kill_best_effort(pid: u32, executor: &dyn ProcessExecutor) -> bool {
    let pid_str = pid.to_string();
    let process_group_id = format!("-{pid_str}");

    let group_ok = executor
        .execute("kill", &["-KILL", "--", &process_group_id], &[], None)
        .map(|o| o.status.success())
        .unwrap_or(false);

    if group_ok {
        return true;
    }

    executor
        .execute("kill", &["-KILL", &pid_str], &[], None)
        .map(|o| o.status.success())
        .unwrap_or(false)
}

#[cfg(windows)]
pub(crate) fn force_kill_best_effort(pid: u32, executor: &dyn ProcessExecutor) -> bool {
    executor
        .execute(
            "taskkill",
            &["/F", "/T", "/PID", &pid.to_string()],
            &[],
            None,
        )
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Try-wait the locked child. Returns `true` if the process has exited.
#[cfg(unix)]
fn try_wait_child(child_arc: &Arc<Mutex<Box<dyn AgentChild>>>) -> bool {
    let status = {
        let mut locked = child_arc
            .lock()
            .expect("child process mutex poisoned - indicates panic in another thread");
        locked.try_wait()
    };
    matches!(status, Ok(Some(_)))
}

/// Check if a child process has exited; sleep briefly if not. Returns `true` if exited.
#[cfg(unix)]
fn check_child_or_sleep(
    child_arc: &Arc<Mutex<Box<dyn AgentChild>>>,
    poll_interval: Duration,
) -> bool {
    if try_wait_child(child_arc) {
        return true;
    }
    std::thread::sleep(poll_interval);
    false
}

/// Poll a child process until deadline. Returns `true` if the child exited before the deadline.
#[cfg(unix)]
fn poll_child_until_deadline(
    child_arc: &Arc<Mutex<Box<dyn AgentChild>>>,
    deadline: std::time::Instant,
    poll_interval: Duration,
) -> bool {
    let mut exited = false;
    while !exited && std::time::Instant::now() < deadline {
        exited = check_child_or_sleep(child_arc, poll_interval);
    }
    exited
}

/// Send SIGTERM to a process group and then the process itself. Returns true if either succeeded.
#[cfg(unix)]
fn send_sigterm(pid_str: &str, process_group_id: &str, executor: &dyn ProcessExecutor) -> bool {
    executor
        .execute("kill", &["-TERM", "--", process_group_id], &[], None)
        .map(|o| o.status.success())
        .unwrap_or(false)
        || executor
            .execute("kill", &["-TERM", pid_str], &[], None)
            .map(|o| o.status.success())
            .unwrap_or(false)
}

/// Send SIGKILL to a process group and then the process itself. Returns true if either succeeded.
#[cfg(unix)]
fn send_sigkill(pid_str: &str, process_group_id: &str, executor: &dyn ProcessExecutor) -> bool {
    executor
        .execute("kill", &["-KILL", "--", process_group_id], &[], None)
        .map(|o| o.status.success())
        .unwrap_or(false)
        || executor
            .execute("kill", &["-KILL", pid_str], &[], None)
            .map(|o| o.status.success())
            .unwrap_or(false)
}

/// Send SIGKILL and poll for exit confirmation. Returns the kill result after escalation.
#[cfg(unix)]
fn escalate_to_sigkill_and_confirm(
    pid_str: &str,
    process_group_id: &str,
    executor: &dyn ProcessExecutor,
    child_arc: &Arc<Mutex<Box<dyn AgentChild>>>,
    config: KillConfig,
) -> KillResult {
    if !send_sigkill(pid_str, process_group_id, executor) {
        return KillResult::Failed;
    }
    let confirm_deadline = std::time::Instant::now() + config.sigkill_confirm_timeout;
    if poll_child_until_deadline(child_arc, confirm_deadline, config.poll_interval) {
        return KillResult::TerminatedByKill;
    }
    KillResult::SignalsSentAwaitingExit { escalated: true }
}

/// Escalate to SIGKILL after SIGTERM grace period expired.
#[cfg(unix)]
fn kill_process_with_child(
    pid_str: &str,
    process_group_id: &str,
    executor: &dyn ProcessExecutor,
    child_arc: &Arc<Mutex<Box<dyn AgentChild>>>,
    config: KillConfig,
) -> KillResult {
    let grace_deadline = std::time::Instant::now() + config.sigterm_grace;
    if poll_child_until_deadline(child_arc, grace_deadline, config.poll_interval) {
        return KillResult::TerminatedByTerm;
    }
    escalate_to_sigkill_and_confirm(pid_str, process_group_id, executor, child_arc, config)
}

/// Kill a process by PID using platform-specific commands via executor.
///
/// First attempts SIGTERM, waits for a grace period while verifying liveness,
/// then escalates to SIGKILL if the process hasn't terminated.
#[cfg(unix)]
pub(crate) fn kill_process(
    pid: u32,
    executor: &dyn ProcessExecutor,
    child: Option<&Arc<Mutex<Box<dyn AgentChild>>>>,
    config: KillConfig,
) -> KillResult {
    let pid_str = pid.to_string();
    let process_group_id = format!("-{pid_str}");

    if !send_sigterm(&pid_str, &process_group_id, executor) {
        return KillResult::Failed;
    }

    match child {
        None => KillResult::TerminatedByTerm,
        Some(child_arc) => {
            kill_process_with_child(&pid_str, &process_group_id, executor, child_arc, config)
        }
    }
}

/// Try-wait the locked child (Windows). Returns `true` if the process has exited.
#[cfg(windows)]
fn try_wait_child(child_arc: &Arc<Mutex<Box<dyn AgentChild>>>) -> bool {
    let status = {
        let locked = child_arc
            .lock()
            .expect("child process mutex poisoned - indicates panic in another thread");
        locked.try_wait()
    };
    matches!(status, Ok(Some(_)))
}

/// Single poll-or-sleep step (Windows). Returns `true` if the child has exited.
#[cfg(windows)]
fn check_child_or_sleep(
    child_arc: &Arc<Mutex<Box<dyn AgentChild>>>,
    poll_interval: Duration,
) -> bool {
    if try_wait_child(child_arc) {
        return true;
    }
    std::thread::sleep(poll_interval);
    false
}

/// Poll a child process until deadline (Windows). Returns `true` if the child exited before the deadline.
#[cfg(windows)]
fn poll_child_until_deadline(
    child_arc: &Arc<Mutex<Box<dyn AgentChild>>>,
    deadline: std::time::Instant,
    poll_interval: Duration,
) -> bool {
    let mut exited = false;
    while !exited && std::time::Instant::now() < deadline {
        exited = check_child_or_sleep(child_arc, poll_interval);
    }
    exited
}

/// Run `taskkill /F /T /PID` and return whether it succeeded.
#[cfg(windows)]
fn run_taskkill(pid: u32, executor: &dyn ProcessExecutor) -> bool {
    executor
        .execute(
            "taskkill",
            &["/F", "/T", "/PID", &pid.to_string()],
            &[],
            None,
        )
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Confirm process exit after taskkill within the confirm-timeout window.
#[cfg(windows)]
fn confirm_exit_after_taskkill(
    child_arc: &Arc<Mutex<Box<dyn AgentChild>>>,
    config: KillConfig,
) -> KillResult {
    let confirm_deadline = std::time::Instant::now() + config.sigkill_confirm_timeout;
    if poll_child_until_deadline(child_arc, confirm_deadline, config.poll_interval) {
        KillResult::TerminatedByKill
    } else {
        KillResult::SignalsSentAwaitingExit { escalated: true }
    }
}

/// Windows kill implementation.
///
/// `taskkill /F` is already forceful; treat this as an escalated kill.
#[cfg(windows)]
pub(crate) fn kill_process(
    pid: u32,
    executor: &dyn ProcessExecutor,
    child: Option<&Arc<Mutex<Box<dyn AgentChild>>>>,
    config: KillConfig,
) -> KillResult {
    if !run_taskkill(pid, executor) {
        return KillResult::Failed;
    }

    match child {
        None => KillResult::TerminatedByKill,
        Some(child_arc) => confirm_exit_after_taskkill(child_arc, config),
    }
}
