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
/// are actively working (CPU time advancing) versus merely existing
/// (stalled, zombie, or idle daemon).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChildProcessInfo {
    /// Number of live child processes found.
    pub child_count: u32,
    /// Cumulative CPU time in milliseconds across all child processes.
    ///
    /// The monitor compares this value across consecutive checks:
    /// - If `cpu_time_ms` advances between checks, children are actively working.
    /// - If `cpu_time_ms` is unchanged, children exist but are idle/stalled.
    pub cpu_time_ms: u64,
}

impl ChildProcessInfo {
    /// No child processes found.
    pub const NONE: Self = Self {
        child_count: 0,
        cpu_time_ms: 0,
    };

    /// Whether any child processes exist.
    #[must_use]
    pub const fn has_children(&self) -> bool {
        self.child_count > 0
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
            cpu_time_ms: 42000,
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
}
