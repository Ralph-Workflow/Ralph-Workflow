//! Real process executor implementation.
//!
//! This module provides the production implementation that spawns actual processes
//! using `std::process::Command`.

use super::{AgentChildHandle, AgentSpawnConfig, ProcessExecutor, ProcessOutput, RealAgentChild};
use std::io;
use std::path::Path;

#[cfg(unix)]
fn set_nonblocking_fd(fd: std::os::unix::io::RawFd) -> io::Result<()> {
    // Make the file descriptor non-blocking so readers can poll/cancel without
    // getting stuck in a blocking read().
    //
    // Safety: fcntl is called with a valid fd owned by this process.
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFL);
        if flags < 0 {
            return Err(io::Error::last_os_error());
        }
        if libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) < 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

#[cfg(unix)]
fn terminate_child_best_effort(child: &mut std::process::Child) {
    let pid = child.id().min(i32::MAX as u32).cast_signed();

    unsafe {
        let _ = libc::kill(-pid, libc::SIGTERM);
        let _ = libc::kill(pid, libc::SIGTERM);
    }

    wait_for_termination_or_send_sigkill(child, pid);
}

#[cfg(unix)]
fn wait_until_deadline(child: &mut std::process::Child, deadline: std::time::Instant) {
    use std::time::{Duration, Instant};

    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(_)) | Err(_) => return,
            Ok(None) => std::thread::sleep(Duration::from_millis(10)),
        }
    }
}

#[cfg(unix)]
fn wait_for_termination_or_send_sigkill(child: &mut std::process::Child, pid: i32) {
    let (term_deadline, kill_deadline) = compute_termination_deadlines();
    wait_until_deadline(child, term_deadline);
    send_sigkill(pid);
    wait_until_deadline(child, kill_deadline);
}

#[cfg(unix)]
fn compute_termination_deadlines() -> (std::time::Instant, std::time::Instant) {
    use std::time::{Duration, Instant};

    let term_deadline = Instant::now() + Duration::from_millis(250);
    let kill_deadline = term_deadline + Duration::from_millis(500);
    (term_deadline, kill_deadline)
}

#[cfg(unix)]
fn send_sigkill(pid: i32) {
    unsafe {
        let _ = libc::kill(-pid, libc::SIGKILL);
        let _ = libc::kill(pid, libc::SIGKILL);
    }
}

#[cfg(unix)]
fn ensure_nonblocking_or_terminate(
    child: &mut std::process::Child,
    stdout_fd: std::os::unix::io::RawFd,
    stderr_fd: std::os::unix::io::RawFd,
) -> io::Result<()> {
    if let Err(e) = set_nonblocking_fd(stdout_fd) {
        terminate_child_best_effort(child);
        return Err(e);
    }

    if let Err(e) = set_nonblocking_fd(stderr_fd) {
        terminate_child_best_effort(child);
        return Err(e);
    }

    Ok(())
}

fn wrap_process_output(output: std::process::Output) -> ProcessOutput {
    ProcessOutput {
        status: output.status,
        stdout: String::from_utf8_lossy(&output.stdout).to_string(),
        stderr: String::from_utf8_lossy(&output.stderr).to_string(),
    }
}

/// Real process executor that uses `std::process::Command`.
///
/// This is the production implementation that spawns actual processes.
#[derive(Debug, Clone, Default)]
pub struct RealProcessExecutor;

impl RealProcessExecutor {
    /// Create a new `RealProcessExecutor`.
    #[must_use]
    pub const fn new() -> Self {
        Self
    }
}

impl ProcessExecutor for RealProcessExecutor {
    fn execute(
        &self,
        command: &str,
        args: &[&str],
        env: &[(String, String)],
        workdir: Option<&Path>,
    ) -> io::Result<ProcessOutput> {
        let output = build_and_run_command(command, args, env, workdir)?;
        Ok(wrap_process_output(output))
    }

    fn spawn(
        &self,
        command: &str,
        args: &[&str],
        env: &[(String, String)],
        workdir: Option<&Path>,
    ) -> io::Result<std::process::Child> {
        let mut cmd = std::process::Command::new(command);
        cmd.args(args);
        env.iter().for_each(|(k, v)| {
            cmd.env(k, v);
        });
        if let Some(dir) = workdir {
            cmd.current_dir(dir);
        }
        cmd.stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
    }

    fn spawn_agent(&self, config: &AgentSpawnConfig) -> io::Result<AgentChildHandle> {
        let mut cmd = build_agent_command(config);
        let mut child = spawn_agent_child(&mut cmd)?;

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| io::Error::other("Failed to capture stdout"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| io::Error::other("Failed to capture stderr"))?;

        #[cfg(unix)]
        {
            use std::os::unix::io::AsRawFd;
            ensure_nonblocking_or_terminate(&mut child, stdout.as_raw_fd(), stderr.as_raw_fd())?;
        }

        #[cfg(not(unix))]
        let _ = (&child, &stdout, &stderr);

        Ok(AgentChildHandle {
            stdout: Box::new(stdout),
            stderr: Box::new(stderr),
            inner: Box::new(RealAgentChild(child)),
        })
    }
}

fn build_agent_command(config: &AgentSpawnConfig) -> std::process::Command {
    let mut cmd = std::process::Command::new(&config.command);
    cmd.args(&config.args);
    config.env.iter().for_each(|(k, v)| {
        cmd.env(k, v);
    });
    cmd.arg(&config.prompt);
    cmd.env("PYTHONUNBUFFERED", "1");
    cmd.env("NODE_ENV", "production");

    #[cfg(unix)]
    unsafe {
        use std::os::unix::process::CommandExt;
        cmd.pre_exec(|| {
            if libc::setpgid(0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }

    cmd.stdin(std::process::Stdio::null());
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    cmd
}

fn spawn_agent_child(cmd: &mut std::process::Command) -> io::Result<std::process::Child> {
    cmd.spawn()
}

fn build_and_run_command(
    command: &str,
    args: &[&str],
    env: &[(String, String)],
    workdir: Option<&Path>,
) -> io::Result<std::process::Output> {
    let mut cmd = std::process::Command::new(command);
    cmd.args(args);
    env.iter().for_each(|(k, v)| {
        cmd.env(k, v);
    });
    if let Some(dir) = workdir {
        cmd.current_dir(dir);
    }
    cmd.output()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[cfg(unix)]
    fn ensure_nonblocking_or_terminate_kills_child_on_failure() {
        use std::process::Command;
        use std::time::{Duration, Instant};

        let mut child = Command::new("sleep")
            .arg("60")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("spawn sleep");

        let result = ensure_nonblocking_or_terminate(&mut child, -1, -1);
        assert!(result.is_err(), "expected nonblocking setup to fail");

        let deadline = Instant::now() + Duration::from_secs(2);
        let mut exited = false;
        while Instant::now() < deadline {
            if !matches!(child.try_wait(), Ok(None)) {
                exited = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }

        if !exited {
            let _ = child.kill();
            let _ = child.wait();
        }

        assert!(
            exited,
            "expected child to be terminated when nonblocking setup fails"
        );
    }
}
