//! Tests for monitor integration with file activity detection.

use super::super::monitor::MonitorConfig;
use super::super::*;
use crate::executor::{AgentChild, MockAgentChild, MockProcessExecutor};
use crate::workspace::MemoryWorkspace;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime};

#[test]
fn monitor_prevents_timeout_with_file_activity() {
    // Setup: Process with no stdout output but files being written within recency window
    let timestamp = new_activity_timestamp();
    // Prevent output-activity from interfering: set timestamp to epoch so it's always stale
    timestamp.store(0, Ordering::Release);
    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    // Create workspace with a recently modified file (within the 500ms window)
    let workspace = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Progress");
    let workspace_arc: Arc<dyn crate::workspace::Workspace> = Arc::new(workspace);

    let file_activity_config = Some(FileActivityConfig {
        tracker: new_file_activity_tracker(),
        workspace: Arc::clone(&workspace_arc),
    });

    let mock_child = MockAgentChild::new(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Use a 500ms timeout with fast check interval so test runs quickly
    let config = MonitorConfig {
        timeout: Duration::from_millis(500),
        check_interval: Duration::from_millis(10),
        kill_config: DEFAULT_KILL_CONFIG,
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

    // Wait 20ms — the file is fresh (< 500ms old), so timeout should not trigger;
    // 20ms gives at least 2 check cycles at check_interval=10ms
    thread::sleep(Duration::from_millis(20));

    // Signal monitor to stop
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");

    // Should complete normally because file activity prevented timeout
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "Monitor should not timeout when files are within recency window"
    );
}

#[test]
fn monitor_times_out_without_any_activity() {
    // Setup: Process with no output and no file changes
    let timestamp = new_activity_timestamp();
    // Ensure timestamp is in the past so output-activity timeout is immediately exceeded
    timestamp.store(0, Ordering::Release);
    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    // Create workspace with no AI-generated files (only logs)
    let workspace = MemoryWorkspace::new_test().with_file(".agent/pipeline.log", "logs");
    let workspace_arc: Arc<dyn crate::workspace::Workspace> = Arc::new(workspace);

    let file_activity_config = Some(FileActivityConfig {
        tracker: new_file_activity_tracker(),
        workspace: Arc::clone(&workspace_arc),
    });

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Zero timeout: always exceeded immediately
    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(5),
        kill_config: DEFAULT_KILL_CONFIG,
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

    let result = handle.join().expect("Monitor thread panicked");

    // Should timeout because no activity (stdout or files)
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "Monitor should timeout when there's no activity"
    );
}

#[test]
fn monitor_respects_output_activity() {
    // Setup: Process with stdout activity (existing behavior)
    let timestamp = new_activity_timestamp();
    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    // No file activity config
    let file_activity_config = None;

    let mock_child = MockAgentChild::new(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // 200ms timeout with fast check interval
    let config = MonitorConfig {
        timeout: Duration::from_millis(200),
        check_interval: Duration::from_millis(10),
        kill_config: DEFAULT_KILL_CONFIG,
    };

    // Update activity timestamp periodically to simulate stdout (6 updates at 20ms = 120ms total)
    let timestamp_clone = timestamp.clone();
    let update_handle = thread::spawn(move || {
        for _ in 0..6 {
            thread::sleep(Duration::from_millis(20));
            touch_activity(&timestamp_clone);
        }
    });

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

    // Wait 40ms then stop — the update loop runs at 20ms intervals, and the timeout is 200ms;
    // 40ms is sufficient to confirm the monitor sees recent activity
    thread::sleep(Duration::from_millis(40));
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");
    update_handle.join().expect("Update thread panicked");

    // Should complete normally because stdout activity prevented timeout
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "Monitor should not timeout when stdout activity is present"
    );
}

#[test]
fn monitor_uses_configurable_check_interval() {
    // Setup: Verify that custom check intervals are respected
    let timestamp = new_activity_timestamp();
    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let mock_child = MockAgentChild::new(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Use a custom check interval (30 seconds as per spec)
    let config = MonitorConfig {
        timeout: Duration::from_secs(60),
        check_interval: Duration::from_secs(30),
        kill_config: DEFAULT_KILL_CONFIG,
    };

    let handle = thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            None, // No file activity
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    // Signal stop quickly — 20ms is sufficient to start the monitor
    thread::sleep(Duration::from_millis(20));
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");

    // Should complete normally
    assert_eq!(result, MonitorResult::ProcessCompleted);
}

#[test]
fn monitor_file_activity_with_old_files_times_out() {
    // Setup: Files exist but are old (>timeout window)
    let timestamp = new_activity_timestamp();
    // Ensure output-activity timeout is also exceeded
    timestamp.store(0, Ordering::Release);
    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    // Create workspace with old file (400 seconds ago, beyond 300s timeout)
    let old_time = SystemTime::now() - Duration::from_secs(400);
    let workspace =
        MemoryWorkspace::new_test().with_file_at_time(".agent/PLAN.md", "old", old_time);
    let workspace_arc: Arc<dyn crate::workspace::Workspace> = Arc::new(workspace);

    let file_activity_config = Some(FileActivityConfig {
        tracker: new_file_activity_tracker(),
        workspace: Arc::clone(&workspace_arc),
    });

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Zero timeout: output activity is immediately exceeded; file is 400s old vs Duration::ZERO
    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(5),
        kill_config: DEFAULT_KILL_CONFIG,
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

    let result = handle.join().expect("Monitor thread panicked");

    // Should timeout because file is too old
    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "Monitor should timeout when files are too old"
    );
}

#[test]
fn monitor_does_not_timeout_on_file_activity_check_error() {
    // Setup: .agent exists as a file (not directory), causing read_dir(.agent) to error.
    // The monitor should treat this as indeterminate activity and skip timeout kill for
    // that cycle, rather than failing closed.
    let timestamp = new_activity_timestamp();
    timestamp.store(0, Ordering::Release);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    // .agent path intentionally created as a FILE so read_dir(".agent") errors.
    let workspace = MemoryWorkspace::new_test().with_file(".agent", "not a directory");
    let workspace_arc: Arc<dyn crate::workspace::Workspace> = Arc::new(workspace);

    let file_activity_config = Some(FileActivityConfig {
        tracker: new_file_activity_tracker(),
        workspace: Arc::clone(&workspace_arc),
    });

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor_impl = Arc::new(MockProcessExecutor::new());
    let executor_dyn: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::from_millis(10),
        kill_config: DEFAULT_KILL_CONFIG,
    };

    let handle = thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            file_activity_config.as_ref(),
            &child,
            &should_stop_clone,
            &executor_dyn,
            config,
        )
    });

    // Give the monitor time to attempt at least one idle check cycle.
    thread::sleep(Duration::from_millis(100));
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");

    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "monitor should not force timeout when file activity check errors"
    );

    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "monitor should not send kill signals when file activity check errors"
    );
}

#[test]
fn monitor_without_file_activity_config_works() {
    // Ensure backward compatibility: monitor works without file activity config
    let timestamp = new_activity_timestamp();
    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let mock_child = MockAgentChild::new(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    let config = MonitorConfig {
        timeout: Duration::from_secs(60),
        check_interval: Duration::from_millis(10),
        kill_config: DEFAULT_KILL_CONFIG,
    };

    let handle = thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            None, // No file activity config
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    thread::sleep(Duration::from_millis(20));
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");

    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "Monitor should work without file activity config"
    );
}
