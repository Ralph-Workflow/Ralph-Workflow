// pipeline/prompt/io_agent_spawn/io.rs — boundary module for agent spawning.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Agent spawning for prompt execution.
//
// This module contains OS-boundary code for agent spawning.

use crate::agents::{is_glm_like_agent, JsonParserType};
use crate::common::{format_argv_for_log, split_command, truncate_text};
use crate::files::llm_output_extraction::has_valid_xml_output;
use crate::logger::argv_requests_json;
use crate::pipeline::idle_timeout::KillConfig;
use crate::pipeline::idle_timeout::{
    monitor_idle_timeout_with_interval_and_kill_config_and_observer, new_activity_timestamp,
    new_file_activity_tracker, time_since_activity, FileActivityConfig, MonitorConfig,
    MonitorResult, StderrActivityTracker, DEFAULT_KILL_CONFIG, IDLE_TIMEOUT_SECS,
};
use crate::pipeline::prompt::io_streaming;
use crate::pipeline::prompt::types::{PipelineRuntime, PromptCommand};
use crate::pipeline::prompt::SIGTERM_EXIT_CODE;
use crate::pipeline::types::{CommandResult, IdleTimeoutCause};
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

const INTERRUPT_KILL_CONFIG: KillConfig = KillConfig::new(
    std::time::Duration::from_millis(500),
    std::time::Duration::from_millis(50),
    std::time::Duration::from_millis(200),
    std::time::Duration::from_secs(2),
    std::time::Duration::from_millis(500),
);

fn map_spawn_error_to_result(e: std::io::Error, argv0: &str) -> CommandResult {
    let (exit_code, detail) = match e.kind() {
        std::io::ErrorKind::NotFound => (127, "command not found"),
        std::io::ErrorKind::PermissionDenied => (126, "permission denied"),
        std::io::ErrorKind::ArgumentListTooLong => {
            (7, "argument list too long (prompt exceeds OS limit)")
        }
        std::io::ErrorKind::InvalidInput => (22, "invalid input"),
        std::io::ErrorKind::OutOfMemory => (12, "out of memory"),
        _ => (1, "spawn failed"),
    };
    CommandResult {
        exit_code,
        stderr: format!("{}: {} - {}", argv0, detail, e),
        session_id: None,
        child_status_at_timeout: None,
    }
}

fn log_glm_info(argv: &[String], display_cmd: &str, logger: &crate::logger::Logger) {
    logger.info(&format!("GLM command details: {display_cmd}"));
    if argv.iter().any(|arg| arg == "-p") {
        logger.info("GLM command includes '-p' flag (correct)");
    } else {
        logger.warn("GLM command may be missing '-p' flag");
    }
}

fn format_idle_cause_msg(idle_timeout_cause: &IdleTimeoutCause) -> String {
    match idle_timeout_cause {
        IdleTimeoutCause::NoQualifying => ", no active child processes".to_string(),
        IdleTimeoutCause::Stalled(info) => format!(
            ", child processes present but not currently active (0 active of {} total, CPU at {}ms)",
            info.child_count, info.cpu_time_ms
        ),
        IdleTimeoutCause::StaleActive(info) => format!(
            ", child processes still looked active but showed no fresh progress ({} active of {} total, CPU stalled at {}ms)",
            info.active_child_count, info.child_count, info.cpu_time_ms
        ),
    }
}

fn classify_idle_timeout_cause(
    child_status_at_timeout: Option<crate::executor::ChildProcessInfo>,
) -> IdleTimeoutCause {
    child_status_at_timeout.map_or(IdleTimeoutCause::NoQualifying, |info| {
        if info.has_stalled_children() {
            IdleTimeoutCause::Stalled(info)
        } else if info.has_currently_active_children() {
            IdleTimeoutCause::StaleActive(info)
        } else {
            IdleTimeoutCause::NoQualifying
        }
    })
}

fn make_completion_check(
    path: Option<&std::path::Path>,
    workspace: &std::sync::Arc<dyn crate::workspace::Workspace>,
) -> Option<std::sync::Arc<dyn Fn() -> bool + Send + Sync>> {
    let path = path?.to_owned();
    let workspace = std::sync::Arc::clone(workspace);
    Some(std::sync::Arc::new(move || {
        has_valid_xml_output(workspace.as_ref(), &path)
    }))
}

fn spawn_stdout_cancel_watcher(
    stdout_cancel: Arc<std::sync::atomic::AtomicBool>,
    monitor_should_stop: Arc<std::sync::atomic::AtomicBool>,
    child_shared: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    activity_timestamp: crate::pipeline::idle_timeout::SharedActivityTimestamp,
    is_interrupted: impl Fn() -> bool + Send + 'static,
) {
    use std::sync::atomic::Ordering;

    const CHILD_EXIT_STDOUT_DRAIN_GRACE: std::time::Duration =
        std::time::Duration::from_millis(200);

    std::thread::spawn(move || {
        let poll = std::time::Duration::from_millis(50);
        loop {
            if monitor_should_stop.load(Ordering::Acquire) {
                return;
            }

            let child_exited = {
                let mut child = child_shared
                    .lock()
                    .expect("child process mutex poisoned - indicates panic in another thread");
                matches!(child.try_wait(), Ok(Some(_)))
            };

            let may_cancel_for_child_exit = child_exited
                && crate::pipeline::idle_timeout::time_since_activity(&activity_timestamp)
                    >= CHILD_EXIT_STDOUT_DRAIN_GRACE;

            if is_interrupted() || may_cancel_for_child_exit {
                stdout_cancel.store(true, Ordering::Release);
                return;
            }
            std::thread::sleep(poll);
        }
    });
}

fn cancel_and_drain_stderr(
    stderr_cancel: &Arc<std::sync::atomic::AtomicBool>,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<Result<String, std::io::Error>>>,
    runtime: &PipelineRuntime<'_>,
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
        runtime
            .logger
            .warn("Stderr collector thread did not exit after cancellation; detaching thread");
        let _ = stderr_join_handle.take();
    }
}

fn handle_interrupt_cleanup(
    child_shared: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    monitor_should_stop: &Arc<std::sync::atomic::AtomicBool>,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<Result<String, std::io::Error>>>,
    stderr_cancel: &Arc<std::sync::atomic::AtomicBool>,
    runtime: &PipelineRuntime<'_>,
) {
    use std::sync::atomic::Ordering;
    super::runtime::terminate_child_best_effort(
        child_shared,
        runtime.executor_arc.as_ref(),
        INTERRUPT_KILL_CONFIG,
    );
    monitor_should_stop.store(true, Ordering::Release);
    super::io_stderr_collector::cancel_and_join_stderr_collector(
        stderr_cancel,
        stderr_join_handle,
        std::time::Duration::from_millis(250),
    );
    let _ = stderr_join_handle.take();
}

fn handle_timeout_cleanup(
    child_shared: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    monitor_should_stop: &Arc<std::sync::atomic::AtomicBool>,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<Result<String, std::io::Error>>>,
    stderr_cancel: &Arc<std::sync::atomic::AtomicBool>,
    runtime: &PipelineRuntime<'_>,
) {
    use std::sync::atomic::Ordering;
    let exited = super::runtime::terminate_child_best_effort(
        child_shared,
        runtime.executor_arc.as_ref(),
        crate::pipeline::idle_timeout::DEFAULT_KILL_CONFIG,
    );
    if exited {
        monitor_should_stop.store(true, Ordering::Release);
    }
    cancel_and_drain_stderr(stderr_cancel, stderr_join_handle, runtime);
}

fn is_interrupt_cleanup_needed(monitor_result_early: &Option<MonitorResult>) -> bool {
    monitor_result_early.is_none() && crate::interrupt::is_user_interrupt_requested()
}

fn is_timeout_cleanup_needed(monitor_result_early: &Option<MonitorResult>) -> bool {
    matches!(
        monitor_result_early,
        Some(MonitorResult::TimedOut { .. }) | Some(MonitorResult::CompleteButWaiting)
    )
}

enum PostMonitorCleanupAction {
    Interrupt,
    Timeout,
    Complete,
}

fn post_monitor_cleanup_action(
    monitor_result_early: &Option<MonitorResult>,
) -> PostMonitorCleanupAction {
    if is_interrupt_cleanup_needed(monitor_result_early) {
        PostMonitorCleanupAction::Interrupt
    } else if is_timeout_cleanup_needed(monitor_result_early) {
        PostMonitorCleanupAction::Timeout
    } else {
        PostMonitorCleanupAction::Complete
    }
}

fn handle_post_monitor_cleanup(
    child_shared: &Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    monitor_should_stop: &Arc<std::sync::atomic::AtomicBool>,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<Result<String, std::io::Error>>>,
    stderr_cancel: &Arc<std::sync::atomic::AtomicBool>,
    monitor_result_early: &Option<MonitorResult>,
    runtime: &PipelineRuntime<'_>,
) {
    use std::sync::atomic::Ordering;
    match post_monitor_cleanup_action(monitor_result_early) {
        PostMonitorCleanupAction::Interrupt => {
            handle_interrupt_cleanup(
                child_shared,
                monitor_should_stop,
                stderr_join_handle,
                stderr_cancel,
                runtime,
            );
        }
        PostMonitorCleanupAction::Timeout => {
            handle_timeout_cleanup(
                child_shared,
                monitor_should_stop,
                stderr_join_handle,
                stderr_cancel,
                runtime,
            );
        }
        PostMonitorCleanupAction::Complete => monitor_should_stop.store(true, Ordering::Release),
    }
}

fn format_escalation_msg(escalated: bool) -> &'static str {
    if !escalated {
        return "";
    }
    if cfg!(windows) {
        ", force killed (taskkill /F)"
    } else {
        ", escalated to SIGKILL after SIGTERM grace period"
    }
}

/// Pure exit code resolution result.
#[derive(Debug, Clone)]
enum ExitCodeResolution {
    TimedOut {
        escalated: bool,
        exit_code: i32,
        child_status_at_timeout: Option<crate::executor::ChildProcessInfo>,
    },
    CompleteButWaiting,
    ProcessCompleted {
        exit_code: i32,
        child_activity_suppression_info: Option<crate::executor::ChildProcessInfo>,
    },
}

/// Pure classification of monitor result to exit code resolution.
fn classify_monitor_result(
    monitor_result: MonitorResult,
    exit_code: i32,
    child_activity_suppression_info: Option<crate::executor::ChildProcessInfo>,
) -> ExitCodeResolution {
    match monitor_result {
        MonitorResult::TimedOut {
            escalated,
            child_status_at_timeout,
        } => ExitCodeResolution::TimedOut {
            escalated,
            exit_code,
            child_status_at_timeout,
        },
        MonitorResult::CompleteButWaiting => ExitCodeResolution::CompleteButWaiting,
        MonitorResult::ProcessCompleted => ExitCodeResolution::ProcessCompleted {
            exit_code,
            child_activity_suppression_info,
        },
    }
}

/// Pure: format exit diagnostic messages from resolution data.
/// Returns a list of (level, message) pairs to be logged by the boundary.
fn format_exit_diagnostics(
    resolution: &ExitCodeResolution,
    idle_duration_secs: f64,
) -> Vec<(&'static str, String)> {
    match resolution {
        ExitCodeResolution::TimedOut {
            escalated,
            exit_code,
            child_status_at_timeout,
        } => {
            let escalation_msg = format_escalation_msg(*escalated);
            let idle_timeout_cause =
                classify_idle_timeout_cause(child_status_at_timeout.as_ref().copied());
            let child_msg = format_idle_cause_msg(&idle_timeout_cause);
            vec![(
                "warn",
                format!(
                    "Agent killed due to idle timeout (no stdout/stderr and no AI file updates for {} seconds, \
                     last activity {:.1}s ago, process exit code was {}{}{}, \
                     kill reason: IDLE_TIMEOUT_MONITOR)",
                    IDLE_TIMEOUT_SECS,
                    idle_duration_secs,
                    exit_code,
                    escalation_msg,
                    child_msg
                ),
            )]
        }
        ExitCodeResolution::CompleteButWaiting => vec![(
            "info",
            "Agent output ready; process was idle-but-done and was forcibly terminated \
             (complete-but-waiting). Treating as success."
                .to_string(),
        )],
        ExitCodeResolution::ProcessCompleted {
            child_activity_suppression_info: Some(info),
            ..
        } => vec![(
            "info",
            format!(
                "idle timeout suppression: child processes showed fresh progress and remained relevant \
                 ({} active of {} total, CPU at {}ms, signature {})",
                info.active_child_count,
                info.child_count,
                info.cpu_time_ms,
                info.descendant_pid_signature
            ),
        )],
        ExitCodeResolution::ProcessCompleted { .. } => vec![],
    }
}

/// Emit exit diagnostics at the boundary (logging effects).
fn emit_exit_diagnostics(
    resolution: &ExitCodeResolution,
    activity_timestamp: &crate::pipeline::idle_timeout::SharedActivityTimestamp,
    runtime: &PipelineRuntime<'_>,
) {
    let idle_duration = time_since_activity(activity_timestamp);
    let diagnostics = format_exit_diagnostics(resolution, idle_duration.as_secs_f64());
    for (level, msg) in diagnostics {
        match level {
            "warn" => runtime.logger.warn(&msg),
            _ => runtime.logger.info(&msg),
        }
    }
}

fn resolve_final_exit_code(
    monitor_result: MonitorResult,
    exit_code: i32,
    activity_timestamp: &crate::pipeline::idle_timeout::SharedActivityTimestamp,
    child_activity_suppression_info: Option<crate::executor::ChildProcessInfo>,
    runtime: &PipelineRuntime<'_>,
) -> (i32, Option<crate::executor::ChildProcessInfo>) {
    let resolution =
        classify_monitor_result(monitor_result, exit_code, child_activity_suppression_info);

    emit_exit_diagnostics(&resolution, activity_timestamp, runtime);

    match resolution {
        ExitCodeResolution::TimedOut {
            escalated: _,
            exit_code: _,
            child_status_at_timeout,
        } => (SIGTERM_EXIT_CODE, child_status_at_timeout),
        ExitCodeResolution::CompleteButWaiting => (0, None),
        ExitCodeResolution::ProcessCompleted {
            exit_code,
            child_activity_suppression_info: _,
        } => (exit_code, None),
    }
}

/// Parsed and validated command arguments.
struct ParsedArgv {
    argv: Vec<String>,
    display_cmd: String,
}

fn parse_and_validate_argv(cmd: &PromptCommand<'_>) -> Result<ParsedArgv, std::io::Error> {
    let argv = split_command(cmd.cmd_str)?;
    if argv.is_empty() || cmd.cmd_str.trim().is_empty() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "Agent command is empty or contains only whitespace",
        ));
    }
    let argv_for_log: Vec<_> = argv
        .iter()
        .chain(std::iter::once(&"<PROMPT>".to_string()))
        .cloned()
        .collect();
    let display_cmd = truncate_text(&format_argv_for_log(&argv_for_log), 160);
    Ok(ParsedArgv { argv, display_cmd })
}

fn log_command_info(parsed: &ParsedArgv, cmd: &PromptCommand<'_>, runtime: &PipelineRuntime<'_>) {
    runtime.logger.info(&format!(
        "Executing: {}{}{}",
        runtime.colors.dim(),
        parsed.display_cmd,
        runtime.colors.reset()
    ));
    if is_glm_like_agent(cmd.cmd_str) {
        log_glm_info(&parsed.argv, &parsed.display_cmd, runtime.logger);
    }
    let _uses_json = cmd.parser_type != JsonParserType::Generic || argv_requests_json(&parsed.argv);
    runtime
        .logger
        .info(&format!("Using {} parser...", cmd.parser_type));
}

fn prepare_logfile_and_spawn_config(
    parsed: &ParsedArgv,
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    anthropic_env_vars_to_sanitize: &[&str],
) -> Result<crate::executor::AgentSpawnConfig, std::io::Error> {
    let logfile_path = Path::new(cmd.logfile);
    if let Some(parent) = logfile_path.parent().filter(|p| !p.as_os_str().is_empty()) {
        runtime.workspace.create_dir_all(parent)?;
    }
    runtime.workspace.write(logfile_path, "")?;
    let complete_env = crate::pipeline::prompt::environment::sanitize_command_env(
        std::env::vars()
            .chain(cmd.env_vars.iter().map(|(k, v)| (k.clone(), v.clone())))
            .collect(),
        cmd.env_vars,
        anthropic_env_vars_to_sanitize,
    );
    Ok(crate::executor::AgentSpawnConfig {
        command: parsed.argv[0].clone(),
        args: parsed.argv[1..].to_vec(),
        env: complete_env,
        prompt: cmd.prompt.to_string(),
        logfile: cmd.logfile.to_string(),
        parser_type: cmd.parser_type,
    })
}

struct SpawnedAgentHandles {
    child_shared: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    activity_timestamp: crate::pipeline::idle_timeout::SharedActivityTimestamp,
    stdout_cancel: Arc<std::sync::atomic::AtomicBool>,
    monitor_should_stop: Arc<std::sync::atomic::AtomicBool>,
    stderr_cancel: Arc<std::sync::atomic::AtomicBool>,
    child_activity_suppressed: Arc<std::sync::Mutex<Option<crate::executor::ChildProcessInfo>>>,
    monitor_handle: Option<std::thread::JoinHandle<MonitorResult>>,
    stderr_join_handle: Option<std::thread::JoinHandle<Result<String, std::io::Error>>>,
}

/// `Ok(Ok((handles, stdout)))` on success, `Ok(Err(result))` on spawn error.
type SpawnAgentResult = Result<
    Result<(SpawnedAgentHandles, Box<dyn std::io::Read + Send>), CommandResult>,
    std::io::Error,
>;

struct SharedMonitorState {
    child_shared: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    child_for_monitor: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>>,
    activity_timestamp: crate::pipeline::idle_timeout::SharedActivityTimestamp,
    file_activity_config: Option<FileActivityConfig>,
    stdout_cancel: Arc<std::sync::atomic::AtomicBool>,
    stdout_cancel_for_monitor: Arc<std::sync::atomic::AtomicBool>,
    monitor_should_stop: Arc<std::sync::atomic::AtomicBool>,
    monitor_should_stop_clone: Arc<std::sync::atomic::AtomicBool>,
    activity_timestamp_clone: crate::pipeline::idle_timeout::SharedActivityTimestamp,
    child_activity_suppressed: Arc<std::sync::Mutex<Option<crate::executor::ChildProcessInfo>>>,
    child_activity_suppressed_for_monitor:
        Arc<std::sync::Mutex<Option<crate::executor::ChildProcessInfo>>>,
    monitor_executor: Arc<dyn crate::executor::ProcessExecutor>,
}

fn create_shared_monitor_state(
    inner: Box<dyn crate::executor::AgentChild>,
    workspace_arc: &Arc<dyn crate::workspace::Workspace>,
    executor_arc: &Arc<dyn crate::executor::ProcessExecutor>,
) -> SharedMonitorState {
    use std::sync::atomic::AtomicBool;
    let child_shared = Arc::new(std::sync::Mutex::new(inner));
    let child_for_monitor = Arc::clone(&child_shared);
    let activity_timestamp = new_activity_timestamp();
    let file_activity_config = Some(FileActivityConfig {
        tracker: new_file_activity_tracker(),
        workspace: Arc::clone(workspace_arc),
    });
    let stdout_cancel = Arc::new(AtomicBool::new(false));
    let stdout_cancel_for_monitor = Arc::clone(&stdout_cancel);
    let monitor_should_stop = Arc::new(AtomicBool::new(false));
    let monitor_should_stop_clone = Arc::clone(&monitor_should_stop);
    let activity_timestamp_clone = activity_timestamp.clone();
    let child_activity_suppressed = Arc::new(std::sync::Mutex::new(None));
    let child_activity_suppressed_for_monitor = Arc::clone(&child_activity_suppressed);
    let monitor_executor = Arc::clone(executor_arc) as Arc<dyn crate::executor::ProcessExecutor>;
    SharedMonitorState {
        child_shared,
        child_for_monitor,
        activity_timestamp,
        file_activity_config,
        stdout_cancel,
        stdout_cancel_for_monitor,
        monitor_should_stop,
        monitor_should_stop_clone,
        activity_timestamp_clone,
        child_activity_suppressed,
        child_activity_suppressed_for_monitor,
        monitor_executor,
    }
}

fn spawn_monitor_thread(
    state: SharedMonitorState,
    completion_check: Option<std::sync::Arc<dyn Fn() -> bool + Send + Sync>>,
) -> std::thread::JoinHandle<MonitorResult> {
    use std::sync::atomic::Ordering;
    let SharedMonitorState {
        child_for_monitor,
        activity_timestamp_clone,
        file_activity_config,
        monitor_should_stop_clone,
        monitor_executor,
        stdout_cancel_for_monitor,
        child_activity_suppressed_for_monitor,
        ..
    } = state;
    std::thread::spawn(move || {
        let result = monitor_idle_timeout_with_interval_and_kill_config_and_observer(
            &activity_timestamp_clone,
            file_activity_config.as_ref(),
            &child_for_monitor,
            &monitor_should_stop_clone,
            &monitor_executor,
            MonitorConfig {
                timeout: Duration::from_secs(IDLE_TIMEOUT_SECS),
                check_interval: Duration::from_secs(30),
                kill_config: DEFAULT_KILL_CONFIG,
                completion_check,
                ..MonitorConfig::default()
            },
            Some(&child_activity_suppressed_for_monitor),
        );
        if matches!(
            result,
            MonitorResult::TimedOut { .. } | MonitorResult::CompleteButWaiting
        ) {
            stdout_cancel_for_monitor.store(true, Ordering::Release);
        }
        result
    })
}

fn spawn_stderr_collector_thread(
    stderr: Box<dyn std::io::Read + Send>,
    stderr_activity_timestamp: crate::pipeline::idle_timeout::SharedActivityTimestamp,
    stderr_cancel_for_thread: Arc<std::sync::atomic::AtomicBool>,
) -> std::thread::JoinHandle<Result<String, std::io::Error>> {
    std::thread::spawn(move || -> Result<String, std::io::Error> {
        const STDERR_MAX_BYTES: usize = 512 * 1024;
        let tracked_stderr = StderrActivityTracker::new(stderr, stderr_activity_timestamp);
        let reader = std::io::BufReader::new(tracked_stderr);
        super::io_stderr_collector::collect_stderr_with_cap_and_drain(
            reader,
            STDERR_MAX_BYTES,
            stderr_cancel_for_thread.as_ref(),
        )
    })
}

fn build_spawned_agent_handles(
    state: SharedMonitorState,
    stderr_cancel: Arc<std::sync::atomic::AtomicBool>,
    stderr_join_handle: Option<std::thread::JoinHandle<Result<String, std::io::Error>>>,
    completion_check: Option<std::sync::Arc<dyn Fn() -> bool + Send + Sync>>,
) -> SpawnedAgentHandles {
    let child_shared = Arc::clone(&state.child_shared);
    let activity_timestamp = state.activity_timestamp.clone();
    let stdout_cancel = Arc::clone(&state.stdout_cancel);
    let monitor_should_stop = Arc::clone(&state.monitor_should_stop);
    let child_activity_suppressed = Arc::clone(&state.child_activity_suppressed);
    let monitor_handle = Some(spawn_monitor_thread(state, completion_check));
    SpawnedAgentHandles {
        child_shared,
        activity_timestamp,
        stdout_cancel,
        monitor_should_stop,
        stderr_cancel,
        child_activity_suppressed,
        monitor_handle,
        stderr_join_handle,
    }
}

/// Returns `Ok(Ok((handles, stdout)))` on success, `Ok(Err(result))` on spawn error.
fn spawn_agent_with_monitoring(
    spawn_config: crate::executor::AgentSpawnConfig,
    runtime: &PipelineRuntime<'_>,
    argv0: &str,
    completion_check: Option<std::sync::Arc<dyn Fn() -> bool + Send + Sync>>,
) -> SpawnAgentResult {
    use std::sync::atomic::AtomicBool;
    let agent_handle = match runtime.executor.spawn_agent(&spawn_config) {
        Ok(h) => h,
        Err(e) => return Ok(Err(map_spawn_error_to_result(e, argv0))),
    };
    let state = create_shared_monitor_state(
        agent_handle.inner,
        &runtime.workspace_arc,
        &runtime.executor_arc,
    );
    spawn_stdout_cancel_watcher(
        Arc::clone(&state.stdout_cancel),
        Arc::clone(&state.monitor_should_stop),
        Arc::clone(&state.child_shared),
        state.activity_timestamp.clone(),
        crate::interrupt::is_user_interrupt_requested,
    );
    let stderr_cancel = Arc::new(AtomicBool::new(false));
    let stderr_join_handle = Some(spawn_stderr_collector_thread(
        agent_handle.stderr,
        state.activity_timestamp.clone(),
        Arc::clone(&stderr_cancel),
    ));
    let handles =
        build_spawned_agent_handles(state, stderr_cancel, stderr_join_handle, completion_check);
    Ok(Ok((handles, agent_handle.stdout)))
}

fn cleanup_handles(handles: &mut SpawnedAgentHandles, runtime: &PipelineRuntime<'_>) {
    super::runtime::cleanup_after_agent_failure(
        &handles.child_shared,
        &handles.monitor_should_stop,
        &mut handles.monitor_handle,
        &mut handles.stderr_join_handle,
        &handles.stderr_cancel,
        runtime.executor_arc.as_ref(),
        crate::pipeline::idle_timeout::DEFAULT_KILL_CONFIG,
    );
}

fn stream_and_wait(
    stdout: Box<dyn std::io::Read + Send>,
    handles: &mut SpawnedAgentHandles,
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
) -> Result<(i32, String, Option<MonitorResult>), std::io::Error> {
    if let Err(e) = crate::pipeline::prompt::io::streaming::stream_agent_output_from_handle(
        stdout,
        cmd,
        runtime,
        handles.activity_timestamp.clone(),
        &handles.stdout_cancel,
    ) {
        cleanup_handles(handles, runtime);
        return Err(e);
    }
    match super::io_process_wait::wait_for_completion_and_collect_stderr(
        &handles.child_shared,
        &mut handles.stderr_join_handle,
        &mut handles.monitor_handle,
        runtime,
    ) {
        Ok(v) => Ok(v),
        Err(e) => {
            cleanup_handles(handles, runtime);
            Err(e)
        }
    }
}

fn finalize_result(
    handles: &mut SpawnedAgentHandles,
    exit_code: i32,
    stderr_output: String,
    monitor_result_early: Option<MonitorResult>,
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
) -> CommandResult {
    handle_post_monitor_cleanup(
        &handles.child_shared,
        &handles.monitor_should_stop,
        &mut handles.stderr_join_handle,
        &handles.stderr_cancel,
        &monitor_result_early,
        runtime,
    );
    let monitor_result: MonitorResult = monitor_result_early
        .or_else(|| handles.monitor_handle.take().and_then(|h| h.join().ok()))
        .unwrap_or(MonitorResult::ProcessCompleted);
    let child_activity_suppression_info = *handles
        .child_activity_suppressed
        .lock()
        .expect("child activity observer mutex poisoned");
    let (final_exit_code, child_status) = resolve_final_exit_code(
        monitor_result,
        exit_code,
        &handles.activity_timestamp,
        child_activity_suppression_info,
        runtime,
    );
    if runtime.config.verbosity.is_verbose() {
        runtime.logger.info(&format!(
            "Phase elapsed: {}",
            runtime.timer.phase_elapsed_formatted()
        ));
    }
    let session_id = io_streaming::extract_session_id_from_logfile(cmd.logfile, runtime.workspace);
    CommandResult {
        exit_code: final_exit_code,
        stderr: stderr_output,
        session_id,
        child_status_at_timeout: child_status,
    }
}

pub(crate) fn run_with_agent_spawn(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    anthropic_env_vars_to_sanitize: &[&str],
) -> Result<CommandResult, std::io::Error> {
    let parsed = parse_and_validate_argv(cmd)?;
    log_command_info(&parsed, cmd, runtime);
    let spawn_config =
        prepare_logfile_and_spawn_config(&parsed, cmd, runtime, anthropic_env_vars_to_sanitize)?;
    let argv0 = parsed.argv[0].clone();
    let completion_check =
        make_completion_check(cmd.completion_output_path, &runtime.workspace_arc);
    let (mut handles, stdout) =
        match spawn_agent_with_monitoring(spawn_config, runtime, &argv0, completion_check)? {
            Ok(pair) => pair,
            Err(result) => return Ok(result),
        };
    let (exit_code, stderr_output, monitor_result_early) =
        stream_and_wait(stdout, &mut handles, cmd, runtime)?;
    Ok(finalize_result(
        &mut handles,
        exit_code,
        stderr_output,
        monitor_result_early,
        cmd,
        runtime,
    ))
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    #[test]
    fn stdout_cancel_watcher_sets_cancel_flag_promptly_on_user_interrupt() {
        use crate::executor::MockAgentChild;

        let _lock = crate::interrupt::interrupt_test_lock();

        let _ = crate::interrupt::take_user_interrupt_request();
        crate::interrupt::reset_user_interrupted_occurred();

        let interrupt_flag = Arc::new(AtomicBool::new(false));
        let interrupt_flag_for_watcher = Arc::clone(&interrupt_flag);
        let stdout_cancel = Arc::new(AtomicBool::new(false));
        let monitor_should_stop = Arc::new(AtomicBool::new(false));
        let (child, _controller) = MockAgentChild::new_running(0);
        let child_shared: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>> =
            Arc::new(std::sync::Mutex::new(Box::new(child)));

        super::spawn_stdout_cancel_watcher(
            Arc::clone(&stdout_cancel),
            Arc::clone(&monitor_should_stop),
            Arc::clone(&child_shared),
            crate::pipeline::idle_timeout::new_activity_timestamp(),
            move || interrupt_flag_for_watcher.load(Ordering::Acquire),
        );

        std::thread::sleep(Duration::from_millis(20));
        assert!(
            !stdout_cancel.load(Ordering::Acquire),
            "cancel flag should not be set before interrupt"
        );

        interrupt_flag.store(true, Ordering::Release);

        let deadline = Instant::now() + Duration::from_millis(300);
        while Instant::now() < deadline {
            if stdout_cancel.load(Ordering::Acquire) {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }

        monitor_should_stop.store(true, Ordering::Release);

        assert!(
            stdout_cancel.load(Ordering::Acquire),
            "stdout_cancel_watcher did not set cancel flag within 300ms of user interrupt"
        );
    }

    #[test]
    fn stdout_cancel_watcher_sets_cancel_flag_when_child_process_exits() {
        use crate::executor::MockAgentChild;

        let (child, controller) = MockAgentChild::new_running(0);
        let child_shared: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>> =
            Arc::new(std::sync::Mutex::new(Box::new(child)));

        let stdout_cancel = Arc::new(AtomicBool::new(false));
        let monitor_should_stop = Arc::new(AtomicBool::new(false));

        super::spawn_stdout_cancel_watcher(
            Arc::clone(&stdout_cancel),
            Arc::clone(&monitor_should_stop),
            Arc::clone(&child_shared),
            crate::pipeline::idle_timeout::new_activity_timestamp(),
            || false,
        );

        std::thread::sleep(Duration::from_millis(20));
        controller.store(false, Ordering::Release);

        let deadline = Instant::now() + Duration::from_millis(600);
        while Instant::now() < deadline {
            if stdout_cancel.load(Ordering::Acquire) {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }

        monitor_should_stop.store(true, Ordering::Release);

        assert!(
            stdout_cancel.load(Ordering::Acquire),
            "stdout_cancel_watcher did not set cancel flag within 300ms of child exit"
        );
    }
}
