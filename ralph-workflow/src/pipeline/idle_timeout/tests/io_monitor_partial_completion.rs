//! Tests for partial_completion_check suppressor behavior (Bug 3).
//!
//! Verifies that when `partial_completion_check` returns `true` (output file exists,
//! even if incomplete), the idle timeout is suppressed — the agent is actively
//! writing output. When it returns `false` (file absent), the timeout fires normally.
//!
//! ## Bug 3: Tool Activity Suppression
//!
//! The idle timeout must also be suppressed when the agent has an active tool
//! execution in progress (e.g., a `write` tool call to create the output file).
//! This is distinct from partial_completion_check which only checks file existence.
//!
//! A running tool should suppress idle timeout even if:
//! - The output file doesn't exist yet (tool is still writing)
//! - No fresh stdout/stderr has been produced
//!
//! This requires a `tool_activity_check` suppressor that tracks parser-observable
//! tool lifecycle events (tool-start, tool-running, tool-finish, etc.).

use super::super::io::KillConfig;
use super::super::runtime::MonitorConfig;
use super::super::*;
use crate::executor::{AgentChild, MockAgentChild, MockProcessExecutor};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

fn wait_until_idle_timeout_exceeded(timestamp: &SharedActivityTimestamp, timeout: Duration) {
    timestamp.store(0, Ordering::Release);
    while !is_idle_timeout_exceeded(timestamp, timeout) {
        std::thread::yield_now();
    }
}

fn fast_kill_config() -> KillConfig {
    KillConfig::new(
        Duration::from_millis(10),
        Duration::from_millis(1),
        Duration::from_millis(5),
        Duration::from_millis(50),
        Duration::from_millis(10),
    )
}

/// When `partial_completion_check` returns `true` (file exists), the idle timeout
/// is suppressed and the process eventually completes normally.
///
/// This simulates the scenario where the agent is actively writing its output file —
/// the idle timeout must NOT kill the process during the write.
#[test]
fn partial_completion_check_true_suppresses_idle_timeout() {
    // Idle timeout fires immediately; process exits after 500ms.
    // partial_completion_check returns true (file exists) → idle suppressed.
    // Expected: ProcessCompleted (not TimedOut).

    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, child_running) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // File exists → partial completion check returns true → suppress idle timeout
    let partial_completion_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(|| true);

    // Spawn a thread to stop the child after a short delay (simulating finished write)
    let child_handle = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(200));
        child_running.store(false, Ordering::Release);
    });

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(50),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 2,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: Some(partial_completion_check),
        tool_activity_check: None,
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop_clone,
        &executor,
        config,
    );

    child_handle.join().expect("child thread should not panic");

    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "partial_completion_check=true must suppress idle timeout; agent should complete normally"
    );
}

/// When `partial_completion_check` returns `false` (file absent), the idle timeout
/// fires normally and the process is killed.
#[test]
fn partial_completion_check_false_allows_idle_timeout_to_fire() {
    // Idle timeout fires immediately; process never exits on its own.
    // partial_completion_check returns false (file absent) → timeout fires.
    // Expected: TimedOut.

    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // File absent → partial completion check returns false → timeout fires
    let partial_completion_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(|| false);

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(5),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: Some(partial_completion_check),
        tool_activity_check: None,
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop_clone,
        &executor,
        config,
    );

    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "partial_completion_check=false must not suppress timeout; expected TimedOut, got: {result:?}"
    );
}

/// Bug 3 regression: When a tool is actively running (e.g., write tool producing output),
/// the idle timeout must be suppressed even if no output file exists yet.
///
/// This tests the `tool_activity_check` suppressor - when it returns true,
/// the idle counter should reset and the timeout should not fire.
///
/// Currently, `partial_completion_check` only checks file existence, which misses
/// the case where a tool is mid-write and the file hasn't been created yet.
#[test]
fn tool_activity_check_suppresses_idle_timeout_even_without_file() {
    // Idle timeout fires immediately; tool_activity_check returns true → timeout suppressed.
    // partial_completion_check returns false (file doesn't exist yet).
    // Child exits after a short delay → expected ProcessCompleted.

    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, child_running) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // File doesn't exist yet (tool is still writing)
    let partial_completion_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(|| false);

    // Tool IS actively running (write tool in progress)
    let tool_activity_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(|| true);

    // Spawn a thread to stop the child after a short delay (simulating tool finished)
    let child_handle = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(200));
        child_running.store(false, Ordering::Release);
    });

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(5),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: Some(partial_completion_check),
        tool_activity_check: Some(tool_activity_check),
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop_clone,
        &executor,
        config,
    );

    child_handle.join().expect("child thread should not panic");

    assert!(
        matches!(result, MonitorResult::ProcessCompleted),
        "tool_activity_check=true must suppress idle timeout even without file; expected ProcessCompleted, got: {result:?}"
    );
}

/// Bug 3 regression: When tool activity check stops returning true (tool finished)
/// AND no output file exists, the idle timeout must fire normally.
///
/// This verifies that tool activity suppression is transient — once the tool finishes
/// and no progress is made, the timeout enforcement resumes.
#[test]
fn tool_activity_check_clears_and_timeout_fires_after_tool_finishes() {
    // Idle timeout fires immediately; process never exits on its own.
    // tool_activity_check returns true initially, then false (tool finished).
    // partial_completion_check returns false (file was never created).
    // Expected: TimedOut after tool activity stops.

    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Track whether tool is still running
    let tool_still_running = Arc::new(AtomicBool::new(true));
    let tool_still_running_for_check = Arc::clone(&tool_still_running);

    // File doesn't exist
    let partial_completion_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(|| false);

    // Tool activity check returns true initially, then false (tool finished)
    let tool_activity_check: Arc<dyn Fn() -> bool + Send + Sync> =
        Arc::new(move || tool_still_running_for_check.load(Ordering::Acquire));

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(5),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: Some(partial_completion_check),
        tool_activity_check: Some(tool_activity_check),
    };

    let handle = std::thread::spawn(move || {
        // Simulate tool finishing after some time
        std::thread::sleep(Duration::from_millis(20));
        tool_still_running.store(false, Ordering::Release);

        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            None,
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    let result = handle.join().expect("monitor thread should not panic");

    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "after tool finishes and file absent, timeout must fire; expected TimedOut, got: {result:?}"
    );
}
