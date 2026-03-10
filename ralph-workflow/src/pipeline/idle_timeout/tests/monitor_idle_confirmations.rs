//! Tests for consecutive idle confirmation behavior in the idle timeout monitor.
//!
//! These tests verify that the monitor requires multiple consecutive idle
//! observations before killing a process, preventing false kills during
//! transient quiet periods (LLM API waits, slow compilations, etc.).

use super::super::kill::{KillConfig, DEFAULT_KILL_CONFIG};
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

#[test]
fn monitor_required_idle_confirmations_defaults_to_two() {
    assert_eq!(
        MonitorConfig::default().required_idle_confirmations,
        2,
        "required_idle_confirmations should default to 2 to prevent false kills on transient idle"
    );
}

#[test]
fn monitor_one_confirmation_required_kills_on_first_idle_check() {
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
        "monitor should kill after a single idle check when required_idle_confirmations=1"
    );
}

#[test]
fn monitor_two_consecutive_idle_checks_kill_when_two_confirmations_required() {
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
        required_idle_confirmations: 2,
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
        "monitor should kill after two consecutive idle checks when required_idle_confirmations=2"
    );
}

#[test]
fn monitor_single_idle_check_does_not_kill_when_two_confirmations_required() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    // Use new_running so that reaching the kill path sends a real kill command
    // (observable via executor calls), making the test non-vacuous.
    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor_impl = Arc::new(MockProcessExecutor::new());
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    // Use a non-zero check_interval so the monitor sleeps between confirmations,
    // giving the test a window to signal stop after the first confirmation but
    // before the second one triggers a kill.
    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(50),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 2,
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

    // The monitor sleeps check_interval (50ms) before each idle check.
    // After ~50ms it performs the first confirmation (count → 1) and then
    // begins sleeping for the second check_interval. We signal stop at ~60ms,
    // which falls inside the second sleep window. The monitor detects should_stop
    // before accumulating a second confirmation, returning ProcessCompleted.
    thread::sleep(Duration::from_millis(60));
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");

    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "monitor must not kill after a single idle check when two confirmations are required"
    );
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "no kill signals should be sent after only one idle confirmation"
    );
}

#[test]
fn monitor_activity_between_checks_resets_idle_confirmation_count() {
    // Use a non-zero timeout so we can reset the idle state by touching the timestamp.
    let timeout = Duration::from_millis(100);
    let timestamp = new_activity_timestamp();
    // Make the monitor see idle immediately by clearing the timestamp.
    wait_until_idle_timeout_exceeded(&timestamp, timeout);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor_impl = Arc::new(MockProcessExecutor::new());
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout,
        check_interval: Duration::from_millis(20),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 2,
    };

    let timestamp_for_touch = timestamp.clone();
    let handle = thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp_for_touch,
            None,
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    // After check_interval (20ms), the monitor performs its first idle check:
    // count → 1. It then sleeps another check_interval. At 25ms we touch the
    // activity timestamp. The next check sees NOT idle (elapsed < timeout),
    // which resets count to 0. The process is never killed.
    thread::sleep(Duration::from_millis(25));
    touch_activity(&timestamp);

    // Stop the monitor before elapsed time can exceed timeout again and
    // accumulate two new idle confirmations.
    thread::sleep(Duration::from_millis(40));
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");

    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "output activity between idle checks must reset the idle confirmation count"
    );
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "no kill signals when activity resets the confirmation count"
    );
}

#[test]
fn monitor_file_activity_resets_idle_confirmation_count() {
    use crate::workspace::MemoryWorkspace;

    let timeout = Duration::from_millis(50);
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, timeout);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    // Fresh file (mtime ≈ now) within the timeout window. File activity resets
    // the idle confirmation count each time it is detected, so the monitor
    // never accumulates enough consecutive idle observations to kill.
    let workspace: Arc<dyn crate::workspace::Workspace> =
        Arc::new(MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Progress"));

    let file_activity_config = Some(FileActivityConfig {
        tracker: new_file_activity_tracker(),
        workspace: Arc::clone(&workspace),
    });

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor_impl = Arc::new(MockProcessExecutor::new());
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout,
        check_interval: Duration::ZERO,
        kill_config: DEFAULT_KILL_CONFIG,
        required_idle_confirmations: 2,
    };

    let handle = thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            file_activity_config.as_ref(),
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    // Give the monitor time to perform several file-activity checks. File
    // activity detection resets the idle confirmation count on each pass,
    // so the monitor never accumulates enough confirmations to kill.
    thread::sleep(Duration::from_millis(20));
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");

    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "file activity between idle checks must reset the idle confirmation count"
    );
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "no kill signals when file activity resets the confirmation count"
    );
}
