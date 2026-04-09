//! Remote build server dispatch.
//!
//! When `cargo xtask` is invoked outside of `/tmp` (i.e. not already on a
//! build server), this module probes `rw-build-server` via SSH and, if
//! reachable, syncs the working tree and re-executes the same subcommand
//! there.  Falls back to local execution when the server is unreachable.
//!
//! # Skip conditions
//!
//! - CWD starts with `/tmp` — already running on a build server.
//! - `XTASK_LOCAL=1` — **emergency fallback only**: use ONLY when `rw-build-server`
//!   is confirmed unreachable (network down, server offline).  Never set this to
//!   work around a test failure or for convenience — that defeats the entire
//!   remote-first design.
//!
//! # Remote root path
//!
//! `/tmp/rw-<first-16-hex-chars of SHA-256(git-root + hostname)>`.
//! The hash makes the path unique per (local repo, machine) pair and stable
//! across invocations, so incremental rsyncs are cheap.

use sha2::{Digest, Sha256};
use std::process::{Command, ExitCode, Stdio};

/// Attempt to dispatch to the remote build server.
///
/// Returns `Some(exit_code)` if the command ran (or failed to sync) on the
/// remote, meaning the caller should propagate that code and not execute
/// locally.  Returns `None` if local execution should proceed.
pub fn try_run_remote(args: &[String]) -> Option<ExitCode> {
    if should_skip_remote() {
        return None;
    }
    let repo_root = git_repo_root()?;
    probe_and_execute(&repo_root, args)
}

fn probe_and_execute(repo_root: &str, args: &[String]) -> Option<ExitCode> {
    if !probe_server() {
        eprintln!("[remote-build] rw-build-server unreachable, running locally");
        return None;
    }
    let remote_root = compute_remote_root(repo_root)?;
    execute_on_remote(repo_root, &remote_root, args)
}

fn execute_on_remote(repo_root: &str, remote_root: &str, args: &[String]) -> Option<ExitCode> {
    eprintln!("[remote-build] syncing to rw-build-server:{remote_root}...");
    if !sync_to_remote(repo_root, remote_root) {
        eprintln!("[remote-build] rsync failed");
        return Some(ExitCode::from(1));
    }
    ensure_remote_git_repo(remote_root);
    let args_str = args.join(" ");
    eprintln!("[remote-build] running: cargo xtask {args_str}");
    let status = Command::new("ssh")
        .arg("-t")
        .arg("rw-build-server")
        .arg(format!("cd {remote_root} && cargo xtask {args_str}"))
        .status()
        .ok()?;
    Some(ExitCode::from(status.code().unwrap_or(1) as u8))
}

fn should_skip_remote() -> bool {
    std::env::var("XTASK_LOCAL").is_ok()
        || std::env::current_dir()
            .map(|p| p.starts_with("/tmp"))
            .unwrap_or(false)
}

fn git_repo_root() -> Option<String> {
    let output = Command::new("git")
        .args(["rev-parse", "--show-toplevel"])
        .stderr(Stdio::null())
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn probe_server() -> bool {
    Command::new("ssh")
        .args([
            "-o",
            "ConnectTimeout=5",
            "-o",
            "BatchMode=yes",
            "rw-build-server",
            "exit",
            "0",
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn compute_remote_root(repo_root: &str) -> Option<String> {
    let hostname_output = Command::new("hostname")
        .stderr(Stdio::null())
        .output()
        .ok()
        .filter(|o| o.status.success())?;
    let hostname = String::from_utf8_lossy(&hostname_output.stdout)
        .trim()
        .to_string();
    let input = format!("{repo_root}{hostname}");
    let hash = Sha256::digest(input.as_bytes());
    let hex: String = hash[..8].iter().map(|b| format!("{b:02x}")).collect();
    Some(format!("/tmp/rw-{hex}"))
}

fn sync_to_remote(repo_root: &str, remote_root: &str) -> bool {
    Command::new("rsync")
        .args([
            "-az",
            "--delete",
            "--exclude=.git",
            "--filter=:- .gitignore",
            &format!("{repo_root}/"),
            &format!("rw-build-server:{remote_root}/"),
        ])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn ensure_remote_git_repo(remote_root: &str) {
    // The synced directory may contain a stale .git file (worktree marker)
    // from the local checkout. Remove it so we can init a real repo.
    // Then initialize a minimal git repo for tests that need libgit2.
    let script = format!(
        "cd {remote_root} && \
         if [ -f .git ]; then rm .git; fi && \
         if [ ! -d .git ]; then \
           git init -q && \
           git config user.email build@remote && \
           git config user.name Build; \
         fi && \
         git add -A -q 2>/dev/null; \
         git commit -q --allow-empty -m sync 2>/dev/null || true"
    );
    let _ = Command::new("ssh")
        .arg("rw-build-server")
        .arg(script)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}
