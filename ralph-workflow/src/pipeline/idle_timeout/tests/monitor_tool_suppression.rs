//! Tests for bounded tool-activity suppression in the idle timeout monitor.
//!
//! These tests verify:
//! 1. The tool suppressor cap: after `max_tool_suppression_ticks` consecutive ticks of
//!    suppression, the suppressor is bypassed and the idle timeout fires normally.
//! 2. `MonitorLoopState::reset_idle` resets the consecutive suppression tick counter,
//!    so interleaved non-tool activity properly resets the cap.

use super::super::io::KillConfig;
use super::super::runtime::base::MonitorLoopState;
use super::super::runtime::MonitorConfig;
use super::super::*;
use crate::executor::{AgentChild, MockAgentChild, MockProcessExecutor};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

fn fast_kill_config() -> KillConfig {
    KillConfig::new(
        Duration::from_millis(10),
        Duration::from_millis(1),
        Duration::from_millis(5),
        Duration::from_millis(50),
        Duration::from_millis(10),
    )
}

fn wait_until_idle_timeout_exceeded(timestamp: &SharedActivityTimestamp, timeout: Duration) {
    timestamp.store(0, Ordering::Release);
    while !is_idle_timeout_exceeded(timestamp, timeout) {
        std::thread::yield_now();
    }
}

/// Verify that a stuck tool-activity counter (check always returns true) eventually allows
/// the idle timeout to fire after `max_tool_suppression_ticks` consecutive suppressor ticks.
///
/// Without the cap, a stuck counter would suppress the timeout indefinitely.
#[test]
fn tool_suppression_cap_allows_timeout_after_max_ticks() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Tool check always returns true — simulates a stuck counter (protocol anomaly).
    let tool_activity_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(|| true);

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: None,
        tool_activity_check: Some(tool_activity_check),
        max_tool_suppression_ticks: 2,
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
        "tool suppressor cap must allow idle timeout to fire after max_tool_suppression_ticks; \
         got {result:?}"
    );
}

/// Verify that `MonitorLoopState::reset_idle` resets the consecutive tool suppression tick
/// counter to 0. This ensures that when file activity or child-process activity provides a
/// genuine idle-state reset, the cap counter also resets (the agent IS making progress).
#[test]
fn reset_idle_resets_consecutive_tool_suppression_ticks() {
    let mut s = MonitorLoopState::new();
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "new MonitorLoopState must start with consecutive_tool_suppression_ticks = 0"
    );

    s.consecutive_tool_suppression_ticks = 5;
    s.reset_idle();
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "reset_idle must reset consecutive_tool_suppression_ticks to 0"
    );
}

/// Verify that when the tool-activity check transitions from true to false, the monitor
/// does not immediately kill: with `required_idle_confirmations = 2`, one false return
/// increments the idle counter to 1 (not enough to kill). The `should_stop` signal is
/// then set and the monitor exits cleanly with `ProcessCompleted`.
///
/// Timing: check_interval = 50ms ensures the `should_stop` signal set after the 4th
/// check() call is detected during the sleep preceding the 5th tick.
#[test]
fn tool_suppressor_disabled_when_check_returns_false_allows_clean_stop() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);
    let should_stop_for_test = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Tool check returns true for the first 3 calls (ticks 1–3 suppressed), then false.
    let check_count = Arc::new(std::sync::atomic::AtomicU32::new(0));
    let check_count_clone = Arc::clone(&check_count);
    let tool_activity_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(move || {
        let n = check_count_clone.fetch_add(1, Ordering::SeqCst);
        n < 3
    });

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        // 50ms interval: the other thread sets should_stop after tick 4's check(),
        // and the monitor detects it during tick 5's 50ms sleep → StopConditionsMet.
        check_interval: Duration::from_millis(50),
        kill_config: fast_kill_config(),
        // 2 confirmations: tick 4 (check=false) only increments to 1, so no kill yet.
        required_idle_confirmations: 2,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: None,
        tool_activity_check: Some(tool_activity_check),
        max_tool_suppression_ticks: 10, // cap is high — suppressor disabled by check returning false
    };

    let handle = std::thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            None,
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    // Wait for tick 4 to have called check() (check_count ≥ 4), then set should_stop.
    // The monitor's tick 5 sleep will detect the signal and return ProcessCompleted.
    while check_count.load(Ordering::SeqCst) < 4 {
        std::thread::yield_now();
    }
    should_stop_for_test.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "monitor must not kill when should_stop is set after tool check returns false"
    );
}
