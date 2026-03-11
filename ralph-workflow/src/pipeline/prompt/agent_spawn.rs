use crate::agents::{is_glm_like_agent, JsonParserType};
use crate::common::{format_argv_for_log, split_command, truncate_text};
use crate::logger::argv_requests_json;
use crate::pipeline::idle_timeout::KillConfig;
use crate::pipeline::idle_timeout::{
    monitor_idle_timeout_with_interval_and_kill_config_and_observer, new_activity_timestamp,
    new_file_activity_tracker, time_since_activity, FileActivityConfig, MonitorConfig,
    MonitorResult, StderrActivityTracker, DEFAULT_KILL_CONFIG, IDLE_TIMEOUT_SECS,
};
use crate::pipeline::types::CommandResult;
use std::io::{self, BufReader};
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use super::types::{PipelineRuntime, PromptCommand};

/// Kill configuration for user-interrupt-triggered subprocess termination.
///
/// Uses a much shorter SIGTERM grace period than the idle-timeout kill config:
/// well-behaved agents respond to SIGTERM within milliseconds, and we want
/// the Ctrl+C response to feel immediate.
const INTERRUPT_KILL_CONFIG: KillConfig = KillConfig::new(
    std::time::Duration::from_millis(500), // SIGTERM grace period
    std::time::Duration::from_millis(50),  // Poll interval
    std::time::Duration::from_millis(200), // SIGKILL confirm timeout
    std::time::Duration::from_secs(2),     // Post-SIGKILL hard cap
    std::time::Duration::from_millis(500), // SIGKILL resend interval
);

/// Spawns the stdout-cancel watcher thread.
///
/// The watcher polls `is_interrupted()` every 50 ms and sets `stdout_cancel = true`
/// when the check returns `true` (e.g. Ctrl+C), so the streaming loop returns
/// promptly. The watcher exits when `monitor_should_stop` is set.
///
/// `is_interrupted` is injectable so tests can use a local `AtomicBool` instead
/// of the process-global interrupt flag, making tests parallel-safe.
fn spawn_stdout_cancel_watcher(
    stdout_cancel: Arc<std::sync::atomic::AtomicBool>,
    monitor_should_stop: Arc<std::sync::atomic::AtomicBool>,
    is_interrupted: impl Fn() -> bool + Send + 'static,
) {
    use std::sync::atomic::Ordering;
    std::thread::spawn(move || {
        let poll = std::time::Duration::from_millis(50);
        loop {
            if monitor_should_stop.load(Ordering::Acquire) {
                return;
            }
            // Cancel immediately on user interrupt (Ctrl+C) so the streaming
            // loop returns and the main thread can check the interrupt flag.
            // Do NOT consume the flag here — the event loop needs it.
            if is_interrupted() {
                stdout_cancel.store(true, Ordering::Release);
                return;
            }
            std::thread::sleep(poll);
        }
    });
}

pub(super) fn run_with_agent_spawn(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    anthropic_env_vars_to_sanitize: &[&str],
) -> io::Result<CommandResult> {
    use std::sync::atomic::{AtomicBool, Ordering};

    let argv = split_command(cmd.cmd_str)?;
    if argv.is_empty() || cmd.cmd_str.trim().is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Agent command is empty or contains only whitespace",
        ));
    }

    let mut argv_for_log = argv.clone();
    argv_for_log.push("<PROMPT>".to_string());
    let display_cmd = truncate_text(&format_argv_for_log(&argv_for_log), 160);
    runtime.logger.info(&format!(
        "Executing: {}{}{}",
        runtime.colors.dim(),
        display_cmd,
        runtime.colors.reset()
    ));

    let is_glm_cmd = is_glm_like_agent(cmd.cmd_str);
    if is_glm_cmd {
        runtime
            .logger
            .info(&format!("GLM command details: {display_cmd}"));
        if argv.iter().any(|arg| arg == "-p") {
            runtime
                .logger
                .info("GLM command includes '-p' flag (correct)");
        } else {
            runtime.logger.warn("GLM command may be missing '-p' flag");
        }
    }

    let _uses_json = cmd.parser_type != JsonParserType::Generic || argv_requests_json(&argv);
    runtime
        .logger
        .info(&format!("Using {} parser...", cmd.parser_type));

    let logfile_path = Path::new(cmd.logfile);
    if let Some(parent) = logfile_path.parent().filter(|p| !p.as_os_str().is_empty()) {
        runtime.workspace.create_dir_all(parent)?;
    }
    runtime.workspace.write(logfile_path, "")?;

    let mut complete_env: std::collections::HashMap<String, String> = std::env::vars().collect();
    for (key, value) in cmd.env_vars {
        complete_env.insert(key.clone(), value.clone());
    }
    super::environment::sanitize_command_env(
        &mut complete_env,
        cmd.env_vars,
        anthropic_env_vars_to_sanitize,
    );

    let config = crate::executor::AgentSpawnConfig {
        command: argv[0].clone(),
        args: argv[1..].to_vec(),
        env: complete_env,
        prompt: cmd.prompt.to_string(),
        logfile: cmd.logfile.to_string(),
        parser_type: cmd.parser_type,
    };

    let agent_handle = match runtime.executor.spawn_agent(&config) {
        Ok(handle) => handle,
        Err(e) => {
            let (exit_code, detail) = match e.kind() {
                io::ErrorKind::NotFound => (127, "command not found"),
                io::ErrorKind::PermissionDenied => (126, "permission denied"),
                io::ErrorKind::ArgumentListTooLong => {
                    (7, "argument list too long (prompt exceeds OS limit)")
                }
                io::ErrorKind::InvalidInput => (22, "invalid input"),
                io::ErrorKind::OutOfMemory => (12, "out of memory"),
                _ => (1, "spawn failed"),
            };
            return Ok(CommandResult {
                exit_code,
                stderr: format!("{}: {} - {}", argv[0], detail, e),
                session_id: None,
                child_status_at_timeout: None,
            });
        }
    };

    let stdout = agent_handle.stdout;
    let stderr = agent_handle.stderr;
    let inner = agent_handle.inner;

    let child_shared = Arc::new(std::sync::Mutex::new(inner));
    let child_for_monitor = Arc::clone(&child_shared);

    let activity_timestamp = new_activity_timestamp();
    let file_activity_config = Some(FileActivityConfig {
        tracker: new_file_activity_tracker(),
        workspace: Arc::clone(&runtime.workspace_arc),
    });
    let stdout_cancel = Arc::new(AtomicBool::new(false));
    let stdout_cancel_for_monitor = Arc::clone(&stdout_cancel);
    let monitor_should_stop = Arc::new(AtomicBool::new(false));
    let monitor_should_stop_clone = Arc::clone(&monitor_should_stop);
    let activity_timestamp_clone = activity_timestamp.clone();
    let child_activity_suppressed = Arc::new(std::sync::Mutex::new(None));
    let child_activity_suppressed_for_monitor = Arc::clone(&child_activity_suppressed);

    // Cancel stdout parsing when the user presses Ctrl+C. Idle-timeout-driven
    // cancellation is set by the monitor only after timeout enforcement actually
    // begins (MonitorResult::TimedOut), so file-activity gating stays consistent
    // between timeout decisions and stdout draining behavior.
    spawn_stdout_cancel_watcher(
        Arc::clone(&stdout_cancel),
        Arc::clone(&monitor_should_stop),
        crate::interrupt::is_user_interrupt_requested,
    );

    let monitor_executor: Arc<dyn crate::executor::ProcessExecutor> =
        std::sync::Arc::clone(&runtime.executor_arc);

    let mut monitor_handle: Option<std::thread::JoinHandle<MonitorResult>> =
        Some(std::thread::spawn(move || {
            let result = monitor_idle_timeout_with_interval_and_kill_config_and_observer(
                &activity_timestamp_clone,
                file_activity_config.as_ref(),
                &child_for_monitor,
                &monitor_should_stop_clone,
                &monitor_executor,
                MonitorConfig {
                    timeout: Duration::from_secs(IDLE_TIMEOUT_SECS),
                    check_interval: Duration::from_secs(30), // 30-second check interval
                    kill_config: DEFAULT_KILL_CONFIG,
                    ..MonitorConfig::default()
                },
                Some(&child_activity_suppressed_for_monitor),
            );
            if matches!(result, MonitorResult::TimedOut { .. }) {
                stdout_cancel_for_monitor.store(true, Ordering::Release);
            }
            result
        }));

    let stderr_activity_timestamp = activity_timestamp.clone();
    let stderr_cancel = Arc::new(AtomicBool::new(false));
    let stderr_cancel_for_thread = Arc::clone(&stderr_cancel);

    let mut stderr_join_handle = Some(std::thread::spawn(move || -> io::Result<String> {
        const STDERR_MAX_BYTES: usize = 512 * 1024;
        let tracked_stderr = StderrActivityTracker::new(stderr, stderr_activity_timestamp);
        let reader = BufReader::new(tracked_stderr);
        super::stderr_collector::collect_stderr_with_cap_and_drain(
            reader,
            STDERR_MAX_BYTES,
            stderr_cancel_for_thread.as_ref(),
        )
    }));

    let activity_timestamp_for_timeout = activity_timestamp.clone();
    if let Err(e) = super::streaming::stream_agent_output_from_handle(
        stdout,
        cmd,
        runtime,
        activity_timestamp,
        &stdout_cancel,
    ) {
        super::cleanup::cleanup_after_agent_failure(
            &child_shared,
            &monitor_should_stop,
            &mut monitor_handle,
            &mut stderr_join_handle,
            &stderr_cancel,
            runtime.executor_arc.as_ref(),
            crate::pipeline::idle_timeout::DEFAULT_KILL_CONFIG,
        );
        return Err(e);
    }

    let (exit_code, stderr_output, monitor_result_early) =
        match super::process_wait::wait_for_completion_and_collect_stderr(
            &child_shared,
            &mut stderr_join_handle,
            &mut monitor_handle,
            runtime,
        ) {
            Ok(v) => v,
            Err(e) => {
                super::cleanup::cleanup_after_agent_failure(
                    &child_shared,
                    &monitor_should_stop,
                    &mut monitor_handle,
                    &mut stderr_join_handle,
                    &stderr_cancel,
                    runtime.executor_arc.as_ref(),
                    crate::pipeline::idle_timeout::DEFAULT_KILL_CONFIG,
                );
                return Err(e);
            }
        };

    // When the user presses Ctrl+C the wait loop exits early (before the process
    // finishes). Kill the child now so it doesn't run on in the background.
    // Use a short grace period: the agent should respond to SIGTERM quickly and
    // we don't want to add noticeable delay to the interrupt response.
    if monitor_result_early.is_none() && crate::interrupt::is_user_interrupt_requested() {
        super::cleanup::terminate_child_best_effort(
            &child_shared,
            runtime.executor_arc.as_ref(),
            INTERRUPT_KILL_CONFIG,
        );
        // Always stop the monitor after an interrupt-triggered kill. We have
        // already sent SIGTERM/SIGKILL to the process group; the event loop will
        // handle all remaining cleanup (RestorePromptPermissions, SaveCheckpoint).
        // Leaving the monitor running would cause it to block for up to
        // IDLE_TIMEOUT_SECS before the pipeline can exit.
        monitor_should_stop.store(true, Ordering::Release);
        super::stderr_collector::cancel_and_join_stderr_collector(
            &stderr_cancel,
            &mut stderr_join_handle,
            std::time::Duration::from_millis(250),
        );
        if stderr_join_handle.is_some() {
            let _ = stderr_join_handle.take();
        }
    } else if matches!(monitor_result_early, Some(MonitorResult::TimedOut { .. })) {
        let exited = super::cleanup::terminate_child_best_effort(
            &child_shared,
            runtime.executor_arc.as_ref(),
            crate::pipeline::idle_timeout::DEFAULT_KILL_CONFIG,
        );

        if exited {
            monitor_should_stop.store(true, Ordering::Release);
        }

        super::stderr_collector::cancel_and_join_stderr_collector(
            &stderr_cancel,
            &mut stderr_join_handle,
            std::time::Duration::from_millis(250),
        );

        // Try again with a larger window. After idle timeout, we prefer to join
        // and avoid leaking a live stderr collector thread.
        if stderr_join_handle.is_some() {
            super::stderr_collector::cancel_and_join_stderr_collector(
                &stderr_cancel,
                &mut stderr_join_handle,
                std::time::Duration::from_secs(2),
            );
        }

        if stderr_join_handle.is_some() {
            runtime
                .logger
                .warn("Stderr collector thread did not exit after cancellation; detaching thread");
            let _ = stderr_join_handle.take();
        }
    } else {
        // Process completed normally; it is safe to stop the monitor/reaper.
        monitor_should_stop.store(true, Ordering::Release);
    }

    let monitor_result: MonitorResult = monitor_result_early
        .or_else(|| monitor_handle.take().and_then(|handle| handle.join().ok()))
        .unwrap_or(MonitorResult::ProcessCompleted);

    let child_activity_suppression_info = *child_activity_suppressed
        .lock()
        .expect("child activity observer mutex poisoned");

    let (final_exit_code, child_status) = match monitor_result {
        MonitorResult::TimedOut {
            escalated,
            child_status_at_timeout,
        } => {
            let idle_duration = time_since_activity(&activity_timestamp_for_timeout);
            let escalation_msg = if escalated {
                if cfg!(windows) {
                    ", force killed (taskkill /F)"
                } else {
                    ", escalated to SIGKILL after SIGTERM grace period"
                }
            } else {
                ""
            };
            let child_msg = child_status_at_timeout.map_or_else(
                || ", no active child processes".to_string(),
                |info| {
                    format!(
                        ", child processes present ({} children, CPU stalled at {}ms)",
                        info.child_count, info.cpu_time_ms
                    )
                },
            );
            runtime.logger.warn(&format!(
                "Agent killed due to idle timeout (no stdout/stderr and no AI file updates for {} seconds, \
                 last activity {:.1}s ago, process exit code was {}{}{}, \
                 kill reason: IDLE_TIMEOUT_MONITOR)",
                IDLE_TIMEOUT_SECS,
                idle_duration.as_secs_f64(),
                exit_code,
                escalation_msg,
                child_msg
            ));
            (super::SIGTERM_EXIT_CODE, child_status_at_timeout)
        }
        MonitorResult::ProcessCompleted => {
            if let Some(info) = child_activity_suppression_info {
                runtime.logger.info(&format!(
                    "idle timeout suppression: child processes remained active \
                     ({} children, CPU advanced to {}ms, signature {})",
                    info.child_count, info.cpu_time_ms, info.descendant_pid_signature
                ));
            }
            (exit_code, None)
        }
    };

    if runtime.config.verbosity.is_verbose() {
        runtime.logger.info(&format!(
            "Phase elapsed: {}",
            runtime.timer.phase_elapsed_formatted()
        ));
    }

    let session_id =
        super::streaming::extract_session_id_from_logfile(cmd.logfile, runtime.workspace);

    Ok(CommandResult {
        exit_code: final_exit_code,
        stderr: stderr_output,
        session_id,
        child_status_at_timeout: child_status,
    })
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    #[test]
    fn stdout_cancel_watcher_sets_cancel_flag_promptly_on_user_interrupt() {
        // The interrupt flags are process-global; coordinate all test access so
        // parallel tests can't steal each other's pending interrupt requests.
        let _lock = crate::interrupt::interrupt_test_lock();

        // Guarantee clean state.
        let _ = crate::interrupt::take_user_interrupt_request();
        crate::interrupt::reset_user_interrupted_occurred();

        // The stdout_cancel_watcher thread should detect the interrupt flag and
        // set stdout_cancel = true within its poll interval (~50ms).
        // Use a local AtomicBool as the interrupt signal instead of the process-global
        // USER_INTERRUPT_REQUESTED flag. This keeps the test parallel-safe.
        let interrupt_flag = Arc::new(AtomicBool::new(false));
        let interrupt_flag_for_watcher = Arc::clone(&interrupt_flag);
        let stdout_cancel = Arc::new(AtomicBool::new(false));
        let monitor_should_stop = Arc::new(AtomicBool::new(false));

        // Spawn via the extracted function, injecting a local interrupt check.
        super::spawn_stdout_cancel_watcher(
            Arc::clone(&stdout_cancel),
            Arc::clone(&monitor_should_stop),
            move || interrupt_flag_for_watcher.load(Ordering::Acquire),
        );

        // Verify that without interrupt, the cancel flag is NOT set immediately.
        std::thread::sleep(Duration::from_millis(20));
        assert!(
            !stdout_cancel.load(Ordering::Acquire),
            "cancel flag should not be set before interrupt"
        );

        // Now signal interrupt via the local flag.
        interrupt_flag.store(true, Ordering::Release);

        // The watcher polls at ~50ms intervals; allow up to 300ms for detection.
        let deadline = Instant::now() + Duration::from_millis(300);
        while Instant::now() < deadline {
            if stdout_cancel.load(Ordering::Acquire) {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }

        // Stop the watcher (in case it didn't exit yet).
        monitor_should_stop.store(true, Ordering::Release);

        assert!(
            stdout_cancel.load(Ordering::Acquire),
            "stdout_cancel_watcher did not set cancel flag within 300ms of user interrupt"
        );
    }
}
