//! Tests for child-process activity detection in the idle timeout monitor.
//!
//! These tests verify that the monitor uses CPU-time-based child-process
//! detection to distinguish actively working subprocesses from stalled ones.
//! Only children whose cumulative CPU time is advancing between checks
//! suppress the idle timeout.

use super::super::io::KillConfig;
use super::super::runtime::MonitorConfig;
use super::super::*;
use crate::executor::{AgentChild, ChildProcessInfo, MockAgentChild, MockProcessExecutor};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

fn wait_until_idle_timeout_exceeded(timestamp: &SharedActivityTimestamp, timeout: Duration) {
    // Set timestamp to just barely exceed the timeout.
    // With timestamp = now - (timeout + 1ms), time_since_activity = timeout + 1ms,
    // which is > timeout, so is_idle_timeout_exceeded returns true.
    // This is better than timestamp=0 which makes time_since_activity = 56 years,
    // breaking activity_resumed_after_file_scan logic.
    let offset = timeout + Duration::from_millis(1);
    let now_ms = SystemTime::UNIX_EPOCH
        .elapsed()
        .unwrap_or_default()
        .as_millis() as u64;
    let past_ms = now_ms.saturating_sub(offset.as_millis() as u64);
    timestamp.store(past_ms, Ordering::Release);
    // Verify idle is now exceeded
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

fn wait_until(timeout: Duration, mut predicate: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if predicate() {
            return true;
        }
        thread::sleep(Duration::from_millis(1));
    }
    predicate()
}

#[test]
fn active_children_with_advancing_cpu_prevent_idle_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    // Auto-advance cpu by 100ms on every query — no background thread needed.
    let executor_impl = Arc::new(
        MockProcessExecutor::new()
            .with_active_children_for(child_pid)
            .with_cpu_step_per_query(child_pid, 100),
    );
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(5),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 3,
        check_child_processes: true,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

    assert!(
        wait_until(Duration::from_millis(200), || {
            executor_impl.child_info_query_count_for(child_pid) >= 3
        }),
        "monitor should have queried children at least 3 times with cpu advancing on each query"
    );
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "no kill signals should be sent while child processes have advancing CPU time"
    );

    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "active child processes with advancing CPU should prevent idle kill"
    );
}

#[test]
fn no_active_children_allows_idle_kill() {
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
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

#[test]
fn child_process_check_disabled_does_not_prevent_kill() {
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
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

#[test]
fn child_processes_that_finish_eventually_allow_kill() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    // Auto-advance cpu by 100ms on every query — no background thread needed.
    let executor_impl = Arc::new(
        MockProcessExecutor::new()
            .with_active_children_info(
                child_pid,
                ChildProcessInfo {
                    child_count: 1,
                    active_child_count: 1,
                    cpu_time_ms: 100,
                    descendant_pid_signature: 11,
                },
            )
            .with_cpu_step_per_query(child_pid, 100),
    );
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let cpu_advancer_executor = executor_impl.clone();
    let cpu_advancer_stop = Arc::new(AtomicBool::new(false));
    let cpu_advancer_stop_clone = Arc::clone(&cpu_advancer_stop);
    let cpu_advancer = thread::spawn(move || {
        let mut cpu_ms = 100u64;
        while !cpu_advancer_stop_clone.load(Ordering::Acquire) {
            cpu_ms += 100;
            cpu_advancer_executor.set_child_cpu_time(child_pid, cpu_ms);
            thread::sleep(Duration::from_millis(1));
        }
    });

    // required_idle_confirmations=5 requires five consecutive 50ms checks (250ms gap)
    // with no CPU progress before a kill fires. The cpu_advancer runs every 1ms, so
    // fresh progress is always detected within 50ms while the advancer is running —
    // even on heavily loaded CI machines where thread scheduling may be delayed.
    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(50),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 5,
        check_child_processes: true,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

    thread::sleep(Duration::from_millis(500));
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "no kill should be sent while children are active"
    );

    cpu_advancer_stop.store(true, Ordering::Release);
    cpu_advancer.join().expect("cpu advancer panicked");

    executor_impl.remove_active_children_for(child_pid);

    let result = handle.join().expect("monitor thread panicked");
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should kill after child processes finish"
    );
}

#[test]
fn monitor_config_defaults_check_child_processes_to_true() {
    assert!(
        MonitorConfig::default().check_child_processes,
        "check_child_processes should default to true to prevent false kills from subprocesses"
    );
}

#[test]
fn stalled_children_allow_idle_kill() {
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
        required_idle_confirmations: 2,
        check_child_processes: true,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

    let result = handle.join().expect("monitor thread panicked");
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should kill after children transition from active to stalled"
    );
}

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
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

#[test]
fn first_child_observation_without_current_activity_times_out_immediately() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, controller) = MockAgentChild::new_running(0);
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

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(20),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: true,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

    // Wait long enough for: 20ms check_interval sleep + ~15ms kill_process round-trip.
    // Use 200ms to allow ample margin on loaded CI machines.
    thread::sleep(Duration::from_millis(200));
    assert!(
        !executor_impl.execute_calls_for("kill").is_empty(),
        "timeout enforcement should start on the first idle check when descendants are not currently active"
    );
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");

    // Mark child as exited AFTER monitor has completed its enforcement action.
    // This ensures try_wait_child sees the child's state at the time of enforcement,
    // not a subsequent state change that would incorrectly produce ProcessCompleted.
    controller.store(false, Ordering::Release);

    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "non-active descendants should not delay timeout on their first observation"
    );
}

#[test]
fn active_children_after_child_free_gap_get_startup_grace_again() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, controller) = MockAgentChild::new_running(0);
    let child_pid = mock_child.id();
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

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
        required_idle_confirmations: 3,
        check_child_processes: true,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

    assert!(
        wait_until(Duration::from_millis(200), || {
            executor_impl.child_info_query_count_for(child_pid) >= 1
        }),
        "monitor should observe the initial active child"
    );

    executor_impl.remove_active_children_for(child_pid);

    assert!(
        wait_until(Duration::from_millis(200), || {
            executor_impl.child_info_query_count_for(child_pid) >= 2
        }),
        "monitor should observe the child-free gap before children reappear"
    );

    executor_impl.add_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 1,
            cpu_time_ms: 0,
            descendant_pid_signature: 22,
        },
    );

    assert!(
        wait_until(Duration::from_millis(200), || {
            executor_impl.child_info_query_count_for(child_pid) >= 3
                || !executor_impl.execute_calls_for("kill").is_empty()
        }),
        "monitor should observe the reappeared child or attempt a kill"
    );
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "a newly active child after a child-free gap should get a fresh startup grace instead of timing out immediately"
    );

    assert!(
        wait_until(Duration::from_millis(200), || {
            !executor_impl.execute_calls_for("kill").is_empty()
        }),
        "after the replacement grace poll expires without fresh progress, timeout enforcement should resume"
    );
    controller.store(false, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should time out after the replacement child also goes stale"
    );
}

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
        check_interval: Duration::from_millis(20),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 2,
        check_child_processes: true,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

    assert!(
        wait_until(Duration::from_millis(200), || {
            executor_impl.child_info_query_count_for(child_pid) >= 1
        }),
        "monitor should record the initial stalled child observation"
    );
    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "one stalled observation should only consume the first idle confirmation"
    );

    executor_impl.add_active_children_info(
        child_pid,
        ChildProcessInfo {
            child_count: 1,
            active_child_count: 0,
            cpu_time_ms: 100,
            descendant_pid_signature: 202,
        },
    );

    assert!(
        wait_until(Duration::from_millis(200), || {
            !executor_impl.execute_calls_for("kill").is_empty()
        }),
        "replacement child subtree should reach timeout enforcement on the next idle check"
    );
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
        check_interval: Duration::from_millis(50),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: true,
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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

    // Update the executor after the first tick grants startup grace (at ~0ms)
    // but before the second tick (at ~50ms) checks for fresh progress.
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

    thread::sleep(Duration::from_millis(50));
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
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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
        completion_check: None,

        partial_completion_check: None,
        tool_activity_check: None,
        max_tool_suppression_ticks: 20,
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
