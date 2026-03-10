//! Tests for child-process activity detection in the idle timeout monitor.
//!
//! These tests verify that the monitor uses the `check_child_processes` flag
//! to avoid false-positive kills when the agent has spawned subprocesses
//! (e.g. `cargo test`, `cargo build`, `npm install`) that are still running
//! even though the agent produces no stdout/stderr output.

use super::super::kill::KillConfig;
use super::super::monitor::MonitorConfig;
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

/// A fast kill config for unit tests so tests don't hang waiting for grace periods.
fn fast_kill_config() -> KillConfig {
    KillConfig::new(
        Duration::from_millis(10),
        Duration::from_millis(1),
        Duration::from_millis(5),
        Duration::from_millis(50),
        Duration::from_millis(10),
    )
}

/// When the agent has active child processes, the monitor must not kill it.
///
/// If the agent has spawned a long-running subprocess (e.g. `cargo test`),
/// it may produce no stdout/stderr while that subprocess runs. The monitor
/// should detect active children and refrain from killing the agent.
#[test]
fn active_children_prevent_idle_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id(); // 12345
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    // Configure the mock executor so has_active_child_processes returns true for our PID.
    let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_for(child_pid));
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(5),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: true,
    };

    let handle = thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            None,
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    // Give the monitor time to perform several idle checks. With active children
    // reported, it must never proceed to kill the agent.
    thread::sleep(Duration::from_millis(40));
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "no kill signals should be sent while active child processes are present"
    );

    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "active child processes should prevent idle kill"
    );
}

/// When there are no active child processes and output is idle, the monitor must kill.
#[test]
fn no_active_children_allows_idle_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    // No children configured: has_active_child_processes returns false.
    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: true,
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop,
        &executor,
        config,
    );

    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should kill when there are no active child processes and output is idle"
    );
}

/// When `check_child_processes` is `false`, the child-process check is skipped and
/// the monitor kills even when the executor would report active children.
#[test]
fn child_process_check_disabled_does_not_prevent_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    // Children are present in the mock, but the check is disabled.
    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(MockProcessExecutor::new().with_active_children_for(child_pid));

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false, // disabled
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop,
        &executor,
        config,
    );

    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should kill even when children are present if check_child_processes is false"
    );
}

/// When child processes exist initially but then finish, the monitor should
/// eventually declare the agent idle and kill it.
#[test]
fn child_processes_that_finish_eventually_allow_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id(); // 12345
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    // Start with active children.
    let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_for(child_pid));
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(5),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: true,
    };

    let handle = thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            None,
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    // While children are present the monitor must not kill.
    thread::sleep(Duration::from_millis(30));
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "no kill should be sent while children are active"
    );

    // Simulate the child subprocess completing.
    executor_impl.remove_active_children_for(child_pid);

    // Now the monitor should detect no children and proceed with timeout enforcement.
    let result = handle.join().expect("monitor thread panicked");
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should kill after child processes finish"
    );
}

/// `MonitorConfig::default()` must have `check_child_processes` set to `true`
/// so the guard is active in production usage.
#[test]
fn monitor_config_defaults_check_child_processes_to_true() {
    assert!(
        MonitorConfig::default().check_child_processes,
        "check_child_processes should default to true to prevent false kills from subprocesses"
    );
}
