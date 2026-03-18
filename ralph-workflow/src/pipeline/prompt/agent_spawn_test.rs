use crate::common::{format_argv_for_log, split_command, truncate_text};
use crate::pipeline::idle_timeout::{
    monitor_idle_timeout_with_interval_and_kill_config_and_observer, new_activity_timestamp,
    time_since_activity, MonitorResult, StderrActivityTracker,
};
use crate::pipeline::prompt::runtime::cleanup::{
    cleanup_after_agent_failure, terminate_child_best_effort,
};
use crate::pipeline::prompt::runtime::process_wait::wait_for_completion_and_collect_stderr;
use crate::pipeline::prompt::runtime::stderr_collector::{
    cancel_and_join_stderr_collector, collect_stderr_with_cap_and_drain,
};
use crate::pipeline::prompt::runtime::streaming::stream_agent_output_from_handle;
use crate::pipeline::types::{CommandResult, IdleTimeoutCause};
use std::io::{self, BufReader};
use std::path::Path;
use std::sync::Arc;

use super::types::{PipelineRuntime, PromptCommand};

#[cfg(test)]
pub fn run_with_agent_spawn_with_monitor_config(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    anthropic_env_vars_to_sanitize: &[&str],
    idle_timeout: std::time::Duration,
    monitor_check_interval: std::time::Duration,
    kill_config: crate::pipeline::idle_timeout::KillConfig,
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

    let logfile_path = Path::new(cmd.logfile);
    if let Some(parent) = logfile_path.parent().filter(|p| !p.as_os_str().is_empty()) {
        runtime.workspace.create_dir_all(parent)?;
    }
    runtime.workspace.write(logfile_path, "")?;

    let complete_env = super::environment::sanitize_command_env(
        std::env::vars()
            .chain(cmd.env_vars.iter().map(|(k, v)| (k.clone(), v.clone())))
            .collect(),
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
    let stdout_cancel = Arc::new(AtomicBool::new(false));
    let stdout_cancel_for_monitor = Arc::clone(&stdout_cancel);
    let monitor_should_stop = Arc::new(AtomicBool::new(false));
    let monitor_should_stop_clone = Arc::clone(&monitor_should_stop);
    let activity_timestamp_clone = activity_timestamp.clone();
    let child_activity_suppressed = Arc::new(std::sync::Mutex::new(None));
    let child_activity_suppressed_for_monitor = Arc::clone(&child_activity_suppressed);

    let monitor_executor: Arc<dyn crate::executor::ProcessExecutor> =
        std::sync::Arc::clone(&runtime.executor_arc);

    let mut monitor_handle: Option<std::thread::JoinHandle<MonitorResult>> =
        Some(std::thread::spawn(move || {
            let result = monitor_idle_timeout_with_interval_and_kill_config_and_observer(
                &activity_timestamp_clone,
                None, // No file activity config
                &child_for_monitor,
                &monitor_should_stop_clone,
                &monitor_executor,
                crate::pipeline::idle_timeout::MonitorConfig {
                    timeout: idle_timeout,
                    check_interval: monitor_check_interval,
                    kill_config,
                    required_idle_confirmations: 2,
                    check_child_processes: true,
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
        collect_stderr_with_cap_and_drain(
            reader,
            STDERR_MAX_BYTES,
            stderr_cancel_for_thread.as_ref(),
        )
    }));

    let activity_timestamp_for_timeout = activity_timestamp.clone();
    if let Err(e) =
        stream_agent_output_from_handle(stdout, cmd, runtime, activity_timestamp, &stdout_cancel)
    {
        cleanup_after_agent_failure(
            &child_shared,
            &monitor_should_stop,
            &mut monitor_handle,
            &mut stderr_join_handle,
            &stderr_cancel,
            runtime.executor_arc.as_ref(),
            kill_config,
        );
        return Err(e);
    }

    let (exit_code, stderr_output, monitor_result_early) =
        match wait_for_completion_and_collect_stderr(
            &child_shared,
            &mut stderr_join_handle,
            &mut monitor_handle,
            runtime,
        ) {
            Ok(v) => v,
            Err(e) => {
                cleanup_after_agent_failure(
                    &child_shared,
                    &monitor_should_stop,
                    &mut monitor_handle,
                    &mut stderr_join_handle,
                    &stderr_cancel,
                    runtime.executor_arc.as_ref(),
                    kill_config,
                );
                return Err(e);
            }
        };

    if matches!(monitor_result_early, Some(MonitorResult::TimedOut { .. })) {
        let exited =
            terminate_child_best_effort(&child_shared, runtime.executor_arc.as_ref(), kill_config);
        if exited {
            monitor_should_stop.store(true, Ordering::Release);
        }
        cancel_and_join_stderr_collector(
            &stderr_cancel,
            &mut stderr_join_handle,
            std::time::Duration::from_millis(250),
        );
    }

    if !matches!(monitor_result_early, Some(MonitorResult::TimedOut { .. })) {
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
            let idle_timeout_cause =
                child_status_at_timeout.map_or(IdleTimeoutCause::NoQualifying, |info| {
                    if info.has_stalled_children() {
                        IdleTimeoutCause::Stalled(info)
                    } else if info.has_currently_active_children() {
                        IdleTimeoutCause::StaleActive(info)
                    } else {
                        IdleTimeoutCause::NoQualifying
                    }
                });
            let child_msg = match idle_timeout_cause {
                IdleTimeoutCause::NoQualifying => ", no active child processes".to_string(),
                IdleTimeoutCause::Stalled(info) => {
                    format!(
                        ", child processes present but not currently active (0 active of {} total, CPU at {}ms)",
                        info.child_count, info.cpu_time_ms
                    )
                }
                IdleTimeoutCause::StaleActive(info) => {
                    format!(
                        ", child processes still looked active but showed no fresh progress ({} active of {} total, CPU stalled at {}ms)",
                        info.active_child_count, info.child_count, info.cpu_time_ms
                    )
                }
            };
            runtime.logger.warn(&format!(
                "Agent killed due to idle timeout (no stdout/stderr for {:.1} seconds, \
                 last activity {:.1}s ago, process exit code was {}{}{}, \
                 kill reason: IDLE_TIMEOUT_MONITOR)",
                idle_timeout.as_secs_f64(),
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
                    "idle timeout suppression: child processes showed fresh progress and remained relevant \
                     ({} active of {} total, CPU at {}ms, signature {})",
                    info.active_child_count,
                    info.child_count,
                    info.cpu_time_ms,
                    info.descendant_pid_signature
                ));
            }
            (exit_code, None)
        }
    };

    let session_id =
        super::streaming::extract_session_id_from_logfile(cmd.logfile, runtime.workspace);

    Ok(CommandResult {
        exit_code: final_exit_code,
        stderr: stderr_output,
        session_id,
        child_status_at_timeout: child_status,
    })
}
