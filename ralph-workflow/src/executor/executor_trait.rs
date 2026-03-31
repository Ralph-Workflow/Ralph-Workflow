//! `ProcessExecutor` trait definition.
//!
//! This module defines the trait abstraction for process execution,
//! enabling dependency injection for testing.

use super::{
    AgentChildHandle, AgentSpawnConfig, ChildProcessInfo, ProcessOutput, RealAgentChild,
    SpawnedProcess,
};
#[cfg(target_os = "macos")]
use crate::executor::macos::child_info_from_libproc;
use crate::executor::ps::parse_ps_output;
use crate::executor::{
    bfs::collect_descendants,
    command::{build_agent_command_internal, build_command},
    ps::{
        child_info_from_descendant_pids, warn_child_process_detection_conservative,
        warn_child_process_detection_degraded,
    },
};
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

    /// Spawn a process with stdin input and return a handle for interaction.
    ///
    /// This method is used when you need to write to the process's stdin.
    /// Unlike `execute()`, this returns a `SpawnedProcess` handle that exposes
    /// only the domain-relevant surface (stdin writing and process completion).
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
    /// Returns a `SpawnedProcess` handle for writing stdin and waiting.
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
    ) -> io::Result<SpawnedProcess> {
        let mut child = build_command(command, args, env, workdir)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()?;
        let stdin = child.stdin.take();
        Ok(SpawnedProcess {
            stdin,
            inner: child,
        })
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
    /// The default implementation spawns the agent command directly.
    /// Mock implementations should override this to return mock results
    /// without spawning real processes.
    fn spawn_agent(&self, config: &AgentSpawnConfig) -> io::Result<AgentChildHandle> {
        let child = build_agent_command_internal(
            &config.command,
            &config.args,
            &config.env,
            &config.prompt,
        )
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()?;
        wrap_agent_child(child)
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
        return get_child_process_info_unix(self, parent_pid);
        #[cfg(not(unix))]
        {
            let _ = parent_pid;
            ChildProcessInfo::NONE
        }
    }

    /// Kill an entire process group by sending SIGKILL to all members.
    ///
    /// Uses `kill(-pid, SIGKILL)` to send the signal to all processes in the
    /// process group identified by `pgid`. This is a best-effort fire-and-forget
    /// call; errors are ignored because the primary process has already exited.
    ///
    /// The default implementation is a no-op so existing mock implementations
    /// continue to compile without change. The `RealProcessExecutor` override
    /// issues the actual SIGKILL.
    #[cfg(unix)]
    fn kill_process_group(&self, _pgid: u32) -> io::Result<()> {
        Ok(())
    }
}

const PS_ATTEMPTS: [&[&str]; 6] = [
    &[
        "-ax", "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "stat=", "-o", "cputime=", "-o",
        "comm=",
    ],
    &[
        "-e", "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "stat=", "-o", "cputime=", "-o",
        "comm=",
    ],
    &[
        "-ax", "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "stat=", "-o", "cputime=",
    ],
    &[
        "-e", "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "stat=", "-o", "cputime=",
    ],
    &["-ax", "-o", "pid=", "-o", "ppid=", "-o", "cputime="],
    &["-e", "-o", "pid=", "-o", "ppid=", "-o", "cputime="],
];

fn try_ps_args<E: ProcessExecutor + ?Sized>(
    executor: &E,
    args: &[&str],
    parent_pid: u32,
) -> Option<ChildProcessInfo> {
    let out = executor.execute("ps", args, &[], None).ok()?;
    out.status
        .success()
        .then(|| parse_ps_output(&out.stdout, parent_pid))
        .flatten()
}

fn try_ps_output_chain<E: ProcessExecutor + ?Sized>(
    executor: &E,
    parent_pid: u32,
) -> Option<ChildProcessInfo> {
    PS_ATTEMPTS
        .iter()
        .find_map(|&args| try_ps_args(executor, args, parent_pid))
}

#[cfg(unix)]
fn try_libproc_fallback(parent_pid: u32) -> Option<ChildProcessInfo> {
    #[cfg(target_os = "macos")]
    return child_info_from_libproc(parent_pid);
    #[cfg(not(target_os = "macos"))]
    {
        let _ = parent_pid;
        None
    }
}

#[cfg(unix)]
fn get_child_process_info_unix<E: ProcessExecutor + ?Sized>(
    executor: &E,
    parent_pid: u32,
) -> ChildProcessInfo {
    try_ps_output_chain(executor, parent_pid)
        .or_else(|| try_libproc_fallback(parent_pid))
        .or_else(|| try_pgrep_fallback(executor, parent_pid))
        .unwrap_or_else(|| {
            warn_child_process_detection_degraded();
            ChildProcessInfo::NONE
        })
}

fn try_pgrep_fallback<E: ProcessExecutor + ?Sized>(
    executor: &E,
    parent_pid: u32,
) -> Option<ChildProcessInfo> {
    let descendants = collect_descendants(executor, parent_pid);
    if !descendants.is_empty() {
        warn_child_process_detection_conservative();
        return Some(child_info_from_descendant_pids(&descendants));
    }
    None
}

impl SpawnedProcess {
    /// Wait for the process to finish, discarding the exit status.
    ///
    /// # Errors
    ///
    /// Returns an error if the wait operation fails.
    pub fn wait(&mut self) -> io::Result<()> {
        self.inner.wait()?;
        Ok(())
    }

    /// Check whether the process has exited without blocking.
    ///
    /// # Errors
    ///
    /// Returns an error if the operation fails.
    pub fn try_wait(&mut self) -> io::Result<Option<std::process::ExitStatus>> {
        self.inner.try_wait()
    }

    /// Kill the process.
    ///
    /// # Errors
    ///
    /// Returns an error if the kill operation fails.
    pub fn kill(&mut self) -> io::Result<()> {
        self.inner.kill()
    }
}

fn wrap_agent_child(mut child: std::process::Child) -> io::Result<AgentChildHandle> {
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
    fn ps_key_with_state_group_and_command() -> (String, Vec<String>) {
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
                "-o".to_string(),
                "comm=".to_string(),
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
    fn get_child_process_info_legacy_ps_output_is_conservative_about_current_activity() {
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
            info.active_child_count, 0,
            "legacy ps output without state or process-group columns must not report current activity"
        );
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
        let ps_output = "200 100 0:01.00\n300 400 0:02.00\n400 1 0:03.00\n";

        let mut results: ResultMap = HashMap::new();
        results.insert(ps_key(), ok_output(ps_output));

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);
        assert_eq!(info.child_count, 1, "should only count PID 200");
        assert_eq!(
            info.active_child_count, 0,
            "legacy ps output without state columns must remain conservative even for related descendants"
        );
        assert_eq!(info.cpu_time_ms, 1000, "should only sum CPU of PID 200");
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_deep_tree() {
        let parent = 100;
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
    fn get_child_process_info_counts_busy_shell_without_descendants_as_current_work() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(
            ps_key_with_state_group_and_command(),
            ok_output("200 100 100 R 0:01.00 sh\n"),
        );

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert_eq!(info.child_count, 1);
        assert_eq!(
            info.active_child_count, 1,
            "a shell process that is itself running with accumulated CPU must count as current child work even without descendants"
        );
        assert_eq!(info.cpu_time_ms, 1000);
    }

    #[test]
    #[cfg(unix)]
    fn get_child_process_info_keeps_non_wrapper_busy_processes_active() {
        let parent = 100;

        let mut results: ResultMap = HashMap::new();
        results.insert(
            ps_key_with_state_group_and_command(),
            ok_output("200 100 100 R 0:01.00 python3\n"),
        );

        let exec = TestExecutor::new(results);
        let info = exec.get_child_process_info(parent);

        assert_eq!(info.child_count, 1);
        assert_eq!(
            info.active_child_count, 1,
            "real worker processes must still count as current child work when they are busy"
        );
        assert_eq!(info.cpu_time_ms, 1000);
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

    #[test]
    #[cfg(target_os = "macos")]
    fn child_pid_entry_count_converts_libproc_bytes_to_pid_count() {
        use super::super::macos::child_pid_entry_count;

        let pid_width = i32::try_from(std::mem::size_of::<libc::pid_t>())
            .expect("pid_t size should fit in i32");

        assert_eq!(child_pid_entry_count(pid_width * 3), Some(3));
        assert_eq!(child_pid_entry_count(pid_width), Some(1));
        assert_eq!(child_pid_entry_count(0), Some(0));
    }
}
