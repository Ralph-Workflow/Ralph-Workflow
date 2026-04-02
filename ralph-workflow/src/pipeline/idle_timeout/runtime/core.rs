//! Enforcement and policy logic for the idle-timeout monitor.

use crate::executor::{AgentChild, ChildProcessInfo, ProcessExecutor};
use crate::pipeline::idle_timeout::io::{
    force_kill_best_effort, kill_process, KillConfig, KillResult,
};
use crate::pipeline::idle_timeout::{
    is_idle_timeout_exceeded, time_since_activity, SharedActivityTimestamp,
};

use super::base::{
    EnforcementStep, IdleConfirmedAction, KillResultContinuation, MonitorLoopAction,
    MonitorLoopState, MonitorParams, MonitorResult, TimeoutEnforcementState,
};
use super::sleep::sleep_until_next_check_or_stop;

use std::sync::Arc;
use std::time::{Duration, SystemTime};

// ============================================================================
// Child process wait utilities
// ============================================================================

fn try_wait_child(child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>) -> bool {
    let mut locked_child = child
        .lock()
        .expect("child process mutex poisoned - indicates panic in another thread");
    matches!(locked_child.try_wait(), Ok(Some(_)))
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

// ============================================================================
// Kill escalation
// ============================================================================

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

// ============================================================================
// Reaper thread
// ============================================================================

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

// ============================================================================
// Enforcement continuation
// ============================================================================

pub enum TimeoutEnforcementContinuation {
    Exited,
    HardCapReached,
    Continue(TimeoutEnforcementState),
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

// ============================================================================
// Kill result processing
// ============================================================================

fn kill_failed_continuation(should_stop: &std::sync::atomic::AtomicBool) -> KillResultContinuation {
    use std::sync::atomic::Ordering;
    if should_stop.load(Ordering::Acquire) {
        KillResultContinuation::ProcessCompleted
    } else {
        KillResultContinuation::Continue
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
            KillResultContinuation::AwaitingExit(TimeoutEnforcementState::new(child_id, escalated))
        }
        KillResult::Failed => kill_failed_continuation(should_stop),
    }
}

fn apply_kill_result(
    kill_result: KillResult,
    child_id: u32,
    last_child_info: Option<ChildProcessInfo>,
    should_stop: &std::sync::atomic::AtomicBool,
    s: &mut MonitorLoopState,
) -> Option<MonitorResult> {
    match process_kill_result(kill_result, child_id, should_stop) {
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

pub fn kill_child_and_apply(
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

// ============================================================================
// Completion check
// ============================================================================

/// Pure: check if the completion callback returns true.
pub fn completion_check_passes(
    completion_check: Option<&Arc<dyn Fn() -> bool + Send + Sync>>,
) -> bool {
    completion_check.is_some_and(|c| c())
}

/// Pure policy: determine MonitorResult when child has exited during enforcement.
fn result_on_enforcement_exit(
    state: &TimeoutEnforcementState,
    last_child_info: Option<ChildProcessInfo>,
    completion_check: Option<&Arc<dyn Fn() -> bool + Send + Sync>>,
    _child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
) -> MonitorResult {
    // If we escalated to SIGKILL (state.escalated = true), any subsequent exit
    // is a timeout - we decided to kill the process and it is now dead.
    // The escalated flag means we DECIDED to use SIGKILL, so the outcome
    // (process no longer running) is a timeout, not clean completion.
    if state.escalated {
        return MonitorResult::TimedOut {
            escalated: true,
            child_status_at_timeout: last_child_info,
        };
    }
    if completion_check_passes(completion_check) {
        MonitorResult::CompleteButWaiting
    } else {
        MonitorResult::ProcessCompleted
    }
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

fn completion_ready_action(params: &MonitorParams<'_>) -> Option<MonitorLoopAction> {
    if !completion_check_passes(params.completion_check.as_ref()) {
        return None;
    }

    if let Some(child_id) = try_get_child_id(params.child) {
        try_complete_but_waiting_and_kill(child_id, params);
        return Some(MonitorLoopAction::Return(MonitorResult::CompleteButWaiting));
    }

    Some(determine_result_on_child_exit(
        params.completion_check.as_ref(),
    ))
}

// ============================================================================
// Child progress tracking helpers
// ============================================================================

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

pub fn handle_child_with_children(
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

// ============================================================================
// File activity check
// ============================================================================

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
    fac: &super::base::FileActivityConfig,
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
pub fn handle_timeout_exceeded(
    params: &MonitorParams<'_>,
    s: &mut MonitorLoopState,
) -> MonitorLoopAction {
    eprintln!(
        "Idle timeout exceeded: no output activity for {} seconds",
        time_since_activity(params.activity_timestamp).as_secs()
    );
    check_timeout_suppressors(params, s).unwrap_or_else(|| handle_idle_confirmed(params, s))
}

// ============================================================================
// Idle confirmed handling
// ============================================================================

#[expect(clippy::print_stderr, reason = "boundary module - runtime diagnostics")]
fn log_idle_progress(consecutive: u32, required: u32) {
    eprintln!(
        "Idle confirmed {consecutive}/{required} times; waiting for next check interval before kill"
    );
}

/// Compute the idle-confirmed policy — pure function, no side effects.
/// Encapsulates all branching so `handle_idle_confirmed` stays thin.
pub fn compute_idle_confirmed_action(
    params: &MonitorParams<'_>,
    s: &MonitorLoopState,
) -> IdleConfirmedAction {
    let consecutive = s.consecutive_idle_count;
    if consecutive < params.required_idle_confirmations {
        return IdleConfirmedAction::Continue;
    }

    let Some(child_id) = try_get_child_id(params.child) else {
        return IdleConfirmedAction::Return(determine_result_on_child_exit(
            params.completion_check.as_ref(),
        ));
    };

    if completion_check_passes(params.completion_check.as_ref()) {
        return IdleConfirmedAction::CompleteAndKill(child_id);
    }

    IdleConfirmedAction::KillAndReturn(child_id)
}

pub fn handle_idle_confirmed(
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

// ============================================================================
// Enforcement phase handling
// ============================================================================

/// Handle the enforcement phase - pure policy part.
/// Returns the enforcement continuation result.
pub fn handle_enforcement_phase(
    state: TimeoutEnforcementState,
    last_child_info: Option<ChildProcessInfo>,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    executor: &Arc<dyn ProcessExecutor>,
    should_stop: &Arc<std::sync::atomic::AtomicBool>,
    kill_config: KillConfig,
    completion_check: Option<&Arc<dyn Fn() -> bool + Send + Sync>>,
) -> (EnforcementStep, Option<TimeoutEnforcementState>) {
    match advance_timeout_enforcement(state, child, executor, kill_config) {
        TimeoutEnforcementContinuation::Exited => {
            let result =
                result_on_enforcement_exit(&state, last_child_info, completion_check, child);
            (EnforcementStep::ReturnResult(result), None)
        }
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

/// Thin boundary: dispatch to enforcement phase handler.
pub fn dispatch_enforcement_phase(
    params: &MonitorParams<'_>,
    s: &mut MonitorLoopState,
) -> MonitorLoopAction {
    let state = match s.timeout_triggered.take() {
        Some(st) => st,
        None => return MonitorLoopAction::Continue,
    };
    let (step, next_state) = compute_enforcement_phase_result(params, state, s.last_child_info);
    s.timeout_triggered = next_state;
    enforcement_step_to_action(step)
}

/// Pure: compute the enforcement phase result.
/// Returns (EnforcementStep, Option<TimeoutEnforcementState>)
fn compute_enforcement_phase_result(
    params: &MonitorParams<'_>,
    state: TimeoutEnforcementState,
    last_child_info: Option<ChildProcessInfo>,
) -> (EnforcementStep, Option<TimeoutEnforcementState>) {
    // If should_stop is set while in enforcement, return ProcessCompleted
    // instead of continuing enforcement (which could return TimedOut via HardCapReached).
    if should_stop_during_enforcement(params) {
        return (
            EnforcementStep::ReturnResult(MonitorResult::ProcessCompleted),
            None,
        );
    }
    handle_enforcement_phase(
        state,
        last_child_info,
        params.child,
        params.executor,
        params.should_stop,
        params.kill_config,
        params.completion_check.as_ref(),
    )
}

/// Pure: check if should_stop is set during enforcement.
fn should_stop_during_enforcement(params: &MonitorParams<'_>) -> bool {
    use std::sync::atomic::Ordering;
    params.should_stop.load(Ordering::Acquire)
}

fn enforcement_step_to_action(step: EnforcementStep) -> MonitorLoopAction {
    match step {
        EnforcementStep::ReturnResult(r) => MonitorLoopAction::Return(r),
        EnforcementStep::Continue => MonitorLoopAction::Continue,
    }
}

// ============================================================================
// Tick policy computation
// ============================================================================

/// Policy decision for one enforcement tick — pure, no side effects.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TickPolicy {
    /// Completion check passed proactively; return immediately with CompleteButWaiting.
    CompletionReady,
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

fn should_stop_before_timeout(params: &MonitorParams<'_>, s: &MonitorLoopState) -> bool {
    use std::sync::atomic::Ordering;
    s.timeout_triggered.is_none() && params.should_stop.load(Ordering::Acquire)
}

fn sleep_check_stops_early(params: &MonitorParams<'_>, s: &MonitorLoopState) -> bool {
    s.timeout_triggered.is_none()
        && sleep_until_next_check_or_stop(params.should_stop.as_ref(), params.check_interval)
}

/// Pure: check if completion is ready.
fn check_completion_ready(params: &MonitorParams<'_>) -> bool {
    completion_check_passes(params.completion_check.as_ref())
}

/// Pure: check if child already exited.
fn check_child_exited(
    timeout_triggered: bool,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
) -> bool {
    !timeout_triggered && try_wait_child(child)
}

/// Pure: check if stop conditions are met.
fn check_stop_conditions(params: &MonitorParams<'_>, s: &MonitorLoopState) -> bool {
    should_stop_before_timeout(params, s) || sleep_check_stops_early(params, s)
}

/// Pure: check if idle timeout is exceeded.
fn check_idle_timeout(activity_timestamp: &SharedActivityTimestamp, timeout: Duration) -> bool {
    is_idle_timeout_exceeded(activity_timestamp, timeout)
}

/// Pure: determine TickPolicy from pre-computed check results.
/// Uses flat match with guards to avoid nesting depth.
fn tick_policy_from_checks(
    completion_ready: bool,
    child_already_exited: bool,
    stop_conditions_met: bool,
    timeout_triggered: bool,
    not_idle: bool,
) -> TickPolicy {
    match () {
        _ if completion_ready => TickPolicy::CompletionReady,
        _ if child_already_exited => TickPolicy::ChildAlreadyExited,
        _ if stop_conditions_met => TickPolicy::StopConditionsMet,
        _ if timeout_triggered => TickPolicy::EnforcementPhase,
        _ if not_idle => TickPolicy::NotIdle,
        _ => TickPolicy::IdleTimeoutExceeded,
    }
}

/// Compute the policy decision for this tick — thin boundary.
pub fn compute_tick_policy(
    timeout_triggered: bool,
    child: &Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    activity_timestamp: &SharedActivityTimestamp,
    timeout: Duration,
    params: &MonitorParams<'_>,
    s: &MonitorLoopState,
) -> TickPolicy {
    tick_policy_from_checks(
        check_completion_ready(params),
        check_child_exited(timeout_triggered, child),
        check_stop_conditions(params, s),
        timeout_triggered,
        !check_idle_timeout(activity_timestamp, timeout),
    )
}

/// Dispatch directive from policy computation — enables thin boundary.
#[derive(Debug)]
enum PolicyDispatch {
    /// Return this action directly (no side effects needed).
    Return(MonitorLoopAction),
    /// NotIdle: return Continue after resetting idle state.
    NotIdle,
    /// CompletionReady: call completion_ready_action and return result.
    CompletionReady,
    /// EnforcementPhase: dispatch to enforcement handler.
    EnforcementPhase,
    /// IdleTimeoutExceeded: handle timeout escalation.
    IdleTimeoutExceeded,
}

/// Pure: determine the dispatch directive from policy.
fn compute_dispatch(policy: TickPolicy) -> PolicyDispatch {
    match policy {
        TickPolicy::CompletionReady => PolicyDispatch::CompletionReady,
        TickPolicy::ChildAlreadyExited => {
            PolicyDispatch::Return(MonitorLoopAction::Return(MonitorResult::ProcessCompleted))
        }
        TickPolicy::StopConditionsMet => {
            PolicyDispatch::Return(MonitorLoopAction::Return(MonitorResult::ProcessCompleted))
        }
        TickPolicy::EnforcementPhase => PolicyDispatch::EnforcementPhase,
        TickPolicy::NotIdle => PolicyDispatch::NotIdle,
        TickPolicy::IdleTimeoutExceeded => PolicyDispatch::IdleTimeoutExceeded,
    }
}

pub fn handle_enforcement_tick(
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
    match compute_dispatch(policy) {
        PolicyDispatch::Return(action) => action,
        PolicyDispatch::NotIdle => {
            s.reset_idle();
            MonitorLoopAction::Continue
        }
        PolicyDispatch::CompletionReady => completion_ready_action(params)
            .unwrap_or(MonitorLoopAction::Return(MonitorResult::CompleteButWaiting)),
        PolicyDispatch::EnforcementPhase => dispatch_enforcement_phase(params, s),
        PolicyDispatch::IdleTimeoutExceeded => handle_timeout_exceeded(params, s),
    }
}
