//! Core pipeline types (cleanup guards and command results).

use crate::files::{cleanup_generated_files_with_workspace, make_prompt_writable_with_workspace};
use crate::git_helpers::{
    disable_git_wrapper, end_agent_phase_in_repo, try_remove_ralph_dir, uninstall_hooks_in_repo,
    verify_hooks_removed, verify_ralph_dir_removed, verify_wrapper_cleaned, GitHelpers,
};
use crate::logger::Logger;
use crate::{ChildProcessInfo, Workspace};

/// Result of running a command, including stderr for error classification.
pub struct CommandResult {
    /// Exit code from the command (0 = success)
    pub(crate) exit_code: i32,
    /// Standard error output captured from the command
    pub(crate) stderr: String,
    /// Session ID from the agent's init event (if available).
    ///
    /// This is extracted from the agent's JSON output and can be used for
    /// session continuation (XSD retry). Not all agents provide session IDs.
    pub session_id: Option<String>,
    /// Child process status at the time of an idle timeout.
    ///
    /// Only populated when the process was killed due to idle timeout.
    /// Contains the child count and cumulative CPU time when the timeout fired.
    pub(crate) child_status_at_timeout: Option<ChildProcessInfo>,
    /// Explicit timeout evidence from the idle-timeout monitor.
    ///
    /// This field carries definitive proof that a wall-clock timeout was enforced
    /// by the monitor. It is populated ONLY when `MonitorResult::TimedOut` is
    /// returned by the idle-timeout monitor — never inferred from exit codes or
    /// stderr patterns.
    ///
    /// When `Some`, the agent was definitely killed by the idle-timeout mechanism.
    /// When `None`, no explicit timeout evidence exists (exit code 143 alone is
    /// insufficient evidence of timeout as SIGTERM may be sent for other reasons).
    pub(crate) timeout_context: Option<TimeoutContext>,
}

/// Explicit evidence that a timeout was enforced by the idle-timeout monitor.
///
/// This type captures the metadata from `MonitorResult::TimedOut` so that
/// downstream classification can distinguish genuine timeouts from other SIGTERM exits.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimeoutContext {
    /// Whether SIGKILL escalation was required after SIGTERM grace period.
    ///
    /// `false`: Process terminated after SIGTERM within grace period.
    /// `true`: Process did not respond to SIGTERM, required SIGKILL/taskkill.
    pub escalated: bool,
    /// Child process state at the time the timeout was enforced.
    ///
    /// This enables observability and debugging of timeout events.
    pub child_status_at_timeout: Option<ChildProcessInfo>,
}

/// Why the idle-timeout monitor stopped waiting for an agent process.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum IdleTimeoutCause {
    /// The agent timed out with no qualifying child work present.
    NoQualifying,
    /// The agent timed out while child processes still existed but no longer showed
    /// current work that should suppress enforcement.
    Stalled(ChildProcessInfo),
    /// The agent timed out while child snapshots still looked active, but they
    /// stopped showing fresh progress across idle checks.
    StaleActive(ChildProcessInfo),
}

/// RAII guard for agent phase cleanup.
///
/// Ensures that agent phase cleanup happens even if the pipeline is interrupted
/// by panics or early returns. Call `disarm()` on successful completion to
/// prevent cleanup.
pub struct AgentPhaseGuard<'a> {
    /// Mutable reference to git helpers for cleanup operations
    pub git_helpers: &'a mut GitHelpers,
    logger: &'a Logger,
    workspace: &'a dyn Workspace,
    active: bool,
}

impl<'a> AgentPhaseGuard<'a> {
    /// Create a new guard that will clean up on drop unless disarmed.
    pub fn new(
        git_helpers: &'a mut GitHelpers,
        logger: &'a Logger,
        workspace: &'a dyn Workspace,
    ) -> Self {
        Self {
            git_helpers,
            logger,
            workspace,
            active: true,
        }
    }

    /// Disarm the guard, preventing cleanup on drop.
    ///
    /// Call this when the pipeline completes successfully.
    pub const fn disarm(&mut self) {
        self.active = false;
    }
}

impl Drop for AgentPhaseGuard<'_> {
    fn drop(&mut self) {
        if !self.active {
            return;
        }

        // Kill any remaining agent processes. This is the panic/early-return safety net.
        crate::executor::process_registry::kill_all_registered_raw();

        // Restore PROMPT.md write permissions FIRST (most important for user recovery).
        // This is best-effort - we don't want to panic in drop().
        // Even if this run didn't lock PROMPT.md, a prior crashed run may have left it
        // read-only, so we always attempt restoration.
        let _ = make_prompt_writable_with_workspace(self.workspace);

        let repo_root = self.workspace.root();
        end_agent_phase_in_repo(repo_root);
        disable_git_wrapper(self.git_helpers);
        let _ = uninstall_hooks_in_repo(repo_root, self.logger);
        let wrapper_remaining = verify_wrapper_cleaned(repo_root);
        if !wrapper_remaining.is_empty() {
            self.logger.warn(&format!(
                "Wrapper artifacts still present after guard cleanup: {}",
                wrapper_remaining.join(", ")
            ));
        }
        match verify_hooks_removed(repo_root) {
            Ok(remaining) => {
                if !remaining.is_empty() {
                    self.logger.warn(&format!(
                        "Ralph hooks still present after guard cleanup: {}",
                        remaining.join(", ")
                    ));
                }
            }
            Err(err) => {
                self.logger
                    .warn(&format!("Failed to verify hook cleanup: {err}"));
            }
        }
        cleanup_generated_files_with_workspace(self.workspace);
        if !try_remove_ralph_dir(repo_root) {
            let remaining = verify_ralph_dir_removed(repo_root);
            self.logger.warn(&format!(
                "Ralph git dir still present after guard cleanup: {}",
                remaining.join(", ")
            ));
        }
        // Clear global mutexes AFTER all cleanup steps complete.
        crate::git_helpers::clear_agent_phase_global_state();
    }
}
