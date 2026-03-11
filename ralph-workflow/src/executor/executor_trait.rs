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

            #[derive(Clone, Copy)]
            struct ProcessSnapshotEntry {
                pid: u32,
                parent_pid: u32,
                cpu_time_ms: u64,
                in_scope: bool,
                currently_active: bool,
            }

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

            fn qualifies_process_state(state: &str) -> bool {
                match state.chars().next() {
                    Some('Z' | 'X' | 'T' | 'I') | None => false,
                    Some(_) => true,
                }
            }

            fn state_indicates_current_activity(state: &str, cpu_time_ms: u64) -> bool {
                match state.chars().next() {
                    Some('D' | 'U') => true,
                    Some('R') => cpu_time_ms > 0,
                    _ => false,
                }
            }

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

            fn child_info_from_descendant_pids(descendants: &[u32]) -> ChildProcessInfo {
                if descendants.is_empty() {
                    return ChildProcessInfo::NONE;
                }

                let child_count = u32::try_from(descendants.len()).unwrap_or(u32::MAX);
                ChildProcessInfo {
                    child_count,
                    active_child_count: 0,
                    cpu_time_ms: 0,
                    descendant_pid_signature: descendant_pid_signature(descendants),
                }
            }

            fn parse_ps_output(stdout: &str, parent_pid: u32) -> Option<ChildProcessInfo> {
                use std::collections::{HashMap, HashSet, VecDeque};

                // First pass: parse all descendant snapshot entries. Prefer richer
                // 5-column output (pid ppid pgid stat cputime) when available and
                // fall back to the legacy 3-column format (pid ppid cputime).
                let mut children_of: HashMap<u32, Vec<ProcessSnapshotEntry>> = HashMap::new();
                let mut saw_parseable = false;

                for line in stdout.lines() {
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    if parts.len() < 3 {
                        continue;
                    }

                    let Ok(entry_pid) = parts[0].parse::<u32>() else {
                        continue;
                    };
                    let Ok(parent_of_entry) = parts[1].parse::<u32>() else {
                        continue;
                    };
                    saw_parseable = true;

                    let (in_scope, currently_active, cputime_text) = if parts.len() >= 5 {
                        let pgid_matches_parent = parts[2]
                            .parse::<u32>()
                            .ok()
                            .is_some_and(|pgid| pgid == parent_pid);
                        let state_qualifies = qualifies_process_state(parts[3]);
                        let cpu_ms = parse_cputime_ms(parts[4]).unwrap_or(0);
                        (
                            pgid_matches_parent && state_qualifies,
                            state_indicates_current_activity(parts[3], cpu_ms),
                            parts[4],
                        )
                    } else {
                        (true, true, parts[2])
                    };

                    let cpu_ms = parse_cputime_ms(cputime_text).unwrap_or(0);
                    children_of
                        .entry(parent_of_entry)
                        .or_default()
                        .push(ProcessSnapshotEntry {
                            pid: entry_pid,
                            parent_pid: parent_of_entry,
                            cpu_time_ms: cpu_ms,
                            in_scope,
                            currently_active,
                        });
                }

                if !saw_parseable {
                    return None;
                }

                // BFS from parent_pid to find all descendants.
                let mut child_count: u32 = 0;
                let mut active_child_count: u32 = 0;
                let mut total_cpu_ms: u64 = 0;
                let mut descendant_pids = Vec::new();
                let mut visited = HashSet::new();
                let mut queue = VecDeque::new();
                queue.push_back(parent_pid);

                while let Some(current) = queue.pop_front() {
                    if let Some(kids) = children_of.get(&current) {
                        for child in kids {
                            if !child.in_scope || !visited.insert(child.pid) {
                                continue;
                            }

                            debug_assert_eq!(child.parent_pid, current);
                            child_count += 1;
                            if child.currently_active {
                                active_child_count += 1;
                            }
                            total_cpu_ms += child.cpu_time_ms;
                            descendant_pids.push(child.pid);
                            queue.push_back(child.pid);
                        }
                    }
                }

                descendant_pids.sort_unstable();

                if child_count == 0 {
                    return Some(ChildProcessInfo::NONE);
                }

                Some(ChildProcessInfo {
                    child_count,
                    active_child_count,
                    cpu_time_ms: total_cpu_ms,
                    descendant_pid_signature: descendant_pid_signature(&descendant_pids),
                })
            }

            fn parse_pgrep_output(stdout: &str) -> Option<Vec<u32>> {
                let mut child_pids = Vec::new();
                for line in stdout.lines() {
                    let pid = line.trim();
                    if pid.is_empty() {
                        continue;
                    }
                    child_pids.push(pid.parse::<u32>().ok()?);
                }
                Some(child_pids)
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

            fn warn_child_process_detection_conservative() {
                static WARNED: OnceLock<()> = OnceLock::new();
                if WARNED.set(()).is_ok() {
                    eprintln!(
                        "Warning: child-process detection is running in conservative fallback mode \
                         (descendant PIDs found without state/CPU evidence); idle timeout will not \
                         be suppressed by those descendants"
                    );
                }
            }

            let discover_descendants_with_pgrep = |parent_pid: u32| -> Option<Vec<u32>> {
                use std::collections::{HashSet, VecDeque};

                let mut descendants = Vec::new();
                let mut visited = HashSet::new();
                let mut queue = VecDeque::new();
                queue.push_back(parent_pid);

                while let Some(current_pid) = queue.pop_front() {
                    let output = self
                        .execute("pgrep", &["-P", &current_pid.to_string()], &[], None)
                        .ok()?;

                    let child_pids = if output.status.success() {
                        parse_pgrep_output(&output.stdout)?
                    } else if output.status.code() == Some(1) {
                        Vec::new()
                    } else {
                        return None;
                    };

                    for child_pid in child_pids {
                        if visited.insert(child_pid) {
                            descendants.push(child_pid);
                            queue.push_back(child_pid);
                        }
                    }
                }

                descendants.sort_unstable();
                Some(descendants)
            };

            #[cfg(target_os = "macos")]
            let discover_descendants_with_libproc = |parent_pid: u32| -> Option<Vec<u32>> {
                use std::collections::{HashSet, VecDeque};
                use std::ffi::c_void;

                #[link(name = "proc")]
                unsafe extern "C" {
                    fn proc_listchildpids(
                        pid: libc::pid_t,
                        buffer: *mut c_void,
                        buffersize: i32,
                    ) -> i32;
                }

                fn list_child_pids(parent_pid: u32) -> Option<Vec<u32>> {
                    let pid = libc::pid_t::try_from(parent_pid).ok()?;
                    let mut capacity: usize = 32;

                    loop {
                        let byte_len = capacity.checked_mul(std::mem::size_of::<libc::pid_t>())?;
                        let buffer_size = i32::try_from(byte_len).ok()?;
                        let mut buffer = vec![libc::pid_t::default(); capacity];

                        // Safety: `buffer` is valid for `buffer_size` bytes, and the kernel
                        // writes at most that many bytes of child pid entries.
                        let bytes_written = unsafe {
                            proc_listchildpids(
                                pid,
                                buffer.as_mut_ptr().cast::<c_void>(),
                                buffer_size,
                            )
                        };
                        if bytes_written < 0 {
                            return None;
                        }
                        if bytes_written == 0 {
                            return Some(Vec::new());
                        }

                        let count = usize::try_from(bytes_written).ok()?;
                        if count < capacity {
                            buffer.truncate(count);
                            let child_pids = buffer
                                .into_iter()
                                .filter_map(|child_pid| u32::try_from(child_pid).ok())
                                .collect();
                            return Some(child_pids);
                        }

                        capacity = capacity.checked_mul(2)?;
                    }
                }

                let mut descendants = Vec::new();
                let mut visited = HashSet::new();
                let mut queue = VecDeque::new();
                queue.push_back(parent_pid);

                while let Some(current_pid) = queue.pop_front() {
                    let child_pids = list_child_pids(current_pid)?;
                    for child_pid in child_pids {
                        if visited.insert(child_pid) {
                            descendants.push(child_pid);
                            queue.push_back(child_pid);
                        }
                    }
                }

                descendants.sort_unstable();
                Some(descendants)
            };

            // Try richer ps invocations first so detached/stopped descendants can
            // be filtered out, then fall back to the legacy shape for compatibility.
            let ps_attempts: [&[&str]; 4] = [
                &[
                    "-ax", "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "stat=", "-o",
                    "cputime=",
                ],
                &[
                    "-e", "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "stat=", "-o",
                    "cputime=",
                ],
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

            if let Some(descendants) = discover_descendants_with_pgrep(parent_pid) {
                if !descendants.is_empty() {
                    warn_child_process_detection_conservative();
                }
                return child_info_from_descendant_pids(&descendants);
            }

            #[cfg(target_os = "macos")]
            if let Some(descendants) = discover_descendants_with_libproc(parent_pid) {
                if !descendants.is_empty() {
                    warn_child_process_detection_conservative();
                }
                return child_info_from_descendant_pids(&descendants);
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

    #[cfg(unix)]
    fn ps_key_with_state_and_group() -> (String, Vec<String>) {
        (
            "ps".to_string(),
            vec![
                "-ax".to_string(),
                "-o".to_string(),
                "pid=".to_string(),
                "-o".to_string(),
                "ppid=".to_string(),
                "-o".to_string(),
                "pgid=".to_string(),
                "-o".to_string(),
                "stat=".to_string(),
                "-o".to_string(),
                "cputime=".to_string(),
            ],
        )
    }

    #[cfg(unix)]
    fn pgrep_key(parent_pid: u32) -> (String, Vec<String>) {
        (
            "pgrep".to_string(),
            vec!["-P".to_string(), parent_pid.to_string()],
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
        assert_eq!(info.active_child_count, 2);
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
        assert_eq!(info.active_child_count, 0);
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
        assert_eq!(info.active_child_count, 1);
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

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_pgrep_fallback_does_not_report_active_children() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(pgrep_key(100), ok_output("200\n300\n"));
        results.insert(pgrep_key(200), ok_output("400\n"));
        results.insert(pgrep_key(300), ok_output(""));
        results.insert(pgrep_key(400), ok_output(""));

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert_eq!(info.child_count, 3);
        assert_eq!(
            info.active_child_count, 0,
            "fallback without process state or cpu evidence must not report active children"
        );
        assert_eq!(info.cpu_time_ms, 0);
        assert_ne!(
            info.descendant_pid_signature, 0,
            "observable descendants should retain a stable signature even in fallback mode"
        );
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_excludes_descendants_in_other_process_groups() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(
            ps_key_with_state_and_group(),
            ok_output(
                "200 100 100 S 0:01.00\n201 100 201 S 0:05.00\n300 200 100 S 0:02.00\n301 201 201 S 0:09.00\n",
            ),
        );

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert_eq!(
            info.child_count, 2,
            "only descendants that remain in the agent process group should qualify"
        );
        assert_eq!(
            info.active_child_count, 0,
            "sleeping same-process-group descendants should remain observable without suppressing timeout"
        );
        assert_eq!(
            info.cpu_time_ms,
            1000 + 2000,
            "detached descendants in a different process group must be excluded"
        );
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_excludes_zombie_descendants() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(
            ps_key_with_state_and_group(),
            ok_output("200 100 100 S 0:01.00\n201 100 100 Z 0:05.00\n"),
        );

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert_eq!(info.child_count, 1, "zombie descendants must not qualify");
        assert_eq!(info.active_child_count, 0);
        assert_eq!(info.cpu_time_ms, 1000, "zombie cpu time must be ignored");
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_returns_none_when_only_non_qualifying_descendants_exist() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(
            ps_key_with_state_and_group(),
            ok_output("200 100 200 S 0:01.00\n300 200 200 S 0:02.00\n"),
        );

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert_eq!(
            info,
            ChildProcessInfo::NONE,
            "an empty qualified descendant set must normalize to no active child work"
        );
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_excludes_zero_cpu_descendants_without_activity_evidence() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(
            ps_key_with_state_and_group(),
            ok_output("200 100 100 S 0:00.00\n"),
        );

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert_eq!(info.child_count, 1);
        assert_eq!(info.active_child_count, 0);
        assert_eq!(info.cpu_time_ms, 0);
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_does_not_count_running_zero_cpu_descendants_as_currently_active() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(
            ps_key_with_state_and_group(),
            ok_output("200 100 100 R 0:00.00\n"),
        );

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert_eq!(info.child_count, 1);
        assert_eq!(
            info.active_child_count, 0,
            "running descendants with zero accumulated CPU should not yet count as current work"
        );
        assert_eq!(info.cpu_time_ms, 0);
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_excludes_sleeping_descendants_with_only_historical_cpu() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(
            ps_key_with_state_and_group(),
            ok_output("200 100 100 S 0:01.00\n300 200 100 S 0:02.00\n"),
        );

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert_eq!(info.child_count, 2);
        assert_eq!(info.active_child_count, 0);
        assert_eq!(info.cpu_time_ms, 3000);
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_pgrep_fallback_is_conservative() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(pgrep_key(100), ok_output("200\n300\n"));
        results.insert(pgrep_key(200), ok_output(""));
        results.insert(pgrep_key(300), ok_output(""));

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert!(info.has_children());
        assert!(
            !info.has_currently_active_children(),
            "fallback without process-state or cpu evidence must not suppress idle timeout"
        );
        assert_eq!(info.cpu_time_ms, 0);
    }
}
