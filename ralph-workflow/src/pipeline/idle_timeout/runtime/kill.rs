//! Subprocess termination helpers for idle-timeout enforcement.

use crate::executor::{AgentChild, ProcessExecutor};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Result of attempting to kill a process.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KillResult {
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
pub const DEFAULT_KILL_CONFIG: KillConfig = KillConfig::new(
    Duration::from_secs(5),
    Duration::from_millis(100),
    Duration::from_millis(500),
    Duration::from_secs(5),
    Duration::from_secs(1),
);

#[cfg(unix)]
pub fn force_kill_best_effort(pid: u32, executor: &dyn ProcessExecutor) -> bool {
    let pid_str = pid.to_string();
    let process_group_id = format!("-{pid_str}");

    // Prefer killing the whole process group so descendant processes that inherited
    // stdout/stderr FDs don't keep pipes open after the parent is gone.
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

/// Kill a process by PID using platform-specific commands via executor.
///
/// First attempts SIGTERM, waits for a grace period while verifying liveness,
/// then escalates to SIGKILL if the process hasn't terminated.
#[cfg(unix)]
pub fn kill_process(
    pid: u32,
    executor: &dyn ProcessExecutor,
    child: Option<&Arc<Mutex<Box<dyn AgentChild>>>>,
    config: KillConfig,
) -> KillResult {
    let pid_str = pid.to_string();
    let process_group_id = format!("-{pid_str}");

    // Send SIGTERM to the process group first (see module docs).
    let term_ok = executor
        .execute("kill", &["-TERM", "--", &process_group_id], &[], None)
        .map(|o| o.status.success())
        .unwrap_or(false)
        || executor
            .execute("kill", &["-TERM", &pid_str], &[], None)
            .map(|o| o.status.success())
            .unwrap_or(false);

    if !term_ok {
        return KillResult::Failed;
    }

    if let Some(child_arc) = child {
        let grace_deadline = std::time::Instant::now() + config.sigterm_grace;
        while std::time::Instant::now() < grace_deadline {
            let status = {
                let mut locked_child = child_arc
                    .lock()
                    .expect("child process mutex poisoned - indicates panic in another thread");
                locked_child.try_wait()
            };

            match status {
                Ok(Some(_)) => return KillResult::TerminatedByTerm,
                Ok(None) | Err(_) => std::thread::sleep(config.poll_interval),
            }
        }

        let kill_ok = executor
            .execute("kill", &["-KILL", "--", &process_group_id], &[], None)
            .map(|o| o.status.success())
            .unwrap_or(false)
            || executor
                .execute("kill", &["-KILL", &pid_str], &[], None)
                .map(|o| o.status.success())
                .unwrap_or(false);
        if !kill_ok {
            return KillResult::Failed;
        }

        let confirm_deadline = std::time::Instant::now() + config.sigkill_confirm_timeout;
        while std::time::Instant::now() < confirm_deadline {
            let status = {
                let mut locked_child = child_arc
                    .lock()
                    .expect("child process mutex poisoned - indicates panic in another thread");
                locked_child.try_wait()
            };

            match status {
                Ok(Some(_)) => return KillResult::TerminatedByKill,
                Ok(None) | Err(_) => std::thread::sleep(config.poll_interval),
            }
        }

        return KillResult::SignalsSentAwaitingExit { escalated: true };
    }

    KillResult::TerminatedByTerm
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
    let result = executor.execute(
        "taskkill",
        &["/F", "/T", "/PID", &pid.to_string()],
        &[],
        None,
    );
    let kill_ok = result.map(|o| o.status.success()).unwrap_or(false);
    if !kill_ok {
        return KillResult::Failed;
    }

    if let Some(child_arc) = child {
        let confirm_deadline = std::time::Instant::now() + config.sigkill_confirm_timeout;
        while std::time::Instant::now() < confirm_deadline {
            let status = {
                let mut locked_child = child_arc
                    .lock()
                    .expect("child process mutex poisoned - indicates panic in another thread");
                locked_child.try_wait()
            };

            match status {
                Ok(Some(_)) => return KillResult::TerminatedByKill,
                Ok(None) | Err(_) => std::thread::sleep(config.poll_interval),
            }
        }

        return KillResult::SignalsSentAwaitingExit { escalated: true };
    }

    KillResult::TerminatedByKill
}
