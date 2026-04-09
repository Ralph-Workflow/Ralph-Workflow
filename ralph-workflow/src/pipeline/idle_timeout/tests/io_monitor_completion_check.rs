//! Tests for completion check behavior in the idle timeout monitor.
//!
//! These tests verify that the monitor correctly handles the completion_check
//! callback to distinguish between a clean exit (output ready) and a genuine timeout.

use super::super::io::KillConfig;
use super::super::runtime::MonitorConfig;
use super::super::*;
use crate::executor::{AgentChild, MockAgentChild, MockProcessExecutor};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
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

#[test]
fn clean_exit_with_valid_output_returns_complete_but_waiting() {
    // Scenario: process still alive, completion_check returns true, idle exceeded.
    // Expected: CompleteButWaiting (not TimedOut).
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // completion_check returns true to indicate output is ready
    let completion_check = Arc::new(|| true);

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: Some(completion_check),

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop_clone,
        &executor,
        config,
    );

    assert_eq!(
        result,
        MonitorResult::CompleteButWaiting,
        "completion_check=true should return CompleteButWaiting even when idle exceeded"
    );
}

#[test]
fn proactive_completion_advances_before_idle_timeout() {
    // Scenario: completion_check returns true immediately, child still alive.
    // The CompletionReady tick policy should fire proactively (not wait for idle timeout).
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // completion_check returns true
    let completion_check = Arc::new(|| true);

    let config = MonitorConfig {
        timeout: Duration::from_secs(300), // long timeout
        check_interval: Duration::from_millis(50),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: Some(completion_check),

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop_clone,
        &executor,
        config,
    );

    assert_eq!(
        result,
        MonitorResult::CompleteButWaiting,
        "completion_check=true should return CompleteButWaiting proactively, not wait for idle timeout"
    );
}

#[test]
fn clean_exit_no_output_returns_process_completed() {
    // Scenario: child exits cleanly (exit code 0), completion_check is None.
    // Expected: ProcessCompleted (not TimedOut).
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, child_running) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Spawn a thread that will stop the child after a short delay
    let child_handle = thread::spawn(move || {
        thread::sleep(Duration::from_millis(10));
        child_running.store(false, Ordering::Release);
    });

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(20),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 2,
        check_child_processes: false,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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
        "child exit with no completion_check should return ProcessCompleted"
    );
}

#[test]
fn enforcement_exited_with_output_returns_complete_but_waiting() {
    // Scenario: child exits DURING enforcement window, completion_check returns true.
    // Expected: CompleteButWaiting (was wrongly returning TimedOut before fix).
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, child_running) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Let child exit during enforcement (required_idle_confirmations = 1 means
    // enforcement starts immediately on first idle check)
    let child_handle = thread::spawn(move || {
        thread::sleep(Duration::from_millis(15));
        child_running.store(false, Ordering::Release);
    });

    // completion_check returns true
    let completion_check = Arc::new(|| true);

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(20),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: Some(completion_check),

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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
        MonitorResult::CompleteButWaiting,
        "exiting during enforcement with completion_check=true should return CompleteButWaiting"
    );
}

#[test]
fn enforcement_exited_without_output_returns_process_completed() {
    // Scenario: child exits before enforcement begins, completion_check is None.
    // The monitor's pre-kill try_wait check sees the child is already dead and
    // returns ProcessCompleted without issuing a kill signal.
    // Expected: ProcessCompleted (not TimedOut).
    //
    // Note: the child is marked dead synchronously before the monitor starts to
    // avoid a timing race where a background thread might not exit within the
    // monitor's first check_interval under high test parallelism.
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, child_running) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Child is already dead when the monitor begins enforcement — deterministic.
    child_running.store(false, Ordering::Release);

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(20),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop_clone,
        &executor,
        config,
    );

    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "child already exited before enforcement must return ProcessCompleted"
    );
}

#[test]
fn enforcement_exited_during_enforcement_returns_timed_out() {
    // Scenario: child exits DURING enforcement (after SIGTERM was sent), no completion_check.
    // Contract: once enforcement has been triggered (kill sent), the outcome is always
    // TimedOut regardless of whether escalation was needed. The child did not exit
    // voluntarily — it was killed.
    // Expected: TimedOut (not ProcessCompleted).
    //
    // Timing design:
    //   - check_interval=10ms: the monitor sleeps 10ms then fires the idle timeout
    //   - at ~10ms: idle timeout fires, child is alive, SIGTERM "sent" (mock), enforcement
    //     state created (s.timeout_triggered = Some(...))
    //   - at ~30ms: child thread sets child_running=false, simulating death from the kill
    //   - enforcement loop spins without sleeping until try_wait_child returns true
    //   - result_on_enforcement_exit must return TimedOut (not ProcessCompleted)
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, child_running) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Child exits well AFTER check_interval so the idle timeout fires while the
    // child is still alive. The monitor sends SIGTERM (mock) and enters enforcement
    // phase; this thread then simulates the process dying from the kill.
    let child_handle = thread::spawn(move || {
        thread::sleep(Duration::from_millis(30));
        child_running.store(false, Ordering::Release);
    });

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(10),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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
        matches!(result, MonitorResult::TimedOut { .. }),
        "child killed by SIGTERM during enforcement must return TimedOut, got {result:?}"
    );
}

#[test]
fn idle_timeout_while_alive_no_output_returns_timed_out() {
    // Scenario: child still alive, no completion_check, idle exceeded.
    // Expected: TimedOut (existing behavior preserved).
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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
        "child alive, no completion_check, idle exceeded should return TimedOut"
    );
}

#[test]
fn idle_timeout_does_not_fire_after_process_exited_cleanly() {
    // Scenario: should_stop is set after try_wait detects exit.
    // Monitor must return ProcessCompleted (not TimedOut).
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));

    let (mock_child, child_running) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Child exits quickly
    let child_handle = thread::spawn(move || {
        thread::sleep(Duration::from_millis(10));
        child_running.store(false, Ordering::Release);
    });

    // Spawn monitor thread
    let should_stop_clone = Arc::clone(&should_stop);
    let handle = thread::spawn(move || {
        let config = MonitorConfig {
            timeout: Duration::ZERO,
            check_interval: Duration::from_millis(50),
            kill_config: fast_kill_config(),
            required_idle_confirmations: 2,
            check_child_processes: false,
            completion_check: None,

            partial_completion_check: None,
            tool_activity_check: None,
            max_tool_suppression_ticks: 20,
        };

        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            None,
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    // Wait for child to exit and set should_stop
    child_handle.join().expect("child thread should not panic");
    thread::sleep(Duration::from_millis(20));
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread should not panic");

    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "should_stop after child exit should return ProcessCompleted"
    );
}

#[test]
fn empty_output_file_does_not_trigger_completion_ready() {
    // Scenario: completion_check returns false (empty/zero-byte file).
    // Expected: should NOT return CompleteButWaiting.
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // completion_check returns false (simulates empty or invalid output file)
    let completion_check = Arc::new(|| false);

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: Some(completion_check),

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop_clone,
        &executor,
        config,
    );

    // With completion_check returning false, we should get TimedOut, not CompleteButWaiting
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "completion_check returning false should result in TimedOut, not CompleteButWaiting"
    );
}
