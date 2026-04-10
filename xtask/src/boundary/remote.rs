//! Remote build server dispatch.
//!
//! When `cargo xtask` is invoked outside of `/tmp` (i.e. not already on a
//! build server), this module probes `rw-build-server` and `rw-build-server-2`
//! via SSH in parallel and, if either is reachable, selects the one with the
//! lower 1-minute load average, then syncs the working tree and re-executes
//! the same subcommand there.  Falls back to local execution when both servers
//! are unreachable.
//!
//! # Server selection
//!
//! Both build servers are probed concurrently (`ConnectTimeout=5`).  The server
//! with the lower 1-minute load average (from `/proc/loadavg`) is selected.
//! When loads are within `0.1` of each other they are treated as equivalent and
//! one is chosen pseudo-randomly using sub-second system time.  If only one
//! server responds it is used unconditionally.
//!
//! # Skip conditions
//!
//! - CWD starts with `/tmp` — already running on a build server.
//! - `XTASK_LOCAL=1` — **emergency fallback only**: use ONLY when both
//!   build servers are confirmed unreachable (network down, servers offline).
//!   Never set this to work around a test failure or for convenience — that
//!   defeats the entire remote-first design.
//!
//! # Remote root path
//!
//! `/tmp/rw-<first-16-hex-chars of SHA-256(git-root + hostname + server)>`.
//! The hash makes the path unique per (local repo, machine, server) triple and
//! stable across invocations, so incremental rsyncs are cheap and each server
//! maintains its own independent incremental build cache.

// `pick_server` lives in `crate::domain::server_selection` because it is a
// pure function with no I/O — exactly what the domain layer is for.  The
// boundary module (this file) owns all I/O: SSH, rsync, hostname, threading.
use crate::domain::server_selection::pick_server;
use sha2::{Digest, Sha256};
use std::process::{Command, ExitCode, Stdio};

const SERVERS: [&str; 2] = ["rw-build-server", "rw-build-server-2"];

/// Attempt to dispatch to the least-loaded remote build server.
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
    let Some(server) = select_server() else {
        eprintln!("[remote-build] no build server reachable, running locally");
        return None;
    };
    eprintln!("[remote-build] selected server: {server}");
    let remote_root = compute_remote_root(repo_root, &server)?;
    execute_on_remote(&server, repo_root, &remote_root, args)
}

fn execute_on_remote(
    server: &str,
    repo_root: &str,
    remote_root: &str,
    args: &[String],
) -> Option<ExitCode> {
    // Kill any stale cargo/rustc processes from a previous interrupted run
    // that would otherwise hold file locks or race on target/ artifacts.
    kill_stale_processes(server, remote_root);
    eprintln!("[remote-build] syncing to {server}:{remote_root}...");
    if !sync_to_remote(server, repo_root, remote_root) {
        eprintln!("[remote-build] rsync failed");
        return Some(ExitCode::from(1));
    }
    ensure_remote_git_repo(server, remote_root);
    let args_str = args.join(" ");
    eprintln!("[remote-build] running: cargo xtask {args_str}");
    // Disable incremental compilation on the remote to avoid fragile
    // dep-graph.part.bin moves that fail when /tmp cleanup or concurrent
    // lane I/O removes intermediate files mid-build.
    let status = Command::new("ssh")
        .arg("-t")
        .arg(server)
        .arg(format!(
            "cd {remote_root} && CARGO_INCREMENTAL=0 cargo xtask {args_str}"
        ))
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

/// Queries `/proc/loadavg` on the remote server and returns the 1-minute load
/// average, or `None` if the server is unreachable or the output cannot be
/// parsed.
fn query_server_load(server: &str) -> Option<f32> {
    let output = Command::new("ssh")
        .args([
            "-o",
            "ConnectTimeout=5",
            "-o",
            "BatchMode=yes",
            server,
            "cat /proc/loadavg",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .next()?
        .parse::<f32>()
        .ok()
}

/// Probes all [`SERVERS`] in parallel, then delegates to [`pick_server`].
///
/// Returns the name of the selected server, or `None` if all servers are
/// unreachable.
fn select_server() -> Option<String> {
    let handles: Vec<_> = SERVERS
        .iter()
        .map(|&server| {
            let s = server.to_string();
            std::thread::spawn(move || {
                let load = query_server_load(&s);
                (s, load)
            })
        })
        .collect();
    let loads: Vec<(String, Option<f32>)> =
        handles.into_iter().filter_map(|h| h.join().ok()).collect();
    let load_refs: Vec<(&str, Option<f32>)> = loads
        .iter()
        .map(|(name, load)| (name.as_str(), *load))
        .collect();
    let tiebreak = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    pick_server(&load_refs, tiebreak).map(String::from)
}

/// Computes the stable remote root path for a given (repo, machine, server) triple.
///
/// The hash incorporates the server name so each server has its own independent
/// `/tmp/rw-<hash>` path, preserving incremental build caches when switching
/// between servers.
fn compute_remote_root(repo_root: &str, server: &str) -> Option<String> {
    let hostname_output = Command::new("hostname")
        .stderr(Stdio::null())
        .output()
        .ok()
        .filter(|o| o.status.success())?;
    let hostname = String::from_utf8_lossy(&hostname_output.stdout)
        .trim()
        .to_string();
    let input = format!("{repo_root}{hostname}{server}");
    let hash = Sha256::digest(input.as_bytes());
    let hex: String = hash[..8].iter().map(|b| format!("{b:02x}")).collect();
    Some(format!("/tmp/rw-{hex}"))
}

fn sync_to_remote(server: &str, repo_root: &str, remote_root: &str) -> bool {
    Command::new("rsync")
        .args([
            "-az",
            "--delete",
            "--exclude=.git/",
            "--filter=:- .gitignore",
            &format!("{repo_root}/"),
            &format!("{server}:{remote_root}/"),
        ])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn kill_stale_processes(server: &str, remote_root: &str) {
    // When an SSH session terminates abruptly (timeout, network drop), cargo
    // and rustc child processes on the remote keep running.  A subsequent rsync
    // + build then races against stale processes that hold open file descriptors
    // in target/, causing "No such file or directory" errors on intermediate
    // artifacts (dep-graph.part.bin, rmeta temp dirs, libssh2.h).
    //
    // We kill only cargo and rustc processes whose command line references this
    // remote root, not any arbitrary process (rsync, ssh, shell) that happens
    // to mention the path.  This prevents concurrent `cargo xtask` invocations
    // from killing each other's active transport sessions.
    //
    // SELF-EXCLUSION: the kill script runs in a shell whose command line contains
    // the remote_root (embedded in the pgrep pattern argument).  Without excluding
    // $$ (the script's own shell PID), pgrep would match it and the kill would
    // terminate the script mid-execution, leaving stale processes alive and causing
    // SIGTERM propagation to the wrong process tree.
    //
    // TARGET CLEANUP: when stale processes are killed, they may leave the
    // target/ directory in a corrupted state (missing deps/ subdirs, partial
    // rmeta files).  We detect stale processes first, then kill them and
    // remove target/ entirely so the next build starts from a clean slate.
    // Detection runs before killing so a clean reconnection (no stale
    // processes) preserves the incremental build cache.
    let script = format!(
        "SELF=$$; STALE=0; \
         for proc in cargo rustc rustdoc clippy-driver; do \
           pgrep -f \"$proc.*{remote_root}\" 2>/dev/null | while read pid; do \
             [ \"$pid\" != \"$SELF\" ] && echo \"$pid\"; \
           done; \
         done > /tmp/rw_stale_pids_$$; \
         if [ -s /tmp/rw_stale_pids_$$ ]; then STALE=1; \
           while read pid; do kill \"$pid\" 2>/dev/null; done < /tmp/rw_stale_pids_$$; \
           sleep 1; \
           while read pid; do kill -9 \"$pid\" 2>/dev/null; done < /tmp/rw_stale_pids_$$; \
         fi; \
         rm -f /tmp/rw_stale_pids_$$; \
         [ \"$STALE\" = \"1\" ] && rm -rf {remote_root}/target; \
         true"
    );
    let _ = Command::new("ssh")
        .arg(server)
        .arg(script)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

fn ensure_remote_git_repo(server: &str, remote_root: &str) {
    // The synced directory has no .git/ (excluded from rsync), but a worktree
    // `.git` *file* (containing `gitdir: /local/path/...`) may survive the
    // rsync because `--exclude=.git/` only matches the directory form.
    // That stale pointer causes libgit2 to fail with "No such file or directory"
    // on every git operation.  Remove the file before initializing.
    //
    // Sequence:
    // 1. If `.git` is a regular file (worktree pointer), delete it.
    // 2. If no valid git repo exists, run `git init`.
    // 3. Stage + commit so HEAD is valid and git_diff / git_snapshot work.
    let script = format!(
        "cd {remote_root} && \
         if [ -f .git ]; then rm -f .git; fi && \
         git rev-parse --git-dir >/dev/null 2>&1 || \
         (git init -q && \
          git config user.email build@remote && \
          git config user.name Build); \
         git add -A -q 2>/dev/null; \
         git commit -q --allow-empty -m sync 2>/dev/null || true"
    );
    let _ = Command::new("ssh")
        .arg(server)
        .arg(script)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}
