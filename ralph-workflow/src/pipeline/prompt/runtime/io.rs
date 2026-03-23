// pipeline/prompt/runtime/io.rs — boundary module for agent execution cleanup.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Cleanup functions for agent execution.

use crate::pipeline::idle_timeout::KillConfig;
use std::io;
use std::sync::Arc;

fn poll_child_exited(
    child_arc: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
) -> bool {
    let mut locked_child = child_arc
        .lock()
        .expect("child process mutex poisoned - indicates panic in another thread");
    matches!(locked_child.try_wait(), Ok(Some(_)))
}

fn resend_kill_if_due(
    executor: &dyn crate::executor::ProcessExecutor,
    pid: u32,
    kill_config: KillConfig,
    last_kill_sent_at: &mut Option<std::time::Instant>,
) {
    use crate::pipeline::idle_timeout::io::force_kill_best_effort;
    let now = std::time::Instant::now();
    let due =
        last_kill_sent_at.is_none_or(|t| now.duration_since(t) >= kill_config.sigkill_resend_interval());
    if due {
        let _ = force_kill_best_effort(pid, executor);
        *last_kill_sent_at = Some(now);
    }
}

/// Arguments bundle for the await-exit polling loop.
struct ExitWaitArgs<'a> {
    child_arc: &'a Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    executor: &'a dyn crate::executor::ProcessExecutor,
    pid: u32,
    kill_config: KillConfig,
}

/// One iteration of the await-exit loop: check exit, maybe resend kill, sleep.
/// Returns `true` if the child has exited.
fn await_exit_step(args: &ExitWaitArgs<'_>, last_kill_sent_at: &mut Option<std::time::Instant>) -> bool {
    if poll_child_exited(args.child_arc) {
        return true;
    }
    resend_kill_if_due(args.executor, args.pid, args.kill_config, last_kill_sent_at);
    std::thread::sleep(args.kill_config.poll_interval());
    false
}

fn await_exit_with_sigkill_resend(args: &ExitWaitArgs<'_>) -> bool {
    let hard_deadline = std::time::Instant::now() + args.kill_config.post_sigkill_hard_cap();
    let mut last_kill_sent_at: Option<std::time::Instant> = None;
    let mut exited = false;
    while !exited && std::time::Instant::now() < hard_deadline {
        exited = await_exit_step(args, &mut last_kill_sent_at);
    }
    exited
}

pub fn terminate_child_best_effort(
    child_arc: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    executor: &dyn crate::executor::ProcessExecutor,
    kill_config: KillConfig,
) -> bool {
    use crate::pipeline::idle_timeout::io::{kill_process, KillResult};

    let pid = {
        let locked_child = child_arc
            .lock()
            .expect("child process mutex poisoned - indicates panic in another thread");
        locked_child.id()
    };

    let args = ExitWaitArgs { child_arc, executor, pid, kill_config };
    match kill_process(pid, executor, Some(child_arc), kill_config) {
        KillResult::TerminatedByTerm | KillResult::TerminatedByKill => true,
        KillResult::SignalsSentAwaitingExit { .. } => await_exit_with_sigkill_resend(&args),
        KillResult::Failed => poll_child_exited(child_arc),
    }
}

fn drain_stderr_collector(
    stderr_cancel: &Arc<std::sync::atomic::AtomicBool>,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<io::Result<String>>>,
) {
    super::io_stderr_collector::cancel_and_join_stderr_collector(
        stderr_cancel,
        stderr_join_handle,
        std::time::Duration::from_millis(250),
    );
    if stderr_join_handle.is_some() {
        super::io_stderr_collector::cancel_and_join_stderr_collector(
            stderr_cancel,
            stderr_join_handle,
            std::time::Duration::from_secs(2),
        );
    }
    if stderr_join_handle.is_some() {
        let _ = stderr_join_handle.take();
    }
}

/// Signal the monitor to stop and join its thread if the child confirmed exited.
fn stop_monitor_if_exited(
    exited: bool,
    monitor_should_stop: &Arc<std::sync::atomic::AtomicBool>,
    monitor_handle: &mut Option<std::thread::JoinHandle<crate::pipeline::idle_timeout::MonitorResult>>,
) {
    use std::sync::atomic::Ordering;
    if !exited {
        return;
    }
    monitor_should_stop.store(true, Ordering::Release);
    if let Some(handle) = monitor_handle.take() {
        let _ = handle.join();
    }
}

pub fn cleanup_after_agent_failure(
    child_arc: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    monitor_should_stop: &Arc<std::sync::atomic::AtomicBool>,
    monitor_handle: &mut Option<
        std::thread::JoinHandle<crate::pipeline::idle_timeout::MonitorResult>,
    >,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<io::Result<String>>>,
    stderr_cancel: &Arc<std::sync::atomic::AtomicBool>,
    executor: &dyn crate::executor::ProcessExecutor,
    kill_config: KillConfig,
) {
    let exited = terminate_child_best_effort(child_arc, executor, kill_config);
    drain_stderr_collector(stderr_cancel, stderr_join_handle);
    stop_monitor_if_exited(exited, monitor_should_stop, monitor_handle);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::executor::{MockAgentChild, MockProcessExecutor};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    #[test]
    #[cfg(unix)]
    fn terminate_child_best_effort_targets_process_group_first() {
        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child_arc = Arc::new(Mutex::new(
            Box::new(mock_child) as Box<dyn crate::executor::AgentChild>
        ));

        let executor = MockProcessExecutor::new();
        terminate_child_best_effort(
            &child_arc,
            &executor,
            crate::pipeline::idle_timeout::KillConfig::new(
                Duration::from_millis(1),
                Duration::from_millis(1),
                Duration::from_millis(1),
                Duration::from_millis(1),
                Duration::from_millis(1),
            ),
        );

        let calls = executor.execute_calls_for("kill");
        assert!(
            calls.iter().any(|(_, args, _, _)| {
                args.iter().any(|a| a == "-TERM") && args.iter().any(|a| a == "-12345")
            }),
            "expected terminate path to SIGTERM the process group (-PID)"
        );
    }

    #[test]
    fn cleanup_after_agent_failure_does_not_stop_monitor_if_child_not_confirmed_exited() {
        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child_arc = Arc::new(Mutex::new(
            Box::new(mock_child) as Box<dyn crate::executor::AgentChild>
        ));

        let monitor_should_stop = Arc::new(AtomicBool::new(false));
        let mut monitor_handle: Option<
            std::thread::JoinHandle<crate::pipeline::idle_timeout::MonitorResult>,
        > = None;
        let mut stderr_join_handle: Option<std::thread::JoinHandle<io::Result<String>>> = None;
        let stderr_cancel = Arc::new(AtomicBool::new(false));

        let executor = MockProcessExecutor::new();

        cleanup_after_agent_failure(
            &child_arc,
            &monitor_should_stop,
            &mut monitor_handle,
            &mut stderr_join_handle,
            &stderr_cancel,
            &executor,
            crate::pipeline::idle_timeout::KillConfig::new(
                Duration::from_millis(1),
                Duration::from_millis(1),
                Duration::from_millis(1),
                Duration::from_millis(1),
                Duration::from_millis(1),
            ),
        );

        assert!(
            !monitor_should_stop.load(Ordering::Acquire),
            "monitor stop flag should remain false if child is still running"
        );
    }
}
