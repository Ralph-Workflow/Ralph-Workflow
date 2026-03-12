//! Tests for child-process activity detection in the idle timeout monitor.
//!
//! These tests verify that the monitor uses CPU-time-based child-process
//! detection to distinguish actively working subprocesses from stalled ones.
//! Only children whose cumulative CPU time is advancing between checks
//! suppress the idle timeout.

use super::super::kill::KillConfig;
use super::super::monitor::MonitorConfig;
use super::super::*;
use crate::executor::{AgentChild, ChildProcessInfo, MockAgentChild, MockProcessExecutor};
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

/// When the agent has active child processes with advancing CPU time,
/// the monitor must not kill it.
#[test]
fn active_children_with_advancing_cpu_prevent_idle_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id(); // 12345
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    // Configure the mock executor with active children.
    let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_for(child_pid));
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    // Simulate CPU time advancing in a background thread.
    let cpu_advancer_executor = executor_impl.clone();
    let cpu_advancer_stop = Arc::clone(&should_stop);
    let cpu_advancer = thread::spawn(move || {
        let mut cpu_ms = 0u64;
        while !cpu_advancer_stop.load(Ordering::Acquire) {
            cpu_ms += 100;
            cpu_advancer_executor.set_child_cpu_time(child_pid, cpu_ms);
            thread::sleep(Duration::from_millis(2));
        }
    });

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
    // whose CPU time is advancing, it must never proceed to kill the agent.
    thread::sleep(Duration::from_millis(40));
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "no kill signals should be sent while child processes have advancing CPU time"
    );

    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");
    cpu_advancer.join().expect("cpu advancer panicked");
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "active child processes with advancing CPU should prevent idle kill"
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

    // No children configured: get_child_process_info returns NONE.
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

    // Start with active children with advancing CPU time.
    let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 1,
            cpu_time_ms: 100,
            descendant_pid_signature: 11,
        },
    ));
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(5),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: true,
    };

    // Advance CPU time so children appear active initially.
    let cpu_advancer_executor = executor_impl.clone();
    let cpu_advancer_stop = Arc::new(AtomicBool::new(false));
    let cpu_advancer_stop_clone = Arc::clone(&cpu_advancer_stop);
    let cpu_advancer = thread::spawn(move || {
        let mut cpu_ms = 100u64;
        while !cpu_advancer_stop_clone.load(Ordering::Acquire) {
            cpu_ms += 100;
            cpu_advancer_executor.set_child_cpu_time(child_pid, cpu_ms);
            thread::sleep(Duration::from_millis(2));
        }
    });

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

    // While children are present with advancing CPU, the monitor must not kill.
    thread::sleep(Duration::from_millis(30));
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "no kill should be sent while children are active"
    );

    // Stop CPU advancement and simulate the child subprocess completing.
    cpu_advancer_stop.store(true, Ordering::Release);
    cpu_advancer.join().expect("cpu advancer panicked");
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

/// Children exist but their CPU time stays constant across checks.
/// The monitor should accumulate idle count and eventually kill.
/// This is the key new behavior: mere existence of children is not enough.
#[test]
fn stalled_children_allow_idle_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    // Children exist with a fixed CPU time that never advances.
    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(MockProcessExecutor::new().with_active_children_info(
            child_pid,
            ChildProcessInfo {
                child_count: 2,
                active_child_count: 0,
                cpu_time_ms: 5000,
                descendant_pid_signature: 22,
            },
        ));

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        // Need 2 confirmations: first check grants grace (first observation),
        // second check sees unchanged CPU → idle count becomes 1,
        // third check sees unchanged CPU → idle count becomes 2 → kill.
        required_idle_confirmations: 2,
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
        "monitor should kill when children exist but CPU time is stalled"
    );
}

/// CPU time advances for a while, then stops. The monitor should kill
/// after CPU stalls for the required number of idle confirmations.
#[test]
fn children_transition_active_to_stalled_allows_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 0,
            cpu_time_ms: 0,
            descendant_pid_signature: u64::from(child_pid),
        },
    ));
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    // Advance CPU time for a short period, then stop.
    let cpu_advancer_executor = executor_impl.clone();
    let cpu_advancer = thread::spawn(move || {
        let mut cpu_ms = 0u64;
        for _ in 0..10 {
            cpu_ms += 100;
            cpu_advancer_executor.set_child_cpu_time(child_pid, cpu_ms);
            thread::sleep(Duration::from_millis(2));
        }
    });

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

    cpu_advancer.join().expect("cpu advancer panicked");
    executor_impl.add_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 0,
            cpu_time_ms: 1000,
            descendant_pid_signature: u64::from(child_pid),
        },
    );

    // After CPU stops advancing, the monitor should eventually kill.
    let result = handle.join().expect("monitor thread panicked");
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should kill after children transition from active to stalled"
    );
}

/// A child that keeps reporting itself as currently active without any new CPU
/// progress must not suppress idle timeout forever.
#[test]
fn repeated_active_snapshot_without_fresh_progress_allows_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(MockProcessExecutor::new().with_active_children_info(
            child_pid,
            ChildProcessInfo {
                child_count: 1,
                active_child_count: 1,
                cpu_time_ms: 4_200,
                descendant_pid_signature: 91,
            },
        ));

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 2,
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
        "repeated active snapshots without fresh child progress must not suppress idle timeout"
    );
}

/// Mere descendant existence must not earn startup grace.
#[test]
fn first_child_observation_without_current_activity_times_out_immediately() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    // Children with 0 CPU time (just spawned, no current activity evidence yet).
    let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 0,
            cpu_time_ms: 0,
            descendant_pid_signature: u64::from(child_pid),
        },
    ));
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(20),
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

    thread::sleep(Duration::from_millis(30));
    assert!(
        !executor_impl.execute_calls_for("kill").is_empty(),
        "timeout enforcement should start on the first idle check when descendants are not currently active"
    );
    controller.store(false, Ordering::Release);
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "non-active descendants should not delay timeout on their first observation"
    );
}

/// Reappearing children during the same idle spell must not earn a second
/// startup grace if no fresh work happened in between.
#[test]
fn children_reappearing_after_stale_gap_do_not_get_second_startup_grace() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_for(child_pid));
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(20),
        kill_config: fast_kill_config(),
        // Require 3 confirmations so the monitor observes: grace -> stalled ->
        // no-children -> reappeared stalled children.
        required_idle_confirmations: 3,
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

    // Poll 1 at ~20ms grants the startup grace. Poll 2 at ~40ms sees the same
    // stalled child and records the first idle confirmation.
    thread::sleep(Duration::from_millis(45));

    // Remove children before poll 3 so the monitor records the stale gap.
    executor_impl.remove_active_children_for(child_pid);
    thread::sleep(Duration::from_millis(25));

    // Re-add children before poll 4 without any fresh output, file activity, or
    // child CPU advancement. The monitor must continue treating the run as idle.
    executor_impl.add_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 0,
            cpu_time_ms: 0,
            descendant_pid_signature: 33,
        },
    );
    let deadline = std::time::Instant::now() + Duration::from_millis(200);
    while std::time::Instant::now() < deadline {
        if !executor_impl.execute_calls_for("kill").is_empty() {
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }
    assert!(
        !executor_impl.execute_calls_for("kill").is_empty(),
        "reappearing stalled children should not restart startup grace after the monitor already observed an idle gap"
    );

    let result = handle.join().expect("monitor thread panicked");
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should time out when children reappear without fresh work"
    );
}

/// Replacing one child subtree with another between polls is not proof of current work.
/// The replacement subtree must show CPU advancement on a later poll before it can
/// suppress timeout enforcement.
#[test]
fn replacement_child_subtree_must_advance_cpu_before_suppressing_timeout() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 0,
            cpu_time_ms: 5_000,
            descendant_pid_signature: 101,
        },
    ));
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(40),
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

    // First poll sees the original subtree and grants the initial observation grace.
    thread::sleep(Duration::from_millis(50));

    executor_impl.add_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 0,
            cpu_time_ms: 100,
            descendant_pid_signature: 202,
        },
    );

    // The replacement subtree has never shown CPU advancement, so the second poll
    // should still proceed to timeout enforcement instead of resetting idle state.
    thread::sleep(Duration::from_millis(55));
    assert!(
        !executor_impl.execute_calls_for("kill").is_empty(),
        "replacement child subtree should not suppress timeout until it shows CPU advancement"
    );

    let result = handle.join().expect("monitor thread panicked");
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should time out when replacement children never advance CPU"
    );
}

/// Active replacement descendants that keep working between polls must still
/// suppress timeout even if the descendant PID set churns.
#[test]
fn active_replacement_child_subtree_with_new_signature_still_counts_as_fresh_work() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 1,
            cpu_time_ms: 500,
            descendant_pid_signature: 101,
        },
    ));
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(25),
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

    thread::sleep(Duration::from_millis(35));
    executor_impl.add_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 1,
            cpu_time_ms: 50,
            descendant_pid_signature: 202,
        },
    );

    thread::sleep(Duration::from_millis(35));
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "freshly active replacement descendants should keep the monitor alive even when their PID signature changes"
    );

    should_stop.store(true, Ordering::Release);
    let result = handle.join().expect("monitor thread panicked");
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "active replacement descendants should continue suppressing idle timeout"
    );
}

/// When timeout fires with stalled children present, `child_status_at_timeout`
/// must be `Some` with the correct child count and CPU time.
#[test]
fn timeout_with_stalled_children_reports_child_status() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(MockProcessExecutor::new().with_active_children_info(
            child_pid,
            ChildProcessInfo {
                child_count: 3,
                active_child_count: 0,
                cpu_time_ms: 7500,
                descendant_pid_signature: 44,
            },
        ));

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 2,
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

    match result {
        MonitorResult::TimedOut {
            child_status_at_timeout: Some(info),
            ..
        } => {
            assert_eq!(info.child_count, 3);
            assert_eq!(info.cpu_time_ms, 7500);
        }
        other => panic!("expected TimedOut with child_status_at_timeout=Some, got {other:?}"),
    }
}

/// When timeout fires with no children, `child_status_at_timeout` must be `None`.
#[test]
fn timeout_without_children_reports_none_child_status() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

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

    match result {
        MonitorResult::TimedOut {
            child_status_at_timeout: None,
            ..
        } => {}
        other => panic!("expected TimedOut with child_status_at_timeout=None, got {other:?}"),
    }
}

/// When `check_child_processes` is disabled, `child_status_at_timeout` must be `None`
/// even if the executor would report children.
#[test]
fn timeout_with_check_disabled_reports_none_child_status() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(MockProcessExecutor::new().with_active_children_for(child_pid));

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop,
        &executor,
        config,
    );

    match result {
        MonitorResult::TimedOut {
            child_status_at_timeout: None,
            ..
        } => {}
        other => panic!(
            "expected TimedOut with child_status_at_timeout=None when check disabled, got {other:?}"
        ),
    }
}
