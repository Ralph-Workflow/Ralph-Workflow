//! `ProcessExecutor` trait definition.
//!
//! This module defines the trait abstraction for process execution,
//! enabling dependency injection for testing.

use super::{AgentChildHandle, AgentSpawnConfig, ChildProcessInfo, ProcessOutput, RealAgentChild};
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

    /// Returns information about child processes of the given parent, including
    /// their cumulative CPU time.
    ///
    /// Used by the idle-timeout monitor to determine whether child processes
    /// are actively working (CPU time advancing between consecutive checks)
    /// versus merely existing (stalled, zombie, or idle daemon).
    ///
    /// Default implementation: parses `ps` output for PID, PPID, and cputime
    /// columns on Unix platforms. Returns `ChildProcessInfo::NONE` on non-Unix.
    ///
    /// Any execution error is treated as "no children" to avoid blocking the
    /// timeout system. If `ps` is unavailable or fails unexpectedly, a one-time
    /// warning is emitted to stderr so operators can diagnose reduced protection
    /// against false-positive idle kills.
    fn get_child_process_info(&self, parent_pid: u32) -> ChildProcessInfo {
        #[cfg(unix)]
        {
            use std::sync::OnceLock;

            /// Parse "DD-HH:MM:SS", "HH:MM:SS", "MM:SS", or "MM:SS.ss" cputime format to milliseconds.
            fn parse_cputime_ms(s: &str) -> Option<u64> {
                let parts: Vec<&str> = s.split(':').collect();
                match parts.len() {
                    3 => {
                        // DD-HH:MM:SS or HH:MM:SS (or HH:MM:SS.ss)
                        let hours = if let Some((days, hours)) = parts[0].split_once('-') {
                            let days: u64 = days.parse().ok()?;
                            let hours: u64 = hours.parse().ok()?;
                            days.checked_mul(24)?.checked_add(hours)?
                        } else {
                            parts[0].parse().ok()?
                        };
                        let minutes: u64 = parts[1].parse().ok()?;
                        let seconds_str = parts[2];
                        let (secs, frac_ms) = if let Some((s, f)) = seconds_str.split_once('.') {
                            let secs: u64 = s.parse().ok()?;
                            let frac: u64 = f.get(..2).unwrap_or(f).parse().ok()?;
                            (secs, frac * 10)
                        } else {
                            (seconds_str.parse().ok()?, 0)
                        };
                        Some((hours * 3600 + minutes * 60 + secs) * 1000 + frac_ms)
                    }
                    2 => {
                        // MM:SS or M:SS (or MM:SS.ss)
                        let minutes: u64 = parts[0].parse().ok()?;
                        let seconds_str = parts[1];
                        let (secs, frac_ms) = if let Some((s, f)) = seconds_str.split_once('.') {
                            let secs: u64 = s.parse().ok()?;
                            let frac: u64 = f.get(..2).unwrap_or(f).parse().ok()?;
                            (secs, frac * 10)
                        } else {
                            (seconds_str.parse().ok()?, 0)
                        };
                        Some((minutes * 60 + secs) * 1000 + frac_ms)
                    }
                    _ => None,
                }
            }

            fn parse_ps_output(stdout: &str, parent_pid: u32) -> Option<ChildProcessInfo> {
                use std::collections::{HashMap, HashSet, VecDeque};

                fn descendant_pid_signature(descendants: &[u32]) -> u64 {
                    const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
                    const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

                    let mut signature = FNV_OFFSET;
                    for pid in descendants {
                        for byte in pid.to_le_bytes() {
                            signature ^= u64::from(byte);
                            signature = signature.wrapping_mul(FNV_PRIME);
                        }
                    }
                    signature
                }

                // First pass: parse all (pid, ppid, cpu_time_ms) tuples.
                let mut children_of: HashMap<u32, Vec<(u32, u64)>> = HashMap::new();
                let mut saw_parseable = false;

                for line in stdout.lines() {
                    let mut parts = line.split_whitespace();
                    let Some(col_pid) = parts.next() else {
                        continue;
                    };
                    let Some(col_parent) = parts.next() else {
                        continue;
                    };
                    let cputime_text = parts.next().unwrap_or("0:00");

                    let Ok(entry_pid) = col_pid.parse::<u32>() else {
                        continue;
                    };
                    let Ok(parent_of_entry) = col_parent.parse::<u32>() else {
                        continue;
                    };
                    saw_parseable = true;

                    let cpu_ms = parse_cputime_ms(cputime_text).unwrap_or(0);
                    children_of
                        .entry(parent_of_entry)
                        .or_default()
                        .push((entry_pid, cpu_ms));
                }

                if !saw_parseable {
                    return None;
                }

                // BFS from parent_pid to find all descendants.
                let mut child_count: u32 = 0;
                let mut total_cpu_ms: u64 = 0;
                let mut descendant_pids = Vec::new();
                let mut visited = HashSet::new();
                let mut queue = VecDeque::new();
                queue.push_back(parent_pid);

                while let Some(current) = queue.pop_front() {
                    if let Some(kids) = children_of.get(&current) {
                        for &(pid, cpu_ms) in kids {
                            if visited.insert(pid) {
                                child_count += 1;
                                total_cpu_ms += cpu_ms;
                                descendant_pids.push(pid);
                                queue.push_back(pid);
                            }
                        }
                    }
                }

                descendant_pids.sort_unstable();

                Some(ChildProcessInfo {
                    child_count,
                    cpu_time_ms: total_cpu_ms,
                    descendant_pid_signature: descendant_pid_signature(&descendant_pids),
                })
            }

            fn warn_child_process_detection_degraded() {
                static WARNED: OnceLock<()> = OnceLock::new();
                if WARNED.set(()).is_ok() {
                    eprintln!(
                        "Warning: child-process detection degraded (ps unavailable or failing); \
                         idle-timeout false-positive prevention may be reduced"
                    );
                }
            }

            // Try BSD-style (macOS) then GNU-style (Linux) ps invocations.
            let ps_attempts: [&[&str]; 2] = [
                &["-ax", "-o", "pid=", "-o", "ppid=", "-o", "cputime="],
                &["-e", "-o", "pid=", "-o", "ppid=", "-o", "cputime="],
            ];

            for args in ps_attempts {
                if let Ok(out) = self.execute("ps", args, &[], None) {
                    if out.status.success() {
                        if let Some(info) = parse_ps_output(&out.stdout, parent_pid) {
                            return info;
                        }
                    }
                }
            }

            // Degraded: emit one-time warning, return no-children (conservative).
            warn_child_process_detection_degraded();
            ChildProcessInfo::NONE
        }
        #[cfg(not(unix))]
        {
            let _ = parent_pid;
            ChildProcessInfo::NONE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

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
    type ResultMap = HashMap<(String, Vec<String>), ProcessOutput>;

    #[cfg(unix)]
    #[derive(Debug)]
    struct TestExecutor {
        results: ResultMap,
    }

    #[cfg(unix)]
    impl TestExecutor {
        fn new(results: ResultMap) -> Self {
            Self { results }
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
            self.results
                .get(&(
                    command.to_string(),
                    args.iter().map(ToString::to_string).collect(),
                ))
                .cloned()
                .ok_or_else(|| std::io::Error::other("unexpected execute"))
        }
    }

    #[cfg(unix)]
    fn ps_key() -> (String, Vec<String>) {
        (
            "ps".to_string(),
            vec![
                "-ax".to_string(),
                "-o".to_string(),
                "pid=".to_string(),
                "-o".to_string(),
                "ppid=".to_string(),
                "-o".to_string(),
                "cputime=".to_string(),
            ],
        )
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_returns_info_from_ps() {
        let pid = 4242;

        let mut results: ResultMap = HashMap::new();
        results.insert(
            ps_key(),
            ok_output("12345 4242 0:01.50\n12346 4242 0:03.00\n99999 1 0:10.00\n"),
        );

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(pid);
        assert_eq!(info.child_count, 2, "should find 2 children of pid 4242");
        assert_eq!(
            info.cpu_time_ms,
            1500 + 3000,
            "should sum CPU times of both children"
        );
        assert!(info.has_children());
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_no_children_returns_zero() {
        let pid = 4242;

        let mut results: ResultMap = HashMap::new();
        results.insert(ps_key(), ok_output("99999 1 0:10.00\n"));

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(pid);
        assert_eq!(info.child_count, 0);
        assert_eq!(info.cpu_time_ms, 0);
        assert!(!info.has_children());
    }

    #[test]
    #[cfg(unix)]
    fn parse_cputime_formats() {
        let pid = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(ps_key(), ok_output("200 100 01:02:03\n"));

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(pid);
        assert_eq!(
            info.cpu_time_ms,
            (3600 + 2 * 60 + 3) * 1000,
            "HH:MM:SS should parse to correct ms"
        );
    }

    #[test]
    #[cfg(unix)]
    fn parse_cputime_with_day_prefix() {
        let pid = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(ps_key(), ok_output("200 100 1-02:03:04\n"));

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(pid);
        assert_eq!(
            info.cpu_time_ms,
            ((24 + 2) * 3600 + 3 * 60 + 4) * 1000,
            "DD-HH:MM:SS should parse to correct ms"
        );
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_includes_grandchildren() {
        let parent = 100;
        // PID 200 is child of 100, PID 300 is child of 200 (grandchild of 100).
        let ps_output = "200 100 0:01.00\n300 200 0:02.00\n999 1 0:05.00\n";

        let mut results: ResultMap = HashMap::new();
        results.insert(ps_key(), ok_output(ps_output));

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);
        assert_eq!(
            info.child_count, 2,
            "should count both child and grandchild"
        );
        assert_eq!(
            info.cpu_time_ms,
            1000 + 2000,
            "should sum CPU of child and grandchild"
        );
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_excludes_unrelated_processes() {
        let parent = 100;
        // PID 200 is child of 100. PID 300's ppid chain goes to 1, NOT to 100.
        let ps_output = "200 100 0:01.00\n300 400 0:02.00\n400 1 0:03.00\n";

        let mut results: ResultMap = HashMap::new();
        results.insert(ps_key(), ok_output(ps_output));

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);
        assert_eq!(info.child_count, 1, "should only count PID 200");
        assert_eq!(info.cpu_time_ms, 1000, "should only sum CPU of PID 200");
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_deep_tree() {
        let parent = 100;
        // 3+ levels of nesting: 100 → 200 → 300 → 400
        let ps_output = "200 100 0:01.00\n300 200 0:02.00\n400 300 0:03.00\n";

        let mut results: ResultMap = HashMap::new();
        results.insert(ps_key(), ok_output(ps_output));

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);
        assert_eq!(
            info.child_count, 3,
            "should count all 3 levels of descendants"
        );
        assert_eq!(
            info.cpu_time_ms,
            1000 + 2000 + 3000,
            "should sum CPU across all descendants"
        );
    }
}
