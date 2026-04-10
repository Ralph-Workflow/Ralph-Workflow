//! Base types for the idle-timeout monitor.

use crate::executor::{AgentChild, ChildProcessInfo, ProcessExecutor};
use crate::pipeline::idle_timeout::{
    SharedActivityTimestamp, SharedFileActivityTracker, IDLE_TIMEOUT_SECS,
};
use crate::workspace::Workspace;
use std::sync::Arc;
use std::time::Duration;

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
    pub kill_config: crate::pipeline::idle_timeout::io::KillConfig,
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
    /// Optional callback to check if the output file exists (even if incomplete).
    ///
    /// When provided, returning `true` suppresses the idle-timeout kill,
    /// signaling that the agent is actively writing output. This is distinct
    /// from `completion_check` which requires VALID output. Use this to avoid
    /// killing an agent that is mid-write of a large XML file.
    ///
    /// The suppression only resets the idle counter for one tick; if the file
    /// never becomes valid XML, the timeout will eventually fire once stdout/stderr
    /// activity also ceases for the required number of idle confirmations.
    pub partial_completion_check: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
    /// Optional callback to check if an active tool execution is in progress.
    ///
    /// When provided, returning `true` suppresses the idle-timeout kill,
    /// signaling that the agent has an active tool execution in progress
    /// (e.g., a `write` tool call to create the output file). This is distinct
    /// from `partial_completion_check` which only checks file existence.
    ///
    /// A running tool should suppress idle timeout even if:
    /// - The output file doesn't exist yet (tool is still writing)
    /// - No fresh stdout/stderr has been produced
    ///
    /// This allows the idle timeout to be suppressed during parser-observable
    /// tool lifecycle events (tool-start, tool-running, tool-finish, etc.).
    pub tool_activity_check: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
    /// Maximum number of consecutive ticks the tool-activity suppressor may fire
    /// before it is bypassed to allow idle-timeout enforcement to proceed.
    ///
    /// A stuck AtomicU32 counter (due to a protocol anomaly, e.g., the agent sends
    /// ContentBlockStart+ToolUse but stdout closes before the matching MessageStart
    /// arrives) would otherwise suppress the idle timeout indefinitely. After this
    /// many consecutive ticks, the suppressor is treated as inactive — the idle
    /// confirmation counter resumes accumulating, and the timeout fires after
    /// `required_idle_confirmations` additional ticks.
    ///
    /// ## Cap lifecycle
    ///
    /// - **Cap exceeded**: the tool suppressor returns `false` each tick, allowing
    ///   idle confirmations to accumulate normally. The `consecutive_tool_suppression_ticks`
    ///   counter stays at the exceeded value.
    /// - **Tool completes** (check returns `false`): `consecutive_tool_suppression_ticks`
    ///   resets to 0. A future tool execution gets a fresh cap window.
    /// - **Genuine progress** (`reset_idle`): `consecutive_tool_suppression_ticks`
    ///   resets to 0, giving the current or next tool execution a fresh cap window.
    ///   This happens when another suppressor (file/child activity) detects real work.
    ///
    /// Default: 20 (= 10 minutes at 30 s check interval). Set lower in tests.
    pub max_tool_suppression_ticks: u32,
}

impl Default for MonitorConfig {
    fn default() -> Self {
        Self {
            timeout: Duration::from_secs(IDLE_TIMEOUT_SECS),
            check_interval: DEFAULT_CHECK_INTERVAL,
            kill_config: crate::pipeline::idle_timeout::io::DEFAULT_KILL_CONFIG,
            required_idle_confirmations: 2,
            check_child_processes: true,
            completion_check: None,
            partial_completion_check: None,
            tool_activity_check: None,
            max_tool_suppression_ticks: 20,
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
pub(crate) const DEFAULT_CHECK_INTERVAL: Duration = Duration::from_secs(30);

#[derive(Debug, Clone, Copy)]
pub(crate) struct TimeoutEnforcementState {
    pub pid: u32,
    pub escalated: bool,
    pub last_sigkill_sent_at: Option<std::time::Instant>,
    pub triggered_at: std::time::Instant,
}

impl TimeoutEnforcementState {
    pub(crate) fn new(pid: u32, escalated: bool) -> Self {
        Self {
            pid,
            escalated,
            triggered_at: std::time::Instant::now(),
            last_sigkill_sent_at: escalated.then_some(std::time::Instant::now()),
        }
    }
}

pub(crate) struct MonitorParams<'a> {
    pub activity_timestamp: &'a SharedActivityTimestamp,
    pub file_activity_config: Option<&'a FileActivityConfig>,
    pub child: &'a Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    pub should_stop: &'a Arc<std::sync::atomic::AtomicBool>,
    pub executor: &'a Arc<dyn ProcessExecutor>,
    pub child_activity_suppressed: Option<&'a Arc<std::sync::Mutex<Option<ChildProcessInfo>>>>,
    pub timeout: Duration,
    pub check_interval: Duration,
    pub kill_config: crate::pipeline::idle_timeout::io::KillConfig,
    pub required_idle_confirmations: u32,
    pub check_child_processes: bool,
    pub completion_check: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
    pub partial_completion_check: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
    pub tool_activity_check: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
    pub max_tool_suppression_ticks: u32,
}

pub(super) enum EnforcementStep {
    ReturnResult(MonitorResult),
    Continue,
}

pub(super) enum KillResultContinuation {
    TimedOut { escalated: bool },
    AwaitingExit(TimeoutEnforcementState),
    ProcessCompleted,
    Continue,
}

#[derive(Debug)]
pub(crate) enum MonitorLoopAction {
    Return(MonitorResult),
    Continue,
}

pub(super) enum IdleConfirmedAction {
    /// Not enough idle confirmations yet; continue polling.
    Continue,
    /// Child already exited or completion check passed; return the given action.
    Return(MonitorLoopAction),
    /// Agent is stuck; kill and return.
    KillAndReturn(u32),
    /// Completion check passed; kill process and return success.
    CompleteAndKill(u32),
}

pub(crate) struct MonitorLoopState {
    pub(crate) timeout_triggered: Option<TimeoutEnforcementState>,
    pub(crate) last_file_activity: Option<std::time::Instant>,
    pub(crate) consecutive_idle_count: u32,
    pub(crate) last_child_observation: Option<ChildProcessInfo>,
    pub(crate) last_child_info: Option<ChildProcessInfo>,
    pub(crate) child_startup_grace_available: bool,
    /// Number of consecutive ticks the tool-activity suppressor has been active.
    ///
    /// Reset to 0 when: (a) the tool-activity check returns false, or (b)
    /// `reset_idle()` is called (i.e., some other suppressor — file/child activity
    /// — resets the idle state, meaning the agent IS making genuine progress).
    pub(crate) consecutive_tool_suppression_ticks: u32,
    /// Whether the cap-exceeded warning has already been emitted for the current
    /// tool suppression sequence. Reset when the tool becomes inactive or when
    /// genuine progress resets idle state.
    pub(crate) tool_suppression_cap_warned: bool,
}

impl MonitorLoopState {
    pub(crate) fn new() -> Self {
        Self {
            timeout_triggered: None,
            last_file_activity: None,
            consecutive_idle_count: 0,
            last_child_observation: None,
            last_child_info: None,
            child_startup_grace_available: true,
            consecutive_tool_suppression_ticks: 0,
            tool_suppression_cap_warned: false,
        }
    }

    /// Reset all idle state, including the tool suppression tick counter.
    ///
    /// Use this when genuine agent progress is detected (file activity, child-process
    /// activity, non-idle output). The tool suppression counter is zeroed because
    /// genuine progress means the cap should restart from scratch.
    ///
    /// If you need to reset idle state while preserving the tool suppression counter
    /// (e.g. from within the tool suppressor itself), use
    /// [`reset_idle_preserving_tool_suppression`](Self::reset_idle_preserving_tool_suppression).
    pub(crate) fn reset_idle(&mut self) {
        self.consecutive_idle_count = 0;
        self.last_child_observation = None;
        self.last_child_info = None;
        self.child_startup_grace_available = true;
        self.consecutive_tool_suppression_ticks = 0;
        self.tool_suppression_cap_warned = false;
    }

    /// Reset idle state but preserve the tool suppression tick counter.
    ///
    /// Used by the tool-activity suppressor to reset the idle confirmation counter
    /// without resetting its own cap tracking. This avoids the fragile pattern of
    /// calling `reset_idle()` and then immediately restoring the tick counter.
    pub(crate) fn reset_idle_preserving_tool_suppression(&mut self) {
        self.consecutive_idle_count = 0;
        self.last_child_observation = None;
        self.last_child_info = None;
        self.child_startup_grace_available = true;
    }
}

/// Decision produced by the pure tool-suppression policy function.
pub(crate) enum ToolSuppressionAction {
    /// Tool check returned `false`; reset consecutive tick counter.
    Inactive,
    /// Tool is active but the consecutive-tick cap is exceeded; bypass suppressor.
    CapExceeded { ticks: u32 },
    /// Tool is active and within the cap; suppress the idle timeout for this tick.
    Suppress { ticks: u32 },
}

/// Pure policy: determine what to do with the tool-activity suppressor for one tick.
///
/// No I/O, no mutations — takes inputs, returns a decision.
#[must_use]
pub(crate) fn evaluate_tool_suppression(
    check_result: bool,
    current_ticks: u32,
    max_ticks: u32,
) -> ToolSuppressionAction {
    if !check_result {
        return ToolSuppressionAction::Inactive;
    }
    let ticks = current_ticks.saturating_add(1);
    if ticks > max_ticks {
        ToolSuppressionAction::CapExceeded { ticks }
    } else {
        ToolSuppressionAction::Suppress { ticks }
    }
}

/// Pure state effects produced by applying a [`ToolSuppressionAction`].
///
/// No I/O — the boundary wiring in `core.rs` reads these fields to perform
/// mutations and emit diagnostics.
pub(crate) struct ToolSuppressionEffect {
    /// New value for `consecutive_tool_suppression_ticks`.
    pub(crate) ticks: u32,
    /// New value for `tool_suppression_cap_warned`.
    pub(crate) cap_warned: bool,
    /// Whether to reset idle state (preserving tool suppression counters).
    pub(crate) reset_idle: bool,
    /// Optional diagnostic message to emit via stderr.
    pub(crate) diagnostic: Option<String>,
    /// Whether the suppressor is active (i.e., should the tick count as suppressed).
    pub(crate) suppressed: bool,
}

/// Pure policy: given a [`ToolSuppressionAction`], max tick cap, and current
/// warning state, compute the state effects and diagnostics without performing I/O.
#[must_use]
pub(crate) fn resolve_tool_suppression(
    action: ToolSuppressionAction,
    max_ticks: u32,
    already_warned: bool,
) -> ToolSuppressionEffect {
    match action {
        ToolSuppressionAction::Inactive => ToolSuppressionEffect {
            ticks: 0,
            cap_warned: false,
            reset_idle: false,
            diagnostic: None,
            suppressed: false,
        },
        ToolSuppressionAction::CapExceeded { ticks } => ToolSuppressionEffect {
            ticks,
            cap_warned: true,
            reset_idle: false,
            diagnostic: (!already_warned).then(|| {
                format!(
                    "Warning: tool-activity suppressor has been active for {ticks} consecutive \
                     ticks (max {max_ticks}); bypassing suppressor to allow idle-timeout enforcement"
                )
            }),
            suppressed: false,
        },
        ToolSuppressionAction::Suppress { ticks } => ToolSuppressionEffect {
            ticks,
            cap_warned: false,
            reset_idle: true,
            diagnostic: Some(
                "Active tool execution detected during idle timeout; \
                 agent is actively running a tool — continuing monitoring"
                    .to_owned(),
            ),
            suppressed: true,
        },
    }
}
