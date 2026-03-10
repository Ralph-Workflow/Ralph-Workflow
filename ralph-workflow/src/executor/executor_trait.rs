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
    /// falls back to `ps -o pid= --ppid <pid>` if `pgrep` is unavailable. Returns
    /// `false` (conservative no-op) on non-Unix.
    ///
    /// Any execution error is treated as "no children" to avoid blocking the
    /// timeout system. When detection degrades (missing command or command error),
    /// a one-time warning is emitted to stderr so operators can spot the reduced
    /// protection against false-positive idle kills.
    fn has_active_child_processes(&self, parent_pid: u32) -> bool {
        #[cfg(unix)]
        {
            use std::sync::OnceLock;

            fn warn_child_process_detection_degraded() {
                static WARNED: OnceLock<()> = OnceLock::new();
                if WARNED.set(()).is_ok() {
                    eprintln!(
                        "Warning: child-process detection degraded (pgrep/ps unavailable or failing); idle-timeout false-positive prevention may be reduced"
                    );
                }
            }

            let pid_str = parent_pid.to_string();

            // Primary: `pgrep -P <pid>`
            match self.execute("pgrep", &["-P", &pid_str], &[], None) {
                Ok(out) => {
                    let has_children = !out.stdout.trim().is_empty();
                    if !has_children {
                        if let Some(code) = out.status.code() {
                            // pgrep exit code 1 means "no matches" (normal).
                            if code != 0 && code != 1 {
                                warn_child_process_detection_degraded();
                            }
                        }
                    }
                    return has_children;
                }
                Err(_) => {
                    warn_child_process_detection_degraded();
                }
            }

            // Fallback: `ps -o pid= --ppid <pid>`
            if let Ok(out) = self.execute("ps", &["-o", "pid=", "--ppid", &pid_str], &[], None) {
                if !out.status.success() {
                    warn_child_process_detection_degraded();
                }
                !out.stdout.trim().is_empty()
            } else {
                warn_child_process_detection_degraded();
                false
            }
        }
        #[cfg(not(unix))]
        {
            let _ = parent_pid;
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::io;
    use std::sync::Mutex;

    #[cfg(unix)]
    fn ok_output(stdout: &str) -> ProcessOutput {
        use std::os::unix::process::ExitStatusExt;

        ProcessOutput {
            status: std::process::ExitStatus::from_raw(0),
            stdout: stdout.to_string(),
            stderr: String::new(),
        }
    }

    #[cfg(unix)]
    #[derive(Debug, Clone)]
    enum TestResult {
        Ok(ProcessOutput),
        Err {
            kind: io::ErrorKind,
            message: String,
        },
    }

    #[cfg(unix)]
    impl TestResult {
        fn to_io_result(&self) -> io::Result<ProcessOutput> {
            match self {
                Self::Ok(out) => Ok(out.clone()),
                Self::Err { kind, message } => Err(io::Error::new(*kind, message.clone())),
            }
        }
    }

    #[cfg(unix)]
    #[derive(Debug)]
    struct TestExecutor {
        calls: Mutex<Vec<(String, Vec<String>)>>,
        results: HashMap<(String, Vec<String>), TestResult>,
    }

    #[cfg(unix)]
    impl TestExecutor {
        fn new(results: HashMap<(String, Vec<String>), TestResult>) -> Self {
            Self {
                calls: Mutex::new(Vec::new()),
                results,
            }
        }

        fn calls_for(&self, command: &str) -> Vec<(String, Vec<String>)> {
            self.calls
                .lock()
                .unwrap()
                .iter()
                .filter(|(c, _)| c == command)
                .cloned()
                .collect()
        }
    }

    #[cfg(unix)]
    impl ProcessExecutor for TestExecutor {
        fn execute(
            &self,
            command: &str,
            args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&std::path::Path>,
        ) -> std::io::Result<ProcessOutput> {
            let key = (
                command.to_string(),
                args.iter().map(ToString::to_string).collect(),
            );
            self.calls.lock().unwrap().push(key.clone());
            self.results.get(&key).map_or_else(
                || Err(std::io::Error::other("unexpected execute")),
                TestResult::to_io_result,
            )
        }
    }

    #[test]
    #[cfg(unix)]
    fn has_active_child_processes_falls_back_to_ps_when_pgrep_missing() {
        let pid = 4242;
        let pid_str = pid.to_string();

        let mut results: HashMap<(String, Vec<String>), TestResult> = HashMap::new();
        results.insert(
            ("pgrep".to_string(), vec!["-P".to_string(), pid_str.clone()]),
            TestResult::Err {
                kind: io::ErrorKind::NotFound,
                message: "pgrep missing".to_string(),
            },
        );
        results.insert(
            (
                "ps".to_string(),
                vec![
                    "-o".to_string(),
                    "pid=".to_string(),
                    "--ppid".to_string(),
                    pid_str,
                ],
            ),
            TestResult::Ok(ok_output("12345\n")),
        );

        let exec = TestExecutor::new(results);
        assert!(
            exec.has_active_child_processes(pid),
            "ps fallback should detect children when pgrep is unavailable"
        );
        assert!(
            !exec.calls_for("ps").is_empty(),
            "ps should be invoked as a fallback"
        );
    }
}
