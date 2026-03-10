//! `ProcessExecutor` trait definition.
//!
//! This module defines the trait abstraction for process execution,
//! enabling dependency injection for testing.

use super::{AgentChildHandle, AgentSpawnConfig, ProcessOutput, RealAgentChild};
use std::io;
use std::path::Path;

/// Trait for executing external processes.
///
/// This trait abstracts process execution to allow dependency injection.
/// Production code uses `RealProcessExecutor` which calls actual commands.
/// Test code can use `MockProcessExecutor` to control process behavior.
///
/// Only external process execution is abstracted. Internal code logic is never mocked.
pub trait ProcessExecutor: Send + Sync + std::fmt::Debug {
    /// Execute a command with given arguments and return its output.
    ///
    /// # Arguments
    ///
    /// * `command` - The program to execute
    /// * `args` - Command-line arguments to pass to the program
    /// * `env` - Environment variables to set for the process (optional)
    /// * `workdir` - Working directory for the process (optional)
    ///
    /// # Returns
    ///
    /// Returns a `ProcessOutput` containing exit status, stdout, and stderr.
    ///
    /// # Errors
    ///
    /// Returns an error if command cannot be spawned or if output capture fails.
    fn execute(
        &self,
        command: &str,
        args: &[&str],
        env: &[(String, String)],
        workdir: Option<&Path>,
    ) -> io::Result<ProcessOutput>;

    /// Spawn a process with stdin input and return the child handle.
    ///
    /// This method is used when you need to write to the process's stdin
    /// or stream its output in real-time. Unlike `execute()`, this returns
    /// a `Child` handle for direct interaction.
    ///
    /// # Arguments
    ///
    /// * `command` - The program to execute
    /// * `args` - Command-line arguments to pass to the program
    /// * `env` - Environment variables to set for the process (optional)
    /// * `workdir` - Working directory for the process (optional)
    ///
    /// # Returns
    ///
    /// Returns a `Child` handle that can be used to interact with the process.
    ///
    /// # Errors
    ///
    /// Returns an error if command cannot be spawned.
    fn spawn(
        &self,
        command: &str,
        args: &[&str],
        env: &[(String, String)],
        workdir: Option<&Path>,
    ) -> io::Result<std::process::Child> {
        let mut cmd = std::process::Command::new(command);
        cmd.args(args);

        for (key, value) in env {
            cmd.env(key, value);
        }

        if let Some(dir) = workdir {
            cmd.current_dir(dir);
        }

        cmd.stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
    }

    /// Spawn an agent process with streaming output support.
    ///
    /// This method is specifically designed for spawning AI agent subprocesses
    /// that need to output streaming JSON in real-time. Unlike `spawn()`, this
    /// returns a handle with boxed stdout for trait object compatibility.
    ///
    /// # Arguments
    ///
    /// * `config` - Agent spawn configuration including command, args, env, prompt, etc.
    ///
    /// # Returns
    ///
    /// Returns an `AgentChildHandle` with stdout, stderr, and the child process.
    ///
    /// # Errors
    ///
    /// Returns an error if the agent cannot be spawned.
    ///
    /// # Default Implementation
    ///
    /// The default implementation uses the `spawn()` method with additional
    /// configuration for agent-specific needs. Mock implementations should
    /// override this to return mock results without spawning real processes.
    fn spawn_agent(&self, config: &AgentSpawnConfig) -> io::Result<AgentChildHandle> {
        let mut cmd = std::process::Command::new(&config.command);
        cmd.args(&config.args);

        // Set environment variables
        for (key, value) in &config.env {
            cmd.env(key, value);
        }

        // Add the prompt as the final argument
        cmd.arg(&config.prompt);

        // Set buffering variables for real-time streaming
        cmd.env("PYTHONUNBUFFERED", "1");
        cmd.env("NODE_ENV", "production");

        // Spawn the process with piped stdout/stderr
        let mut child = cmd
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()?;

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| io::Error::other("Failed to capture stdout"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| io::Error::other("Failed to capture stderr"))?;

        Ok(AgentChildHandle {
            stdout: Box::new(stdout),
            stderr: Box::new(stderr),
            inner: Box::new(RealAgentChild(child)),
        })
    }

    /// Check if a command exists and can be executed.
    ///
    /// This is a convenience method that executes a command with a
    /// `--version` or similar flag to check if it's available.
    ///
    /// # Arguments
    ///
    /// * `command` - The program to check
    ///
    /// # Returns
    ///
    /// Returns `true` if command exists, `false` otherwise.
    fn command_exists(&self, command: &str) -> bool {
        match self.execute(command, &[], &[], None) {
            Ok(output) => output.status.success(),
            Err(_) => false,
        }
    }

    /// Returns true if the process identified by `parent_pid` has at least one
    /// live child process.
    ///
    /// Used by the idle-timeout monitor to avoid false-positive kills when the
    /// agent has spawned a subprocess (e.g. `cargo test`, `npm install`) that is
    /// still running even though the agent produces no stdout/stderr output.
    ///
    /// Default implementation: invokes `pgrep -P <pid>` on Unix platforms and
    /// returns `false` (conservative no-op) on non-Unix. Any execution error is
    /// treated as "no children" to avoid blocking the timeout system.
    fn has_active_child_processes(&self, parent_pid: u32) -> bool {
        #[cfg(unix)]
        {
            let pid_str = parent_pid.to_string();
            self.execute("pgrep", &["-P", &pid_str], &[], None)
                .map(|o| !o.stdout.trim().is_empty())
                .unwrap_or(false)
        }
        #[cfg(not(unix))]
        {
            let _ = parent_pid;
            false
        }
    }
}
