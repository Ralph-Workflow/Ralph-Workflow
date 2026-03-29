//! Global process registry for tracking spawned agent PIDs and cleanup.
//!
//! This module provides a centralized registry that tracks all spawned agent process
//! PIDs, enabling reliable cleanup when the workflow completes, is interrupted, or panics.
//!
//! # Why a global registry?
//!
//! Without a centralized registry, process lifecycle is managed through local variables
//! that can be lost on panic, forced exit, or early return. The registry provides a
//! single source of truth for "what agent processes exist right now" that survives
//! across call stacks and can be consulted during cleanup.
//!
//! # Signal handler safety
//!
//! The `kill_all_registered_raw()` function is safe to call from signal handler context
//! (the second Ctrl+C path). It uses `try_lock()` to avoid deadlocking if the main
//! thread holds the mutex. If the lock cannot be acquired, it returns without blocking
//! — process cleanup will be attempted by the AgentPhaseGuard Drop handler instead.

use std::collections::HashSet;
use std::sync::{Mutex, OnceLock};

static REGISTRY: OnceLock<Mutex<HashSet<u32>>> = OnceLock::new();

fn registry() -> &'static Mutex<HashSet<u32>> {
    REGISTRY.get_or_init(|| Mutex::new(HashSet::new()))
}

// =============================================================================
// Public API (available to external test crates via test-utils feature)
// =============================================================================

/// Register a process PID with the global registry.
///
/// Called immediately after successful agent spawn to ensure the PID is tracked
/// before any subsequent setup that could fail.
pub fn register(pid: u32) {
    let mut guard = registry().lock().expect("process registry mutex poisoned");
    guard.insert(pid);
}

/// Unregister a process PID from the global registry.
///
/// Called when a process is confirmed to have exited. Idempotent - unregistering
/// a PID that was never registered is a no-op.
pub fn unregister(pid: u32) {
    let mut guard = registry().lock().expect("process registry mutex poisoned");
    guard.remove(&pid);
}

/// Return a snapshot of currently registered PIDs.
///
/// Used for logging and diagnostics.
pub fn registered_pids() -> Vec<u32> {
    let guard = registry().lock().expect("process registry mutex poisoned");
    guard.iter().copied().collect()
}

// =============================================================================
// Pure policy helpers (no side effects)
// =============================================================================

/// Pure: compute SIGTERM signals for graceful shutdown.
/// Returns pairs of (target, signal) for process group and process.
fn compute_signals_for_term(pid: u32) -> [(i32, libc::c_int); 2] {
    let pid_i32 = pid.min(i32::MAX as u32) as i32;
    [(-pid_i32, libc::SIGTERM), (pid_i32, libc::SIGTERM)]
}

/// Pure: compute SIGKILL signals for forceful termination.
/// Returns pairs of (target, signal) where negative target means process group.
fn compute_signals_for_kill(pid: u32) -> [(i32, libc::c_int); 2] {
    let pid_i32 = pid.min(i32::MAX as u32) as i32;
    [(-pid_i32, libc::SIGKILL), (pid_i32, libc::SIGKILL)]
}

/// Check if a process is still alive using kill(pid, 0).
fn is_process_alive(pid: u32) -> bool {
    let pid_i32 = pid.min(i32::MAX as u32) as i32;
    // kill(pid, 0) returns 0 if process exists and we have permission to signal it.
    // Returns -1 with ESRCH if process does not exist, or EPERM if we lack permission.
    // Any error other than ESRCH means the process exists (we just can't signal it).
    let result = unsafe { libc::kill(pid_i32, 0) };
    if result == 0 {
        true // Process exists and we can signal it
    } else {
        // Check if it failed because process doesn't exist (ESRCH)
        std::io::Error::last_os_error()
            .raw_os_error()
            .map(|e| e != libc::ESRCH)
            .unwrap_or(false)
    }
}

/// Poll a set of PIDs until all have exited or deadline is reached.
/// Returns the PIDs that are still alive.
fn poll_until_exited_or_deadline(pids: &[u32], deadline: std::time::Instant) -> Vec<u32> {
    let mut remaining: Vec<u32> = pids.to_vec();
    while !remaining.is_empty() && std::time::Instant::now() < deadline {
        remaining = poll_tick(remaining);
    }
    remaining
}

/// Run one polling tick: retain alive PIDs and sleep if any remain.
fn poll_tick(mut remaining: Vec<u32>) -> Vec<u32> {
    remaining.retain(|&pid| is_process_alive(pid));
    if !remaining.is_empty() {
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    remaining
}

/// Signal-safe shutdown: uses `try_lock()` to avoid deadlocking when called
/// from a signal handler while the main thread holds the registry mutex.
/// If the lock cannot be acquired, PIDs cannot be drained, but the function
/// still returns without blocking (the AgentPhaseGuard Drop will retry later).
///
/// Three-phase shutdown:
/// 1. SIGTERM to process group and process (graceful)
/// 2. Grace period (500ms) with polling for exit
/// 3. SIGKILL escalation for resistant processes + verification poll
#[cfg(unix)]
pub fn kill_all_registered_raw() {
    let pids = drain_registry_with_retry();
    if pids.is_empty() {
        return;
    }
    sigterm_all(&pids);
    let grace_deadline = std::time::Instant::now() + std::time::Duration::from_millis(500);
    let still_alive = poll_until_exited_or_deadline(&pids, grace_deadline);
    sigkill_survivors(&still_alive);
    sigkill_stragglers();
}

/// Drain the registry using try_lock with up to 200 retries (signal-safe).
#[cfg(unix)]
fn drain_registry_with_retry() -> Vec<u32> {
    (0..200)
        .find_map(|_| try_drain_registry_once())
        .unwrap_or_default()
}

/// Attempt a single try_lock drain of the registry.
///
/// Returns `Some(pids)` on success (acquired or poisoned lock).
/// Returns `None` and sleeps 1ms on `WouldBlock` (retry needed).
#[cfg(unix)]
fn try_drain_registry_once() -> Option<Vec<u32>> {
    match registry().try_lock() {
        Ok(mut guard) => Some(guard.drain().collect()),
        Err(std::sync::TryLockError::Poisoned(poisoned)) => {
            Some(poisoned.into_inner().drain().collect())
        }
        Err(std::sync::TryLockError::WouldBlock) => {
            std::thread::sleep(std::time::Duration::from_millis(1));
            None
        }
    }
}

/// Phase 1: SIGTERM to process group and individual processes.
/// Agents call setpgid(0, 0) in pre_exec so they are in their own process groups.
#[cfg(unix)]
fn sigterm_all(pids: &[u32]) {
    pids.iter()
        .flat_map(|&pid| compute_signals_for_term(pid))
        .for_each(|(target, sig)| {
            let _ = unsafe { libc::kill(target, sig) };
        });
}

/// Phase 3: SIGKILL escalation for processes that survived SIGTERM + verification poll.
#[cfg(unix)]
fn sigkill_survivors(still_alive: &[u32]) {
    if still_alive.is_empty() {
        return;
    }
    still_alive
        .iter()
        .flat_map(|&pid| compute_signals_for_kill(pid))
        .for_each(|(target, sig)| {
            let _ = unsafe { libc::kill(target, sig) };
        });
    let kill_deadline = std::time::Instant::now() + std::time::Duration::from_millis(500);
    let _ = poll_until_exited_or_deadline(still_alive, kill_deadline);
}

/// Phase 4: Recheck for late-registered PIDs and SIGKILL them immediately.
///
/// Between the initial drain and now (~1s), new PIDs may have been registered.
/// This phase runs unconditionally to ensure no PIDs escape cleanup.
#[cfg(unix)]
fn sigkill_stragglers() {
    let stragglers: Vec<u32> = match registry().try_lock() {
        Ok(mut guard) => guard.drain().collect(),
        Err(_) => Vec::new(),
    };
    stragglers
        .iter()
        .flat_map(|&pid| compute_signals_for_kill(pid))
        .for_each(|(target, sig)| {
            let _ = unsafe { libc::kill(target, sig) };
        });
}

/// Thin wiring: gather inputs, call pure policy, execute effects.
/// Uses `output()` instead of `spawn()` to wait for taskkill to complete,
/// ensuring each process is actually terminated before returning.
#[cfg(windows)]
pub fn kill_all_registered_raw() {
    let pids: Vec<u32> = {
        let mut guard = registry().lock().expect("process registry mutex poisoned");
        guard.drain().collect()
    };

    for pid in pids {
        // Use output() instead of spawn() to wait for taskkill to complete.
        // /F = forceful, /T = kill child processes too.
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .output();
    }
}

/// Spawn a real process and register its PID with the global registry.
///
/// Returns the child handle and PID. The caller is responsible for cleaning up
/// the child process.
///
/// This is a test helper for external test crates to set up process registry test state.
#[cfg(any(test, feature = "test-utils"))]
pub fn spawn_and_register_for_test(command: &str, args: &[&str]) -> std::process::Child {
    use std::process::Stdio;

    let mut cmd = std::process::Command::new(command);
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = cmd.spawn().expect("spawn process for test");

    let pid = child.id();
    register(pid);
    child
}

// =============================================================================
// Test-only functions
// =============================================================================

/// Test-only: clear the registry of all PIDs.
///
/// Used in tests to ensure clean state between test cases.
/// Uses `lock().expect()` to guarantee cleanup.
#[cfg(test)]
pub(crate) fn clear_registry() {
    let mut guard = registry().lock().expect("process registry lock poisoned");
    guard.clear();
}

// =============================================================================
// Unit tests
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    fn registry_test_guard() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: std::sync::OnceLock<std::sync::Mutex<()>> = std::sync::OnceLock::new();
        LOCK.get_or_init(|| std::sync::Mutex::new(()))
            .lock()
            .expect("process registry test lock poisoned")
    }

    #[test]
    fn register_and_unregister_round_trip() {
        let _guard = registry_test_guard();
        super::clear_registry();

        register(12345);
        assert!(registered_pids().contains(&12345));

        unregister(12345);
        assert!(!registered_pids().contains(&12345));

        super::clear_registry();
    }

    #[test]
    fn unregister_nonexistent_is_noop() {
        let _guard = registry_test_guard();
        super::clear_registry();

        unregister(99999);

        super::clear_registry();
    }

    #[test]
    fn duplicate_register_is_idempotent() {
        let _guard = registry_test_guard();
        super::clear_registry();

        register(12345);
        register(12345);
        let pids = registered_pids();
        assert_eq!(pids.len(), 1);
        assert!(pids.contains(&12345));

        super::clear_registry();
    }

    #[test]
    fn kill_all_registered_clears_registry() {
        let _guard = registry_test_guard();
        super::clear_registry();

        register(12345);
        register(67890);

        kill_all_registered_raw();
        assert!(registered_pids().is_empty());

        super::clear_registry();
    }

    /// Integration-style test: spawn a real child process, register it,
    /// call kill_all_registered_raw(), and verify the process is killed.
    #[test]
    #[cfg(unix)]
    fn kill_all_registered_raw_actually_kills_process() {
        let _guard = registry_test_guard();
        super::clear_registry();

        // Spawn a real sleep process
        let mut child = std::process::Command::new("sleep")
            .arg("30")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("spawn sleep process");

        let pid = child.id();
        register(pid);

        // Verify the process is running
        assert!(
            child.try_wait().expect("try_wait").is_none(),
            "sleep process should be running before kill"
        );

        // Call kill_all_registered_raw which should send SIGKILL
        kill_all_registered_raw();

        // Verify the registry is cleared
        assert!(
            registered_pids().is_empty(),
            "registry should be empty after kill_all_registered_raw"
        );

        // Verify the process is dead within a bounded time
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        let mut killed = false;
        while std::time::Instant::now() < deadline {
            match child.try_wait() {
                Ok(Some(status)) => {
                    use std::os::unix::process::ExitStatusExt;
                    let was_killed = status.signal().is_some();
                    assert!(
                        was_killed,
                        "sleep process should have been killed by signal, got: {status}"
                    );
                    killed = true;
                    break;
                }
                Ok(None) => {
                    std::thread::sleep(std::time::Duration::from_millis(50));
                }
                Err(e) => {
                    panic!("try_wait failed: {e}");
                }
            }
        }

        assert!(
            killed,
            "sleep process should have been killed within 5 seconds"
        );

        // Cleanup - ensure process is reaped
        let _ = child.wait();

        super::clear_registry();
    }

    // ===================================================================
    // Pure helper function tests
    // ===================================================================

    #[test]
    #[cfg(unix)]
    fn is_process_alive_returns_false_for_nonexistent_pid() {
        // Use a PID that definitely does not exist
        let result = super::is_process_alive(u32::MAX - 1);
        assert!(!result, "nonexistent PID should return false");
    }

    #[test]
    #[cfg(unix)]
    fn is_process_alive_returns_true_for_current_process() {
        let current_pid = std::process::id();
        let result = super::is_process_alive(current_pid);
        assert!(result, "current process should be alive");
    }

    #[test]
    #[cfg(unix)]
    fn poll_until_exited_returns_immediately_for_empty_list() {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        let result = super::poll_until_exited_or_deadline(&[], deadline);
        assert!(
            result.is_empty(),
            "empty list should return immediately with empty result"
        );
    }

    #[test]
    #[cfg(unix)]
    fn poll_until_exited_returns_empty_for_nonexistent_pids() {
        // PIDs that don't exist should be identified as "not alive" immediately
        let pids = [u32::MAX - 1, u32::MAX - 2];
        let deadline = std::time::Instant::now() + std::time::Duration::from_millis(100);
        let result = super::poll_until_exited_or_deadline(&pids, deadline);
        // Nonexistent PIDs return false from is_process_alive, so they should
        // not be in the remaining set (they're already "exited")
        assert!(
            result.is_empty(),
            "nonexistent PIDs should not be in remaining set"
        );
    }

    #[test]
    #[cfg(unix)]
    fn compute_signals_for_term_produces_correct_targets() {
        // Test that compute_signals_for_term returns SIGTERM for both
        // negative PID (process group) and positive PID (individual process)
        let signals = super::compute_signals_for_term(12345);

        assert_eq!(signals.len(), 2, "should return 2 signal targets");

        // First target: negative PID (process group), SIGTERM
        assert_eq!(signals[0].0, -12345, "first target should be negative PID");
        assert_eq!(
            signals[0].1,
            libc::SIGTERM,
            "first target should use SIGTERM"
        );

        // Second target: positive PID (individual process), SIGTERM
        assert_eq!(signals[1].0, 12345, "second target should be positive PID");
        assert_eq!(
            signals[1].1,
            libc::SIGTERM,
            "second target should use SIGTERM"
        );
    }

    #[test]
    #[cfg(unix)]
    fn compute_signals_for_term_handles_large_pid() {
        // Test that u32::MAX is properly clamped to i32::MAX
        let signals = super::compute_signals_for_term(u32::MAX);

        // Both targets should be clamped to i32::MAX
        assert_eq!(signals[0].0, -i32::MAX);
        assert_eq!(signals[1].0, i32::MAX);
    }

    // ===================================================================
    // Graceful shutdown integration tests
    // ===================================================================

    #[test]
    #[cfg(unix)]
    fn kill_all_sends_sigterm_first_allowing_graceful_exit() {
        let _guard = registry_test_guard();
        super::clear_registry();

        // Spawn a process that exits cleanly on SIGTERM
        let mut child = std::process::Command::new("sleep")
            .arg("60")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("spawn sleep process");

        let pid = child.id();
        register(pid);

        kill_all_registered_raw();

        // Wait for child and check it was killed
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut exited = false;
        while std::time::Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                exited = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }

        assert!(
            exited,
            "process should have exited after kill_all_registered_raw"
        );
        assert!(registered_pids().is_empty(), "registry should be empty");
        let _ = child.wait();
        super::clear_registry();
    }

    #[test]
    #[cfg(unix)]
    fn kill_all_escalates_to_sigkill_for_sigterm_resistant_process() {
        let _guard = registry_test_guard();
        super::clear_registry();

        // Spawn a process that ignores SIGTERM
        let mut child = std::process::Command::new("bash")
            .args(["-c", "trap '' TERM; sleep 60"])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("spawn SIGTERM-resistant process");

        let pid = child.id();
        register(pid);

        // Brief sleep to let the trap handler install
        std::thread::sleep(std::time::Duration::from_millis(100));

        kill_all_registered_raw();

        // Verify the process is dead
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut killed = false;
        while std::time::Instant::now() < deadline {
            if let Ok(Some(status)) = child.try_wait() {
                use std::os::unix::process::ExitStatusExt;
                assert!(
                    status.signal().is_some(),
                    "SIGTERM-resistant process should have been killed by signal"
                );
                killed = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }

        assert!(
            killed,
            "SIGTERM-resistant process should have been killed within timeout"
        );
        let _ = child.wait();
        super::clear_registry();
    }

    #[test]
    #[cfg(unix)]
    fn kill_all_verifies_processes_actually_exited() {
        let _guard = registry_test_guard();
        super::clear_registry();

        // Spawn multiple processes
        let mut children = vec![];
        for _ in 0..3 {
            let child = std::process::Command::new("sleep")
                .arg("60")
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn()
                .expect("spawn sleep process");
            let pid = child.id();
            register(pid);
            children.push(child);
        }

        kill_all_registered_raw();

        // Verify all processes exited within a reasonable timeout
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut all_exited = false;
        while std::time::Instant::now() < deadline {
            let mut alive_count = 0;
            for child in &mut children {
                if child.try_wait().ok().flatten().is_none() {
                    alive_count += 1;
                }
            }
            if alive_count == 0 {
                all_exited = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }

        assert!(all_exited, "all spawned processes should have exited");
        assert!(registered_pids().is_empty(), "registry should be empty");

        for mut child in children {
            let _ = child.wait();
        }
        super::clear_registry();
    }
}
