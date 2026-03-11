use crate::pipeline::idle_timeout::MonitorResult;
use crate::pipeline::prompt::PipelineRuntime;
use std::io;
use std::sync::Arc;

pub(super) fn wait_for_completion_and_collect_stderr(
    child_arc: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<io::Result<String>>>,
    monitor_handle: &mut Option<std::thread::JoinHandle<MonitorResult>>,
    runtime: &PipelineRuntime<'_>,
) -> io::Result<(i32, String, Option<MonitorResult>)> {
    use std::time::Duration;

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    enum WaitOutcome {
        Completed(std::process::ExitStatus),
        TimedOut(MonitorResult),
        /// User pressed Ctrl+C (interrupt flag set). Caller must kill the child.
        UserInterrupted,
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
            Err(panic_payload) => {
                let panic_msg = panic_payload.downcast_ref::<String>().map_or_else(
                    || {
                        panic_payload.downcast_ref::<&str>().map_or_else(
                            || "<unknown panic>".to_string(),
                            std::string::ToString::to_string,
                        )
                    },
                    std::clone::Clone::clone,
                );
                Err(panic_msg)
            }
        }
    }

    fn try_take_stderr_output(
        stderr_join_handle: &mut Option<std::thread::JoinHandle<io::Result<String>>>,
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

    let check_interval = Duration::from_millis(100);
    let outcome = loop {
        match try_take_monitor_result(monitor_handle) {
            Ok(Some(monitor_result)) => {
                if matches!(monitor_result, MonitorResult::TimedOut { .. }) {
                    break WaitOutcome::TimedOut(monitor_result);
                }
            }
            Ok(None) => {}
            Err(panic_msg) => {
                runtime.logger.warn(&format!(
                    "Idle-timeout monitor thread panicked: {panic_msg}. Treating as timeout and forcing termination."
                ));
                break WaitOutcome::TimedOut(MonitorResult::TimedOut {
                    escalated: true,
                    child_status_at_timeout: None,
                });
            }
        }

        // Check for user interrupt (Ctrl+C). Do not consume the flag here —
        // the event loop must still see it to drive the Interrupted state transition.
        if crate::interrupt::is_user_interrupt_requested() {
            break WaitOutcome::UserInterrupted;
        }

        let mut child = child_arc
            .lock()
            .expect("child process mutex poisoned - indicates panic in another thread");
        if let Some(status) = child.try_wait()? {
            break WaitOutcome::Completed(status);
        }
        drop(child);
        std::thread::sleep(check_interval);
    };

    let status = match outcome {
        WaitOutcome::Completed(status) => status,
        WaitOutcome::TimedOut(monitor_result) => {
            let stderr_output = try_take_stderr_output(stderr_join_handle, runtime);
            return Ok((
                super::SIGTERM_EXIT_CODE,
                stderr_output,
                Some(monitor_result),
            ));
        }
        // User pressed Ctrl+C. Return immediately so the caller can kill the child.
        // We use SIGTERM_EXIT_CODE to signal abnormal exit; the caller will enforce
        // termination. The interrupt flag is NOT consumed here — the event loop
        // must still see it to drive the Interrupted state transition.
        WaitOutcome::UserInterrupted => {
            let stderr_output = try_take_stderr_output(stderr_join_handle, runtime);
            return Ok((super::SIGTERM_EXIT_CODE, stderr_output, None));
        }
    };

    let exit_code = status.code().unwrap_or(1);
    if status.code().is_none() && runtime.config.verbosity.is_debug() {
        runtime
            .logger
            .warn("Process terminated by signal (no exit code), treating as failure");
    }

    let stderr_output = match stderr_join_handle.take() {
        Some(handle) => match handle.join() {
            Ok(result) => result?,
            Err(panic_payload) => {
                let panic_msg = panic_payload.downcast_ref::<String>().map_or_else(
                    || {
                        panic_payload.downcast_ref::<&str>().map_or_else(
                            || "<unknown panic>".to_string(),
                            std::string::ToString::to_string,
                        )
                    },
                    std::clone::Clone::clone,
                );
                runtime.logger.warn(&format!(
                    "Stderr collection thread panicked: {panic_msg}. This may indicate a bug."
                ));
                String::new()
            }
        },
        None => String::new(),
    };

    if !stderr_output.is_empty() && runtime.config.verbosity.is_debug() {
        runtime.logger.warn(&format!(
            "Agent stderr output detected ({} bytes):",
            stderr_output.len()
        ));
        for (i, line) in stderr_output.lines().take(5).enumerate() {
            runtime.logger.info(&format!("  stderr[{i}]: {line}"));
        }
        if stderr_output.lines().count() > 5 {
            runtime.logger.info(&format!(
                "  ... ({} more lines, see log file for full output)",
                stderr_output.lines().count() - 5
            ));
        }
    }

    Ok((exit_code, stderr_output, None))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::executor::MockAgentChild;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::workspace::MemoryWorkspace;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    // RAII guard: ensures the interrupt flags are cleared after the test
    // so other tests in the same process are not affected.
    struct InterruptGuard;
    impl Drop for InterruptGuard {
        fn drop(&mut self) {
            let _ = crate::interrupt::take_user_interrupt_request();
            crate::interrupt::reset_user_interrupted_occurred();
        }
    }

    #[test]
    fn wait_loop_exits_promptly_when_user_interrupt_is_requested() {
        // The interrupt flags are process-global; coordinate all test access so
        // parallel tests can't steal each other's pending interrupt requests.
        let _lock = crate::interrupt::interrupt_test_lock();

        // Guarantee clean state.
        let _ = crate::interrupt::take_user_interrupt_request();
        crate::interrupt::reset_user_interrupted_occurred();

        let (child, controller) = MockAgentChild::new_running(0);
        let child_arc: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>> =
            Arc::new(std::sync::Mutex::new(Box::new(child)));

        let mut stderr_join_handle: Option<std::thread::JoinHandle<io::Result<String>>> = None;
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

        // Set the interrupt flag immediately before calling the function under test,
        // and clear it immediately after. Holding the flag only for the duration of
        // wait_for_completion_and_collect_stderr minimises the interference window for
        // parallel tests whose production watcher also checks the global flag.
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
        // Clear the interrupt flag as soon as the function returns.
        let _ = crate::interrupt::take_user_interrupt_request();

        // Clean up the child so it doesn't leak.
        controller.store(false, Ordering::Release);

        // The wait loop must return within 250ms when interrupted,
        // not wait for the child to exit naturally.
        assert!(
            elapsed < Duration::from_millis(250),
            "wait_for_completion_and_collect_stderr blocked for {elapsed:?} after user interrupt; \
             expected early exit within 250ms"
        );

        // The function should return Ok (not an error).
        assert!(
            result.is_ok(),
            "expected Ok result on interrupt, got: {result:?}"
        );
    }

    #[test]
    fn monitor_thread_panic_is_treated_as_timeout_to_avoid_hanging_wait_loop() {
        // The interrupt flags are process-global; coordinate all test access so
        // parallel tests can't steal each other's pending interrupt requests.
        let _lock = crate::interrupt::interrupt_test_lock();

        // Guarantee clean state: if another test set the interrupt flag, the wait loop
        // would short-circuit to UserInterrupted (returning None for monitor_result)
        // instead of detecting the monitor panic as TimedOut.
        let _ = crate::interrupt::take_user_interrupt_request();
        crate::interrupt::reset_user_interrupted_occurred();

        let (child, controller) = MockAgentChild::new_running(0);
        let child_arc: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>> =
            Arc::new(std::sync::Mutex::new(Box::new(child)));

        let mut stderr_join_handle: Option<std::thread::JoinHandle<io::Result<String>>> = None;
        let mut monitor_handle: Option<std::thread::JoinHandle<MonitorResult>> =
            Some(std::thread::spawn(|| panic!("monitor blew up")));

        // Wait for the panicking thread to become finished so the wait loop's
        // monitor-check fires on the first iteration, before the interrupt-check.
        // This prevents interference from parallel tests that hold the global
        // interrupt flag (which would cause the interrupt branch to fire instead).
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

            // The desired behavior is to return promptly on monitor panic (treat as timeout).
            // Increased threshold to 250ms to reduce flakiness on loaded systems.
            assert!(
                elapsed < Duration::from_millis(250),
                "wait loop returned too late ({elapsed:?}); monitor panic was likely swallowed"
            );

            let (exit_code, _stderr, monitor_result) =
                result.expect("expected wait_for_completion to return Ok");
            assert_eq!(exit_code, super::super::SIGTERM_EXIT_CODE);
            assert!(matches!(
                monitor_result,
                Some(MonitorResult::TimedOut { .. })
            ));
        });
    }
}
