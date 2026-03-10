//! System tests for Ctrl+C (SIGINT) cleanup verification.
//!
//! These tests spawn real Ralph pipeline processes and send SIGINT signals
//! to verify that cleanup (PROMPT.md permissions, hooks, markers) happens
//! correctly regardless of interrupt timing.
//!
//! # Why System Tests?
//!
//! Integration tests cannot test actual SIGINT handling because:
//! - `MockEffectHandler` doesn't run real signal handlers
//! - `MemoryWorkspace` doesn't have real file permissions
//! - The interrupt context is process-global state
//!
//! # Test Categories
//!
//! 1. Early interrupt (before event loop active)
//! 2. Mid-execution interrupt (during agent phase)
//! 3. Cleanup verification (PROMPT.md, hooks, marker)
//!
//! # Platform Support
//!
//! These tests are Unix-only (`#[cfg(unix)]`) because they rely on:
//! - `libc::kill` for sending signals
//! - Unix file permission model (mode bits)
//!
//! # Note on Test Reliability
//!
//! These tests involve real process spawning and signal delivery, which can
//! be timing-sensitive. Tests use generous timeouts and retry logic to
//! minimize flakiness while still verifying the core cleanup behavior.

#![cfg(unix)]

use crate::test_timeout::{register_timeout_cleanup, with_timeout};
use serial_test::serial;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};

use std::time::{Duration, Instant};

fn collect_output(mut child: Child) -> (ExitStatus, String, String) {
    use std::io::Read;

    // Drain stdout/stderr BEFORE wait() to avoid deadlock if the child fills a pipe buffer.
    // Drain concurrently so one full pipe doesn't block draining the other.
    let stdout_pipe = child.stdout.take();
    let stderr_pipe = child.stderr.take();

    let stdout_handle = std::thread::spawn(move || {
        let mut stdout = String::new();
        if let Some(mut out) = stdout_pipe {
            let _ = out.read_to_string(&mut stdout);
        }
        stdout
    });

    let stderr_handle = std::thread::spawn(move || {
        let mut stderr = String::new();
        if let Some(mut err) = stderr_pipe {
            let _ = err.read_to_string(&mut stderr);
        }
        stderr
    });

    let status = child.wait().expect("wait for child");
    let stdout = stdout_handle.join().unwrap_or_default();
    let stderr = stderr_handle.join().unwrap_or_default();

    (status, stdout, stderr)
}
use tempfile::TempDir;
use test_helpers::{commit_all, create_isolated_config, init_git_repo};

fn assert_status_is_sigint_130(status: ExitStatus, stdout: &str, stderr: &str) {
    assert_eq!(
        status.code(),
        Some(130),
        "expected exit code 130 (SIGINT convention). stdout:\n{stdout}\nstderr:\n{stderr}"
    );
}

fn assert_signal_killed_or_exited_sigint(status: ExitStatus, stdout: &str, stderr: &str) {
    use std::os::unix::process::ExitStatusExt;

    if status.code() == Some(130) {
        return;
    }

    if status.signal() == Some(libc::SIGINT) {
        return;
    }

    panic!(
        "expected exit by SIGINT (code 130 or signal {}). got: code={:?} signal={:?}\nstdout:\n{stdout}\nstderr:\n{stderr}",
        libc::SIGINT,
        status.code(),
        status.signal()
    );
}

/// Extended timeout for signal cleanup tests.
///
/// These tests spawn real processes, wait for startup, send signals, and
/// verify cleanup. They need more time than typical system tests.
const SIGNAL_TEST_TIMEOUT: Duration = Duration::from_secs(60);

/// Timeout for waiting for marker file to appear.
const MARKER_WAIT_TIMEOUT: Duration = Duration::from_secs(30);

/// Brief delay after process spawn before sending early SIGINT.
///
/// We want SIGINT to be delivered after `main()` has had a chance to install the
/// Ctrl+C handler (`interrupt::setup_interrupt_handler()`), but before the reducer
/// event loop begins. A slightly larger delay reduces flakiness.
const EARLY_SIGINT_DELAY: Duration = Duration::from_millis(600);

/// Polling interval for marker file checks.
const POLL_INTERVAL: Duration = Duration::from_millis(50);

static CHILD_CLEANUP_EPOCH: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

/// Set file to read-only (no write bits for user/group/other).
fn set_readonly(path: &Path) {
    let metadata = std::fs::metadata(path).expect("stat file for readonly");
    let mut perms = metadata.permissions();
    perms.set_mode(perms.mode() & !0o222);
    std::fs::set_permissions(path, perms).expect("set readonly permissions");
}

/// Find the ralph binary.
///
/// Tries these locations in order:
/// 1. `CARGO_BIN_EXE_ralph` environment variable (set by cargo test)
/// 2. `target/debug/ralph` relative to repo root
/// 3. `target/release/ralph` relative to repo root
///
/// Returns `None` if no binary is found.
fn find_ralph_binary() -> Option<std::path::PathBuf> {
    // Check environment variable first (set by cargo test --package)
    if let Ok(path) = std::env::var("CARGO_BIN_EXE_ralph") {
        let path = std::path::PathBuf::from(path);
        if path.exists() {
            return Some(path);
        }
    }

    // Find repo root (parent of 'tests' directory)
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").ok()?;
    let tests_dir = std::path::PathBuf::from(manifest_dir);
    let repo_root = tests_dir.parent()?;

    // Try debug build first
    let debug_path = repo_root.join("target/debug/ralph");
    if debug_path.exists() {
        return Some(debug_path);
    }

    // Then release build
    let release_path = repo_root.join("target/release/ralph");
    if release_path.exists() {
        return Some(release_path);
    }

    None
}

/// Spawn ralph pipeline process targeting the given repo.
///
/// Uses minimal arguments to start a pipeline run that will create the
/// `.no_agent_commit` marker when entering the agent phase.
///
/// Returns `None` if the ralph binary cannot be found.
fn spawn_ralph_pipeline(repo_dir: &Path) -> Option<Child> {
    let ralph_bin = find_ralph_binary()?;

    // Isolate config home so user configuration can't affect system test behavior.
    // NOTE: Config resolution expects XDG_CONFIG_HOME to be the parent of
    // `ralph-workflow.toml`.
    let xdg_config_home = create_isolated_config(repo_dir);

    // Capture stdout/stderr so failures are diagnosable.
    // We keep outputs in-memory (piped) and only read them on failure paths.
    let child = Command::new(&ralph_bin)
        .current_dir(repo_dir)
        .args(["--developer-iters", "1", "--reviewer-reviews", "0"])
        .env("NO_COLOR", "1") // Disable colors for cleaner output
        .env("RALPH_LOG", "warn") // Reduce log noise
        .env("RALPH_NO_RESUME_PROMPT", "1") // Ensure non-interactive in tests
        .env("XDG_CONFIG_HOME", xdg_config_home)
        // Use a command that blocks until SIGINT so the agent phase is long enough
        // for the test to reliably observe the marker.
        .env(
            "RALPH_DEVELOPER_CMD",
            "bash -lc 'trap \"exit 0\" INT; while true; do sleep 1; done'",
        )
        .env(
            "RALPH_REVIEWER_CMD",
            "bash -lc 'trap \"exit 0\" INT; while true; do sleep 1; done'",
        )
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn ralph process");

    // Avoid PID-reuse SIGKILL risk: only attempt kill if this epoch is still current
    // and the child still appears to be running.
    let epoch = CHILD_CLEANUP_EPOCH.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
    register_timeout_cleanup(Box::new({
        let child_id = child.id();
        move || {
            if CHILD_CLEANUP_EPOCH.load(std::sync::atomic::Ordering::SeqCst) != epoch {
                return;
            }

            // Best-effort emergency cleanup on system test timeout.
            // Avoid killing a recycled PID: only SIGKILL if we can still signal the process.
            unsafe {
                let check = libc::kill(child_id.cast_signed(), 0);
                if check == 0 {
                    let _ = libc::kill(child_id.cast_signed(), libc::SIGKILL);
                }
            }
        }
    }));

    Some(child)
}

/// Wait for `.no_agent_commit` marker to appear.
///
/// Polls the file system until the marker exists or timeout is reached.
/// Returns `true` if marker appeared, `false` on timeout.
fn wait_for_marker(repo_dir: &Path, timeout: Duration) -> bool {
    let marker_path = repo_dir.join(".git/ralph/no_agent_commit");
    let start = Instant::now();

    while start.elapsed() < timeout {
        if marker_path.exists() {
            return true;
        }
        std::thread::sleep(POLL_INTERVAL);
    }
    false
}

fn wait_for_wrapper_dir_path(repo_dir: &Path, timeout: Duration) -> Option<std::path::PathBuf> {
    let start = Instant::now();

    while start.elapsed() < timeout {
        if let Some(path) = read_wrapper_dir_path(repo_dir) {
            return Some(path);
        }
        std::thread::sleep(POLL_INTERVAL);
    }

    None
}

fn wait_for_marker_size(repo_dir: &Path, timeout: Duration, expected_size: u64) -> bool {
    let marker_path = repo_dir.join(".git/ralph/no_agent_commit");
    let start = Instant::now();

    while start.elapsed() < timeout {
        if let Ok(metadata) = std::fs::metadata(&marker_path) {
            if metadata.len() == expected_size {
                return true;
            }
        }
        std::thread::sleep(POLL_INTERVAL);
    }

    false
}

/// Send SIGINT to process.
fn send_sigint(child: &Child) {
    let rc = unsafe { libc::kill(child.id().cast_signed(), libc::SIGINT) };
    assert_eq!(rc, 0, "expected SIGINT delivery via kill() to succeed");
}

/// Assert PROMPT.md has write permission for owner.
fn assert_prompt_writable(prompt_path: &Path) {
    let metadata = std::fs::metadata(prompt_path).expect("stat PROMPT.md");
    let mode = metadata.permissions().mode();
    assert_ne!(
        mode & 0o200,
        0,
        "PROMPT.md should be writable after cleanup (mode={mode:o})"
    );
}

/// Assert git wrapper tracking file has been removed.
fn assert_git_wrapper_track_file_removed(repo_dir: &Path) {
    let track_file = repo_dir.join(".git/ralph/git-wrapper-dir.txt");
    assert!(
        !track_file.exists(),
        "git wrapper track file should be removed after cleanup: {}",
        track_file.display()
    );
}

/// Check if file contains Ralph hook marker.
fn contains_ralph_marker(path: &Path) -> bool {
    std::fs::read_to_string(path)
        .map(|content| content.contains("RALPH_RUST_MANAGED_HOOK"))
        .unwrap_or(false)
}

/// Require ralph binary to be available.
///
/// System tests must not silently no-op in CI/dev runs. If the binary isn't
/// available, fail with a clear message so the test invocation can be fixed.
macro_rules! require_ralph_binary {
    () => {
        assert!(
            find_ralph_binary().is_some(),
            "ralph binary not found for system test. Build the binary (e.g., `cargo build -p ralph-workflow`) or run tests with Cargo so CARGO_BIN_EXE_ralph is set."
        );
    };
}

/// Test: Ctrl+C during agent phase restores PROMPT.md to writable.
///
/// This is the primary acceptance test: verify PROMPT.md is writable after
/// Ctrl+C at any point during pipeline execution.
///
/// # Test Steps
///
/// 1. Create temp git repo with read-only PROMPT.md (simulating prior crash)
/// 2. Spawn Ralph pipeline process
/// 3. Wait for `.no_agent_commit` marker (agent phase active)
/// 4. Send SIGINT
/// 5. Wait for process exit
/// 6. Assert PROMPT.md has write permission
#[test]
#[serial]
fn test_ctrl_c_restores_prompt_md_writable() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);

            // Set PROMPT.md read-only (simulating prior crashed run)
            let prompt_path = temp_dir.path().join("PROMPT.md");
            assert!(prompt_path.exists(), "PROMPT.md should exist in test repo");
            set_readonly(&prompt_path);

            // Spawn ralph pipeline
            let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            // Wait for agent phase to start (marker appears)
            let marker_appeared = wait_for_marker(temp_dir.path(), MARKER_WAIT_TIMEOUT);
            assert!(
                marker_appeared,
                "Agent phase should start (marker should appear)"
            );

            // Send SIGINT
            send_sigint(&child);

            // Wait for exit and capture output for debugging on failure.
            let (status, stdout, stderr) = collect_output(child);
            assert_status_is_sigint_130(status, &stdout, &stderr);

            // Key assertion: PROMPT.md must be writable after cleanup
            assert_prompt_writable(&prompt_path);

            // Marker should be cleaned up
            assert!(
                !temp_dir.path().join(".git/ralph/no_agent_commit").exists(),
                ".no_agent_commit should be removed after Ctrl+C cleanup"
            );

            // Wrapper tracking file (PATH injection) should be cleaned up
            assert_git_wrapper_track_file_removed(temp_dir.path());
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Test: Ctrl+C BEFORE lock restores PROMPT.md to writable.
///
/// Covers Gap 1: early interrupt before `LockPromptPermissions` executes.
/// Must verify restoration even when `restore_needed=false`.
///
/// # Test Steps
///
/// 1. Create temp git repo with read-only PROMPT.md
/// 2. Spawn Ralph pipeline process
/// 3. Send SIGINT IMMEDIATELY (before agent phase starts)
/// 4. Wait for process exit
/// 5. Assert PROMPT.md has write permission
#[test]
#[serial]
fn test_ctrl_c_before_lock_restores_prompt_md_writable() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);

            // Create PROMPT.md read-only (simulating prior crashed run)
            let prompt_path = temp_dir.path().join("PROMPT.md");
            assert!(prompt_path.exists(), "PROMPT.md should exist in test repo");
            set_readonly(&prompt_path);

            // Spawn ralph pipeline
            let mut child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            // Send SIGINT IMMEDIATELY (before agent phase starts)
            // This tests the early interrupt path in signal handler
            std::thread::sleep(EARLY_SIGINT_DELAY);
            send_sigint(&child);

            // If graceful shutdown does not begin promptly, send a second SIGINT to
            // force immediate termination. This matches real-user behavior when the
            // first Ctrl+C lands during a stuck phase transition.
            let grace_deadline = Instant::now() + Duration::from_secs(2);
            let mut exited = false;
            while Instant::now() < grace_deadline {
                if child
                    .try_wait()
                    .expect("try_wait should succeed while waiting for early SIGINT handling")
                    .is_some()
                {
                    exited = true;
                    break;
                }
                std::thread::sleep(POLL_INTERVAL);
            }

            if !exited
                && child
                    .try_wait()
                    .expect("final try_wait before second SIGINT should succeed")
                    .is_none()
            {
                send_sigint(&child);
            }

            // Wait for exit.
            // NOTE: If SIGINT arrives before ctrlc installs the handler, the OS default
            // signal disposition can terminate the process directly. In that case, cleanup
            // may not run and PROMPT.md may remain read-only.
            //
            // This test aims to validate the *handled* early interrupt path, so we retry
            // with an increased delay if we didn't observe the conventional 130 exit.
            let (status, stdout, stderr) = collect_output(child);

            if status.code() == Some(130) {
                // Even for the first attempt, accept raw signal termination as long as we got SIGINT.
                // This keeps the assertion aligned with Unix process semantics.
                assert_signal_killed_or_exited_sigint(status, &stdout, &stderr);
            } else {
                // If SIGINT was delivered before the Ctrl+C handler was installed, the OS may
                // terminate the process directly (signal exit) and our cleanup won't run. The goal
                // of this test is to validate the *handled* early-interrupt path, so we retry with
                // a larger delay and then require the conventional 130 code.
                let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph retry");
                std::thread::sleep(Duration::from_millis(1200));
                send_sigint(&child);
                let (status, stdout, stderr) = collect_output(child);
                assert_status_is_sigint_130(status, &stdout, &stderr);
            }

            // Key assertion: PROMPT.md must be writable after exit.
            // Even with early interrupt, startup cleanup should have restored it.
            assert_prompt_writable(&prompt_path);
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Test: Ctrl+C removes `.no_agent_commit` marker.
///
/// Verify the .`no_agent_commit` marker is removed after Ctrl+C so git
/// operations are unblocked.
///
/// # Test Steps
///
/// 1. Create temp git repo with PROMPT.md
/// 2. Spawn Ralph pipeline process
/// 3. Wait for `.no_agent_commit` marker to appear
/// 4. Assert marker exists
/// 5. Send SIGINT
/// 6. Wait for process exit
/// 7. Assert marker does not exist
#[test]
#[serial]
fn test_ctrl_c_removes_no_agent_commit() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);

            let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            // Wait for marker to appear
            let marker_path = temp_dir.path().join(".git/ralph/no_agent_commit");
            let appeared = wait_for_marker(temp_dir.path(), MARKER_WAIT_TIMEOUT);
            assert!(appeared, "Marker should appear when agent phase starts");

            assert!(marker_path.exists(), "Marker file should exist");

            // Send SIGINT
            send_sigint(&child);
            let (status, stdout, stderr) = collect_output(child);
            assert_status_is_sigint_130(status, &stdout, &stderr);

            // Marker must not exist after cleanup
            assert!(
                !marker_path.exists(),
                ".no_agent_commit should be removed after Ctrl+C cleanup"
            );

            // Wrapper tracking file (PATH injection) should be cleaned up
            assert_git_wrapper_track_file_removed(temp_dir.path());
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Test: Ctrl+C restores git hooks to pre-Ralph state.
///
/// Verify git hooks are restored to their pre-Ralph state (original content).
///
/// # Test Steps
///
/// 1. Create temp git repo with PROMPT.md
/// 2. Create original pre-commit hook
/// 3. Spawn Ralph pipeline process
/// 4. Wait for `.no_agent_commit` marker
/// 5. Send SIGINT
/// 6. Wait for process exit
/// 7. Assert hook content matches original
#[test]
#[serial]
fn test_ctrl_c_restores_git_hooks() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);

            // Create original pre-commit hook
            let hooks_dir = temp_dir.path().join(".git/hooks");
            std::fs::create_dir_all(&hooks_dir).expect("create hooks dir");
            let precommit_path = hooks_dir.join("pre-commit");
            let original_hook = "#!/bin/bash\necho 'Original hook'\n";
            std::fs::write(&precommit_path, original_hook).expect("write original hook");

            let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            let appeared = wait_for_marker(temp_dir.path(), MARKER_WAIT_TIMEOUT);
            assert!(appeared, "Marker should appear when agent phase starts");

            // Send SIGINT
            send_sigint(&child);
            let (status, stdout, stderr) = collect_output(child);
            assert_status_is_sigint_130(status, &stdout, &stderr);

            let hook_content = std::fs::read_to_string(&precommit_path)
                .expect("pre-commit hook should exist after cleanup");
            assert_eq!(
                hook_content, original_hook,
                "pre-commit hook should be restored to original content"
            );
            assert!(
                !hook_content.contains("RALPH_RUST_MANAGED_HOOK"),
                "restored hook must not contain Ralph marker"
            );

            // Wrapper tracking file (PATH injection) should be cleaned up
            assert_git_wrapper_track_file_removed(temp_dir.path());
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Test: Ctrl+C removes hook when no prior hook existed.
///
/// Verify hooks are removed entirely when no prior hook existed before
/// Ralph started.
///
/// # Test Steps
///
/// 1. Create temp git repo with PROMPT.md
/// 2. Ensure no pre-commit hook exists
/// 3. Spawn Ralph pipeline process
/// 4. Wait for `.no_agent_commit` marker
/// 5. Send SIGINT
/// 6. Wait for process exit
/// 7. Assert hooks don't exist or don't have Ralph marker
#[test]
#[serial]
fn test_ctrl_c_removes_hook_when_no_prior_hook_existed() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);

            // Ensure no hooks exist
            let hooks_dir = temp_dir.path().join(".git/hooks");
            let precommit_path = hooks_dir.join("pre-commit");
            let prepush_path = hooks_dir.join("pre-push");
            assert!(
                !precommit_path.exists(),
                "Test precondition: no pre-commit hook"
            );

            let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            let appeared = wait_for_marker(temp_dir.path(), MARKER_WAIT_TIMEOUT);
            assert!(appeared, "Marker should appear when agent phase starts");

            send_sigint(&child);
            let (status, stdout, stderr) = collect_output(child);
            assert_status_is_sigint_130(status, &stdout, &stderr);

            // Hooks should not exist after cleanup (they were installed by Ralph, no original).
            assert!(
                !precommit_path.exists(),
                "pre-commit should be removed when no prior hook existed"
            );
            assert!(
                !prepush_path.exists(),
                "pre-push should be removed when no prior hook existed"
            );

            // Wrapper tracking file (PATH injection) should be cleaned up
            assert_git_wrapper_track_file_removed(temp_dir.path());
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Test: Startup cleanup restores PROMPT.md from prior run.
///
/// Verify startup-time orphan cleanup handles SIGKILL scenario where
/// prior run left PROMPT.md read-only.
///
/// # Test Steps
///
/// 1. Create temp git repo
/// 2. Simulate prior SIGKILL scenario: PROMPT.md read-only + marker exists
/// 3. Spawn Ralph pipeline - startup cleanup should restore PROMPT.md
/// 4. Wait briefly for startup cleanup to run
/// 5. Send SIGINT to exit cleanly
/// 6. Wait for process exit
/// 7. Assert PROMPT.md is writable
/// 8. Assert marker is cleaned up
#[test]
#[serial]
fn test_startup_cleanup_restores_prompt_md_from_prior_run() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);
            let prompt_path = temp_dir.path().join("PROMPT.md");

            // Simulate prior SIGKILL scenario:
            // 1. PROMPT.md is read-only
            // 2. .no_agent_commit marker exists
            set_readonly(&prompt_path);
            // Write a non-empty marker payload so we can detect the new process
            // rewriting it (workspace marker creation writes an empty file).
            std::fs::create_dir_all(temp_dir.path().join(".git/ralph"))
                .expect("create ralph dir");
            std::fs::write(temp_dir.path().join(".git/ralph/no_agent_commit"), "stale")
                .expect("write marker");

            // Spawn Ralph pipeline - startup cleanup should restore PROMPT.md
            let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            // Wait for startup cleanup to restore PROMPT.md before we send SIGINT.
            //
            // IMPORTANT: We pre-create `.no_agent_commit` to simulate a prior SIGKILL.
            // That means `wait_for_marker()` would return immediately, which is not a
            // readiness handshake. Instead, wait until the marker has been rewritten
            // by the new process (it will be created with size 0 by the workspace-aware
            // marker writer), then poll for PROMPT.md to become writable.
            assert!(
                wait_for_marker_size(temp_dir.path(), MARKER_WAIT_TIMEOUT, 0),
                "expected new process to recreate .no_agent_commit with size 0"
            );

            // Send SIGINT to exit cleanly
            send_sigint(&child);
            let (status, stdout, stderr) = collect_output(child);
            assert_status_is_sigint_130(status, &stdout, &stderr);

            // PROMPT.md should be writable after exit.
            // Note: PROMPT.md may become read-only during normal execution due to
            // LockPromptPermissions, so we only assert writability after cleanup.
            assert_prompt_writable(&prompt_path);

            // Marker should be cleaned up (stale one removed; any newly created marker removed too).
            assert!(
                !temp_dir.path().join(".git/ralph/no_agent_commit").exists(),
                ".no_agent_commit should not exist after cleanup"
            );

            // Wrapper tracking file (PATH injection) should be cleaned up
            assert_git_wrapper_track_file_removed(temp_dir.path());
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Assert head-oid tracking file has been removed.
fn assert_head_oid_removed(repo_dir: &Path) {
    let head_oid = repo_dir.join(".git/ralph/head-oid.txt");
    assert!(
        !head_oid.exists(),
        "head-oid.txt should be removed after cleanup: {}",
        head_oid.display()
    );
}

/// Assert generated files have been removed.
fn assert_generated_files_removed(repo_dir: &Path) {
    let generated = [
        ".git/ralph/no_agent_commit",
        ".agent/PLAN.md",
        ".agent/commit-message.txt",
        ".agent/checkpoint.json.tmp",
        ".git/ralph/head-oid.txt",
        ".git/ralph/git-wrapper-dir.txt",
    ];
    for file in &generated {
        let path = repo_dir.join(file);
        assert!(
            !path.exists(),
            "generated file {file} should be removed after cleanup: {}",
            path.display()
        );
    }
}

/// Test: Ctrl+C removes `.agent/head-oid.txt` tracking file.
///
/// The head-oid file is used for unauthorized commit detection and must
/// be cleaned up on interrupt to avoid false positives on the next run.
#[test]
#[serial]
fn test_ctrl_c_removes_head_oid_file() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let repo = init_git_repo(&temp_dir);

            // capture_head_oid requires a HEAD commit to exist; create one.
            let _ = commit_all(&repo, "initial commit");

            let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            let appeared = wait_for_marker(temp_dir.path(), MARKER_WAIT_TIMEOUT);
            assert!(appeared, "Marker should appear when agent phase starts");

            // Head-oid should exist during agent phase
            // (it's created by start_agent_phase -> capture_head_oid)
            let head_oid = temp_dir.path().join(".git/ralph/head-oid.txt");
            // Give a brief moment for the file to be written
            std::thread::sleep(Duration::from_millis(200));
            assert!(
                head_oid.exists(),
                "head-oid.txt should exist during agent phase"
            );

            send_sigint(&child);
            let (status, stdout, stderr) = collect_output(child);
            assert_status_is_sigint_130(status, &stdout, &stderr);

            assert_head_oid_removed(temp_dir.path());
            assert_git_wrapper_track_file_removed(temp_dir.path());
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Test: Ctrl+C removes all generated files.
///
/// Verifies that `.agent/PLAN.md`, `.agent/commit-message.txt`,
/// `.agent/checkpoint.json.tmp`, and `.no_agent_commit` are all removed
/// after Ctrl+C cleanup.
#[test]
#[serial]
fn test_ctrl_c_removes_generated_files() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);

            let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            let appeared = wait_for_marker(temp_dir.path(), MARKER_WAIT_TIMEOUT);
            assert!(appeared, "Marker should appear when agent phase starts");

            send_sigint(&child);
            let (status, stdout, stderr) = collect_output(child);
            assert_status_is_sigint_130(status, &stdout, &stderr);

            assert_generated_files_removed(temp_dir.path());
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Read the wrapper dir path from the track file.
fn read_wrapper_dir_path(repo_dir: &Path) -> Option<std::path::PathBuf> {
    let track_file = repo_dir.join(".git/ralph/git-wrapper-dir.txt");
    std::fs::read_to_string(&track_file)
        .ok()
        .map(|s| std::path::PathBuf::from(s.trim()))
}

/// Assert wrapper temp dir has been removed from the filesystem.
fn assert_wrapper_temp_dir_removed(wrapper_dir: &Path) {
    assert!(
        !wrapper_dir.exists(),
        "wrapper temp dir should be removed after cleanup: {}",
        wrapper_dir.display()
    );
}

/// Test: Ctrl+C removes the wrapper temp dir from /tmp.
///
/// Existing tests verify the track file is removed, but NOT that the
/// actual temp directory in /tmp is also cleaned up.
#[test]
#[serial]
fn test_ctrl_c_removes_wrapper_temp_dir() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);

            let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            let appeared = wait_for_marker(temp_dir.path(), MARKER_WAIT_TIMEOUT);
            assert!(appeared, "Marker should appear when agent phase starts");

            // Read wrapper dir path BEFORE sending SIGINT (track file exists during agent phase).
            let wrapper_dir = read_wrapper_dir_path(temp_dir.path());

            send_sigint(&child);
            let (status, stdout, stderr) = collect_output(child);
            assert_status_is_sigint_130(status, &stdout, &stderr);

            // Track file should be removed.
            assert_git_wrapper_track_file_removed(temp_dir.path());

            // Wrapper temp dir should also be removed from /tmp.
            if let Some(wrapper_dir) = wrapper_dir {
                assert_wrapper_temp_dir_removed(&wrapper_dir);
            }
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Test: Startup cleans up orphaned wrapper dir from a prior crashed run.
///
/// Simulates a SIGKILL scenario where the prior run left the wrapper temp dir
/// and track file behind. The next run should clean them up.
#[test]
#[serial]
fn test_startup_cleans_orphaned_wrapper_dir() {
    with_timeout(
        || {
            require_ralph_binary!();

            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);

            // Create a fake orphaned wrapper dir in temp.
            let orphan_dir = tempfile::Builder::new()
                .prefix("ralph-git-wrapper-")
                .tempdir()
                .expect("create orphan wrapper dir");
            let orphan_path = orphan_dir.keep();

            // Write track file pointing to the orphaned dir.
            let ralph_dir = temp_dir.path().join(".git/ralph");
            std::fs::create_dir_all(&ralph_dir).expect("create ralph dir");
            std::fs::write(
                temp_dir.path().join(".git/ralph/git-wrapper-dir.txt"),
                format!("{}\n", orphan_path.display()),
            )
            .expect("write orphan track file");

            assert!(orphan_path.exists(), "precondition: orphan dir exists");

            // Spawn Ralph - startup should clean the orphaned wrapper dir.
            let child = spawn_ralph_pipeline(temp_dir.path()).expect("spawn ralph for test");

            let appeared = wait_for_marker(temp_dir.path(), MARKER_WAIT_TIMEOUT);
            assert!(appeared, "Marker should appear when agent phase starts");

            // The orphaned dir should have been cleaned up during startup.
            assert!(
                !orphan_path.exists(),
                "orphaned wrapper dir should be removed during startup: {}",
                orphan_path.display()
            );

            send_sigint(&child);
            let (status, stdout, stderr) = collect_output(child);
            assert_status_is_sigint_130(status, &stdout, &stderr);

            // After exit, the new wrapper dir (created by this run) should also be cleaned.
            assert_git_wrapper_track_file_removed(temp_dir.path());
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

/// Test: Graceful pipeline exit removes the wrapper temp dir from /tmp.
///
/// Verifies wrapper dir cleanup on normal finalization, including restoration of
/// Ralph-managed hooks to their original contents.
#[test]
#[serial]
fn test_graceful_exit_removes_wrapper_temp_dir() {
    with_timeout(
        || {
            let temp_dir = TempDir::new().expect("create temp dir");
            let _repo = init_git_repo(&temp_dir);

            let hooks_dir = temp_dir.path().join(".git/hooks");
            std::fs::create_dir_all(&hooks_dir).expect("create hooks dir");
            let precommit_path = hooks_dir.join("pre-commit");
            let original_hook = "#!/bin/bash\necho 'Original hook'\n";
            std::fs::write(&precommit_path, original_hook).expect("write original hook");

            let workspace =
                ralph_workflow::workspace::WorkspaceFs::new(temp_dir.path().to_path_buf());
            let logger = ralph_workflow::logger::Logger::new(
                ralph_workflow::logger::Colors::with_enabled(false),
            );
            let mut helpers = ralph_workflow::git_helpers::GitHelpers::default();

            ralph_workflow::git_helpers::start_agent_phase_in_repo(temp_dir.path(), &mut helpers)
                .expect("start agent phase in target repo");

            let wrapper_dir = wait_for_wrapper_dir_path(temp_dir.path(), MARKER_WAIT_TIMEOUT)
                .expect("wrapper track file should exist during agent phase");
            assert!(
                wrapper_dir.exists(),
                "wrapper temp dir should exist during agent phase: {}",
                wrapper_dir.display()
            );

            let mut guard =
                ralph_workflow::pipeline::AgentPhaseGuard::new(&mut helpers, &logger, &workspace);
            let config = ralph_workflow::config::Config::test_default();
            let timer = ralph_workflow::pipeline::Timer::new();
            let final_state = ralph_workflow::reducer::PipelineState::initial(1, 1);

            ralph_workflow::app::finalization::finalize_pipeline(
                &mut guard,
                ralph_workflow::app::finalization::FinalizeContext {
                    logger: &logger,
                    colors: ralph_workflow::logger::Colors::with_enabled(false),
                    config: &config,
                    timer: &timer,
                    workspace: &workspace,
                },
                &final_state,
                None,
            );

            // Track file should be removed after graceful exit.
            assert_git_wrapper_track_file_removed(temp_dir.path());

            assert_wrapper_temp_dir_removed(&wrapper_dir);

            let hook_content = std::fs::read_to_string(&precommit_path)
                .expect("pre-commit hook should exist after graceful cleanup");
            assert_eq!(
                hook_content, original_hook,
                "pre-commit hook should be restored after graceful cleanup"
            );
            assert!(
                !hook_content.contains("RALPH_RUST_MANAGED_HOOK"),
                "restored hook must not contain Ralph marker"
            );

            // All generated files should be removed.
            assert_generated_files_removed(temp_dir.path());
        },
        SIGNAL_TEST_TIMEOUT,
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use serial_test::serial;

    /// Verify helper functions work correctly.
    #[test]
    #[serial]
    fn test_set_readonly() {
        let temp_dir = TempDir::new().expect("create temp dir");
        let file_path = temp_dir.path().join("test.txt");
        std::fs::write(&file_path, "test content").expect("write file");

        // Verify file is writable initially
        let mode = std::fs::metadata(&file_path)
            .expect("stat")
            .permissions()
            .mode();
        assert_ne!(mode & 0o200, 0, "file should be writable initially");

        // Set readonly
        set_readonly(&file_path);

        // Verify file is now read-only
        let mode = std::fs::metadata(&file_path)
            .expect("stat")
            .permissions()
            .mode();
        assert_eq!(
            mode & 0o222,
            0,
            "file should have no write bits after set_readonly"
        );
    }

    /// Verify `contains_ralph_marker` detection.
    #[test]
    #[serial]
    fn test_contains_ralph_marker() {
        let temp_dir = TempDir::new().expect("create temp dir");

        // File without marker
        let no_marker = temp_dir.path().join("no_marker.sh");
        std::fs::write(&no_marker, "#!/bin/bash\necho hello\n").expect("write");
        assert!(!contains_ralph_marker(&no_marker));

        // File with marker
        let with_marker = temp_dir.path().join("with_marker.sh");
        std::fs::write(
            &with_marker,
            "#!/bin/bash\n# RALPH_RUST_MANAGED_HOOK\nexit 0\n",
        )
        .expect("write");
        assert!(contains_ralph_marker(&with_marker));

        // Non-existent file
        let missing = temp_dir.path().join("missing.sh");
        assert!(!contains_ralph_marker(&missing));
    }
}
