//! Type definitions for process execution.
//!
//! This module defines the core types used for process execution,
//! including process output, agent spawn configuration, and agent child handles.

use crate::agents::JsonParserType;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io;

/// Output from an executed process.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProcessOutput {
    /// The exit status of process.
    pub status: std::process::ExitStatus,
    /// The captured stdout as a UTF-8 string.
    pub stdout: String,
    /// The captured stderr as a UTF-8 string.
    pub stderr: String,
}

impl ProcessOutput {
    /// Returns `true` if the process exited with a zero status code.
    #[must_use]
    pub fn succeeded(&self) -> bool {
        self.status.success()
    }

    /// Returns the process exit code, or `-1` if unavailable.
    #[must_use]
    pub fn exit_code(&self) -> i32 {
        self.status.code().unwrap_or(-1)
    }
}

/// Handle to a spawned process that requires stdin interaction.
///
/// This wraps a `std::process::Child` to expose only the domain-relevant
/// surface: stdin writing and process completion. The raw `Child` is kept
/// private to prevent raw effect types from leaking into domain code.
pub struct SpawnedProcess {
    /// The stdin handle for writing input to the process.
    pub stdin: Option<std::process::ChildStdin>,
    /// The inner child process handle.
    pub(crate) inner: std::process::Child,
}

/// Configuration for spawning an agent process with streaming support.
///
/// This struct contains all the parameters needed to spawn an agent subprocess,
/// including the command, arguments, environment variables, prompt, and parser type.
#[derive(Debug, Clone)]
pub struct AgentSpawnConfig {
    /// The command to execute (e.g., "claude", "codex").
    pub command: String,
    /// Arguments to pass to the command.
    pub args: Vec<String>,
    /// Environment variables to set for the process.
    pub env: HashMap<String, String>,
    /// The prompt to pass to the agent.
    pub prompt: String,
    /// Path to the log file for output.
    pub logfile: String,
    /// The JSON parser type to use for output.
    pub parser_type: JsonParserType,
}

/// Result of spawning an agent process.
///
/// This wraps the spawned child process with handles to stdout and stderr
/// for streaming output in real-time.
pub struct AgentChildHandle {
    /// The stdout stream for reading agent output.
    pub stdout: Box<dyn io::Read + Send>,
    /// The stderr stream for reading error output.
    pub stderr: Box<dyn io::Read + Send>,
    /// The inner child process handle.
    pub inner: Box<dyn AgentChild>,
}

/// Trait for interacting with a spawned agent child process.
///
/// This trait abstracts the `std::process::Child` operations needed for
/// agent monitoring and output collection. It allows mocking in tests.
pub trait AgentChild: Send + std::fmt::Debug {
    /// Get the process ID.
    fn id(&self) -> u32;

    /// Wait for the process to complete and return the exit status.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn wait(&mut self) -> io::Result<std::process::ExitStatus>;

    /// Try to wait without blocking.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn try_wait(&mut self) -> io::Result<Option<std::process::ExitStatus>>;
}

/// Wrapper for real `std::process::Child`.
pub struct RealAgentChild(pub std::process::Child);

impl std::fmt::Debug for RealAgentChild {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RealAgentChild")
            .field("id", &self.0.id())
            .finish()
    }
}

impl AgentChild for RealAgentChild {
    fn id(&self) -> u32 {
        self.0.id()
    }

    fn wait(&mut self) -> io::Result<std::process::ExitStatus> {
        self.0.wait()
    }

    fn try_wait(&mut self) -> io::Result<Option<std::process::ExitStatus>> {
        self.0.try_wait()
    }
}

/// Information about child processes of a given parent.
///
/// Used by the idle-timeout monitor to determine whether child processes
/// are currently active versus merely present but stalled.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChildProcessInfo {
    /// Number of live child processes found.
    pub child_count: u32,
    /// Number of descendants that are currently in an active process state.
    ///
    /// This counts descendants that are actively running or blocked in a
    /// state that still indicates current work, rather than merely sleeping
    /// with historical CPU usage.
    #[serde(default)]
    pub active_child_count: u32,
    /// Cumulative CPU time in milliseconds across all child processes.
    ///
    /// This remains useful for observability when child work is present but no
    /// longer current enough to suppress the idle timeout.
    pub cpu_time_ms: u64,
    /// Deterministic signature of the current descendant PID set.
    ///
    /// This lets the idle-timeout monitor distinguish "the same child subtree is
    /// still running" from "the old subtree exited and a new one replaced it
    /// between polls", even when the cumulative CPU time drops or resets.
    #[serde(default)]
    pub descendant_pid_signature: u64,
}

impl ChildProcessInfo {
    /// No child processes found.
    pub const NONE: Self = Self {
        child_count: 0,
        active_child_count: 0,
        cpu_time_ms: 0,
        descendant_pid_signature: 0,
    };

    /// Whether any child processes exist.
    #[must_use]
    pub const fn has_children(&self) -> bool {
        self.child_count > 0
    }

    /// Whether any child processes are currently active enough to suppress timeout.
    #[must_use]
    pub const fn has_currently_active_children(&self) -> bool {
        self.active_child_count > 0
    }

    /// Whether descendants are still observable but no longer show current work.
    #[must_use]
    pub const fn has_stalled_children(&self) -> bool {
        self.has_children() && !self.has_currently_active_children()
    }

    /// Whether the latest snapshot proves fresh current work relative to a
    /// previous observation.
    ///
    /// Fresh work can show up either as additional CPU time from the same
    /// descendant set or as a still-active replacement descendant set, which is
    /// common for shells and build tools that churn worker PIDs between polls.
    #[must_use]
    pub const fn shows_fresh_progress_since(&self, previous: Self) -> bool {
        self.has_currently_active_children()
            && (self.cpu_time_ms > previous.cpu_time_ms
                || self.descendant_pid_signature != previous.descendant_pid_signature)
    }
}

/// Result of an agent command execution (for testing).
///
/// This is used by `MockProcessExecutor` to return mock results without
/// actually spawning processes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentCommandResult {
    /// Exit code from the command (0 = success).
    pub exit_code: i32,
    /// Standard error from the command.
    pub stderr: String,
}

impl AgentCommandResult {
    /// Create a successful result.
    #[must_use]
    pub const fn success() -> Self {
        Self {
            exit_code: 0,
            stderr: String::new(),
        }
    }

    /// Create a failed result with the given exit code and stderr.
    pub fn failure(exit_code: i32, stderr: impl Into<String>) -> Self {
        Self {
            exit_code,
            stderr: stderr.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn child_process_info_serde_round_trip() {
        let info = ChildProcessInfo {
            child_count: 3,
            active_child_count: 2,
            cpu_time_ms: 42000,
            descendant_pid_signature: 12345,
        };
        let json = serde_json::to_string(&info).unwrap();
        let restored: ChildProcessInfo = serde_json::from_str(&json).unwrap();
        assert_eq!(info, restored);
    }

    #[test]
    fn child_process_info_none_serde_round_trip() {
        let info = ChildProcessInfo::NONE;
        let json = serde_json::to_string(&info).unwrap();
        let restored: ChildProcessInfo = serde_json::from_str(&json).unwrap();
        assert_eq!(info, restored);
    }

    #[test]
    fn child_process_info_distinguishes_stalled_children_from_current_work() {
        let stalled = ChildProcessInfo {
            child_count: 2,
            active_child_count: 0,
            cpu_time_ms: 4200,
            descendant_pid_signature: 99,
        };
        let active = ChildProcessInfo {
            child_count: 2,
            active_child_count: 1,
            cpu_time_ms: 4200,
            descendant_pid_signature: 99,
        };

        assert!(stalled.has_stalled_children());
        assert!(!active.has_stalled_children());
        assert!(!ChildProcessInfo::NONE.has_stalled_children());
    }

    #[test]
    fn child_process_info_requires_same_subtree_and_cpu_growth_for_fresh_progress() {
        let previous = ChildProcessInfo {
            child_count: 1,
            active_child_count: 1,
            cpu_time_ms: 200,
            descendant_pid_signature: 7,
        };
        let fresh = ChildProcessInfo {
            child_count: 1,
            active_child_count: 1,
            cpu_time_ms: 300,
            descendant_pid_signature: 7,
        };
        let stale = ChildProcessInfo {
            child_count: 1,
            active_child_count: 1,
            cpu_time_ms: 200,
            descendant_pid_signature: 7,
        };
        let replaced = ChildProcessInfo {
            child_count: 1,
            active_child_count: 1,
            cpu_time_ms: 400,
            descendant_pid_signature: 8,
        };
        let replaced_without_current_activity = ChildProcessInfo {
            child_count: 1,
            active_child_count: 0,
            cpu_time_ms: 450,
            descendant_pid_signature: 9,
        };

        assert!(fresh.shows_fresh_progress_since(previous));
        assert!(!stale.shows_fresh_progress_since(previous));
        assert!(replaced.shows_fresh_progress_since(previous));
        assert!(!replaced_without_current_activity.shows_fresh_progress_since(previous));
    }
}
