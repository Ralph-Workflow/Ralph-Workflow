// pipeline/prompt/io_process_wait/io.rs — boundary module for process wait operations.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Process wait functions for agent execution.

use crate::pipeline::idle_timeout::MonitorResult;
use crate::pipeline::prompt::PipelineRuntime;
use std::sync::Arc;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WaitOutcome {
    Completed(std::process::ExitStatus),
    TimedOut(MonitorResult),
    UserInterrupted,
}

fn downcast_panic_message(panic_payload: Box<dyn std::any::Any + Send>) -> String {
    panic_payload.downcast_ref::<String>().map_or_else(
        || {
            panic_payload.downcast_ref::<&str>().map_or_else(
                || "<unknown panic>".to_string(),
                std::string::ToString::to_string,
            )
        },
        std::clone::Clone::clone,
    )
}

fn try_take_monitor_result(
    monitor_handle: &mut Option<std::thread::JoinHandle<MonitorResult>>,
) -> Result<Option<MonitorResult>, String> {
    let finished = monitor_handle
        .as_ref()
        .is_some_and(std::thread::JoinHandle::is_finished);
    if !finished {
        return Ok(None);
    }
    let handle = monitor_handle
        .take()
        .ok_or_else(|| "monitor handle missing after finished check".to_string())?;
    match handle.join() {
        Ok(result) => Ok(Some(result)),
        Err(payload) => Err(downcast_panic_message(payload)),
    }
}

fn try_take_stderr_output(
    stderr_join_handle: &mut Option<std::thread::JoinHandle<std::io::Result<String>>>,
    runtime: &PipelineRuntime<'_>,
) -> String {
    let finished = stderr_join_handle
        .as_ref()
        .is_some_and(std::thread::JoinHandle::is_finished);
    if !finished {
        return String::new();
    }
    stderr_join_handle.take().map_or(String::new(), |handle| {
        handle.join().map_or(String::new(), |result| {
            result.unwrap_or_else(|e| {
                runtime
                    .logger
                    .warn(&format!("Stderr collection failed after timeout: {e}"));
                String::new()
            })
        })
    })
}

fn join_stderr_handle(
    handle: std::thread::JoinHandle<std::io::Result<String>>,
    runtime: &PipelineRuntime<'_>,
) -> std::io::Result<String> {
    match handle.join() {
        Ok(result) => result,
        Err(payload) => {
            let msg = downcast_panic_message(payload);
            runtime.logger.warn(&format!(
                "Stderr collection thread panicked: {msg}. This may indicate a bug."
            ));
            Ok(String::new())
        }
    }
}

fn collect_stderr_from_handle(
    stderr_join_handle: &mut Option<std::thread::JoinHandle<std::io::Result<String>>>,
    runtime: &PipelineRuntime<'_>,
) -> std::io::Result<String> {
    match stderr_join_handle.take() {
        Some(handle) => join_stderr_handle(handle, runtime),
        None => Ok(String::new()),
    }
}

fn log_stderr_preview_lines(stderr_output: &str, runtime: &PipelineRuntime<'_>) {
    for (i, line) in stderr_output.lines().take(5).enumerate() {
        runtime.logger.info(&format!("  stderr[{i}]: {line}"));
    }
    let total = stderr_output.lines().count();
    if total > 5 {
        runtime.logger.info(&format!(
            "  ... ({} more lines, see log file for full output)",
            total - 5
        ));
    }
}

fn log_stderr_if_debug(stderr_output: &str, runtime: &PipelineRuntime<'_>) {
    if stderr_output.is_empty() || !runtime.config.verbosity.is_debug() {
        return;
    }
    runtime.logger.warn(&format!(
        "Agent stderr output detected ({} bytes):",
        stderr_output.len()
    ));
    log_stderr_preview_lines(stderr_output, runtime);
}

fn monitor_panic_timeout_outcome(panic_msg: &str, runtime: &PipelineRuntime<'_>) -> WaitOutcome {
    runtime.logger.warn(&format!(
        "Idle-timeout monitor thread panicked: {panic_msg}. Treating as timeout and forcing termination."
    ));
    WaitOutcome::TimedOut(MonitorResult::TimedOut {
        escalated: true,
        child_status_at_timeout: None,
    })
}

/// Pure classification of monitor take result.
fn classify_take_result(r: &Option<MonitorResult>) -> ClassifiedTakeResult {
    match r {
        Some(MonitorResult::TimedOut { .. } | MonitorResult::CompleteButWaiting) => {
            ClassifiedTakeResult::TimeoutRelevant
        }
        Some(_) | None => ClassifiedTakeResult::NotTimeoutRelevant,
    }
}

enum ClassifiedTakeResult {
    TimeoutRelevant,
    NotTimeoutRelevant,
}

/// Domain helper: decide the wait outcome based on monitor result (pure).
fn decide_wait_outcome(r: &Option<MonitorResult>) -> Option<WaitOutcome> {
    match classify_take_result(r) {
        ClassifiedTakeResult::TimeoutRelevant => {
            r.as_ref().map(|result| WaitOutcome::TimedOut(*result))
        }
        ClassifiedTakeResult::NotTimeoutRelevant => None,
    }
}

fn interpret_monitor_take_result(
    take_result: Result<Option<MonitorResult>, String>,
    runtime: &PipelineRuntime<'_>,
) -> Option<WaitOutcome> {
    // Boundary: gather input, execute effects, translate result, return
    match take_result {
        // Effect: handle panic case with logging
        Err(panic_msg) => Some(monitor_panic_timeout_outcome(&panic_msg, runtime)),
        // Pure: use domain helper to decide
        Ok(r) => decide_wait_outcome(&r),
    }
}

fn check_monitor_for_timeout(
    monitor_handle: &mut Option<std::thread::JoinHandle<MonitorResult>>,
    runtime: &PipelineRuntime<'_>,
) -> Option<WaitOutcome> {
    interpret_monitor_take_result(try_take_monitor_result(monitor_handle), runtime)
}

fn try_poll_child(
    child_arc: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    executor: &dyn crate::executor::ProcessExecutor,
) -> std::io::Result<Option<WaitOutcome>> {
    let mut child = child_arc
        .lock()
        .expect("child process mutex poisoned - indicates panic in another thread");
    let result = child.try_wait().map(|opt| opt.map(WaitOutcome::Completed));
    // If the child has exited, kill its process group to clean up any zombies.
    // This is best-effort: we ignore the result because the parent has already
    // exited successfully and the kill is just cleanup.
    if result.as_ref().is_ok_and(|opt| opt.is_some()) {
        let child_id = child.id();
        let _ = executor.kill_process_group(child_id);
    }
    result
}

/// One poll iteration: check monitor, interrupt, child. Returns `Some` if done.
fn poll_wait_step(
    child_arc: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    monitor_handle: &mut Option<std::thread::JoinHandle<MonitorResult>>,
    runtime: &PipelineRuntime<'_>,
) -> std::io::Result<Option<WaitOutcome>> {
    if let Some(outcome) = check_monitor_for_timeout(monitor_handle, runtime) {
        return Ok(Some(outcome));
    }
    if crate::interrupt::is_user_interrupt_requested() {
        return Ok(Some(WaitOutcome::UserInterrupted));
    }
    try_poll_child(child_arc, runtime.executor)
}

fn poll_wait_loop(
    child_arc: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    monitor_handle: &mut Option<std::thread::JoinHandle<MonitorResult>>,
    runtime: &PipelineRuntime<'_>,
) -> std::io::Result<WaitOutcome> {
    let check_interval = std::time::Duration::from_millis(100);
    loop {
        if let Some(outcome) = poll_wait_step(child_arc, monitor_handle, runtime)? {
            return Ok(outcome);
        }
        std::thread::sleep(check_interval);
    }
}

fn resolve_exit_code(status: std::process::ExitStatus, runtime: &PipelineRuntime<'_>) -> i32 {
    if status.code().is_none() && runtime.config.verbosity.is_debug() {
        runtime
            .logger
            .warn("Process terminated by signal (no exit code), treating as failure");
    }
    status.code().unwrap_or(1)
}

pub fn wait_for_completion_and_collect_stderr(
    child_arc: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<std::io::Result<String>>>,
    monitor_handle: &mut Option<std::thread::JoinHandle<MonitorResult>>,
    runtime: &PipelineRuntime<'_>,
) -> std::io::Result<(i32, String, Option<MonitorResult>)> {
    let outcome = poll_wait_loop(child_arc, monitor_handle, runtime)?;

    let status = match outcome {
        WaitOutcome::Completed(status) => {
            // Child exited cleanly — parent's wait() in poll_wait_loop has already
            // reaped the child. No zombie reap needed.
            status
        }
        WaitOutcome::TimedOut(monitor_result) => {
            let stderr_output = try_take_stderr_output(stderr_join_handle, runtime);
            return Ok((
                crate::pipeline::prompt::SIGTERM_EXIT_CODE,
                stderr_output,
                Some(monitor_result),
            ));
        }
        WaitOutcome::UserInterrupted => {
            let stderr_output = try_take_stderr_output(stderr_join_handle, runtime);
            return Ok((
                crate::pipeline::prompt::SIGTERM_EXIT_CODE,
                stderr_output,
                None,
            ));
        }
    };

    let exit_code = resolve_exit_code(status, runtime);
    let stderr_output = collect_stderr_from_handle(stderr_join_handle, runtime)?;
    log_stderr_if_debug(&stderr_output, runtime);
    Ok((exit_code, stderr_output, None))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::executor::MockAgentChild;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::workspace::MemoryWorkspace;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    struct InterruptGuard;
    impl Drop for InterruptGuard {
        fn drop(&mut self) {
            let _ = crate::interrupt::take_user_interrupt_request();
            crate::interrupt::reset_user_interrupted_occurred();
        }
    }

    #[test]
    fn wait_loop_exits_promptly_when_user_interrupt_is_requested() {
        let _lock = crate::interrupt::interrupt_test_lock();

        let _ = crate::interrupt::take_user_interrupt_request();
        crate::interrupt::reset_user_interrupted_occurred();

        let (child, controller) = MockAgentChild::new_running(0);
        let child_arc: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>> =
            Arc::new(std::sync::Mutex::new(Box::new(child)));

        let mut stderr_join_handle: Option<std::thread::JoinHandle<std::io::Result<String>>> = None;
        let mut monitor_handle: Option<std::thread::JoinHandle<MonitorResult>> = None;

        let workspace = MemoryWorkspace::new_test();
        let logger = Logger::new(Colors::new());
        let colors = Colors::new();
        let config = crate::config::Config::test_default();
        let mut timer = Timer::new();
        let executor_arc: Arc<dyn crate::executor::ProcessExecutor> =
            Arc::new(crate::executor::MockProcessExecutor::new());

        let runtime = PipelineRuntime {
            timer: &mut timer,
            logger: &logger,
            colors: &colors,
            config: &config,
            executor: executor_arc.as_ref(),
            executor_arc: Arc::clone(&executor_arc),
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
        };

        crate::interrupt::request_user_interrupt();
        let _guard = InterruptGuard;
        let start = Instant::now();
        let result = wait_for_completion_and_collect_stderr(
            &child_arc,
            &mut stderr_join_handle,
            &mut monitor_handle,
            &runtime,
        );
        let elapsed = start.elapsed();
        let _ = crate::interrupt::take_user_interrupt_request();

        controller.store(false, Ordering::Release);

        assert!(
            elapsed < Duration::from_millis(250),
            "wait_for_completion_and_collect_stderr blocked for {elapsed:?} after user interrupt; \
             expected early exit within 250ms"
        );

        assert!(
            result.is_ok(),
            "expected Ok result on interrupt, got: {result:?}"
        );
    }

    #[test]
    fn monitor_thread_panic_is_treated_as_timeout_to_avoid_hanging_wait_loop() {
        let _lock = crate::interrupt::interrupt_test_lock();

        let _ = crate::interrupt::take_user_interrupt_request();
        crate::interrupt::reset_user_interrupted_occurred();

        let (child, controller) = MockAgentChild::new_running(0);
        let child_arc: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>> =
            Arc::new(std::sync::Mutex::new(Box::new(child)));

        let mut stderr_join_handle: Option<std::thread::JoinHandle<std::io::Result<String>>> = None;
        let mut monitor_handle: Option<std::thread::JoinHandle<MonitorResult>> =
            Some(std::thread::spawn(|| panic!("monitor blew up")));

        while !monitor_handle
            .as_ref()
            .is_some_and(std::thread::JoinHandle::is_finished)
        {
            std::thread::yield_now();
        }

        let workspace = MemoryWorkspace::new_test();
        let logger = Logger::new(Colors::new());
        let colors = Colors::new();
        let config = crate::config::Config::test_default();
        let mut timer = Timer::new();

        let executor_arc: Arc<dyn crate::executor::ProcessExecutor> =
            Arc::new(crate::executor::MockProcessExecutor::new());

        let runtime = PipelineRuntime {
            timer: &mut timer,
            logger: &logger,
            colors: &colors,
            config: &config,
            executor: executor_arc.as_ref(),
            executor_arc: Arc::clone(&executor_arc),
            workspace: &workspace,
            workspace_arc: std::sync::Arc::new(workspace.clone()),
        };

        let done = Arc::new(AtomicBool::new(false));
        std::thread::scope(|scope| {
            let done_for_stopper = Arc::clone(&done);
            let controller_for_stopper = Arc::clone(&controller);
            scope.spawn(move || {
                let deadline = Instant::now() + Duration::from_millis(300);
                while Instant::now() < deadline {
                    if done_for_stopper.load(Ordering::Acquire) {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(1));
                }
                controller_for_stopper.store(false, Ordering::Release);
            });

            let start = Instant::now();
            let result = wait_for_completion_and_collect_stderr(
                &child_arc,
                &mut stderr_join_handle,
                &mut monitor_handle,
                &runtime,
            );
            let elapsed = start.elapsed();

            done.store(true, Ordering::Release);
            controller.store(false, Ordering::Release);

            assert!(
                elapsed < Duration::from_millis(250),
                "wait loop returned too late ({elapsed:?}); monitor panic was likely swallowed"
            );

            let (exit_code, _stderr, monitor_result) =
                result.expect("expected wait_for_completion to return Ok");
            assert_eq!(exit_code, crate::pipeline::prompt::SIGTERM_EXIT_CODE);
            assert!(matches!(
                monitor_result,
                Some(MonitorResult::TimedOut { .. })
            ));
        });
    }

    #[test]
    fn try_poll_child_kills_process_group_after_exit() {
        let child = MockAgentChild::new(0);
        let child_arc: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>> =
            Arc::new(std::sync::Mutex::new(Box::new(child)));

        let executor = Arc::new(MockProcessExecutor::new());

        let outcome = try_poll_child(&child_arc, executor.as_ref());
        assert!(outcome.is_ok());
        assert!(outcome.unwrap().is_some());

        assert_eq!(executor.kill_process_group_calls(), vec![12345]);
    }
}
