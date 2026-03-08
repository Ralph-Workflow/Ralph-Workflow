//! Tests for monitor integration with file activity detection.

use super::super::monitor::MonitorConfig;
use super::super::*;
use crate::executor::{AgentChild, MockAgentChild, MockProcessExecutor};
use crate::workspace::{DirEntry, MemoryWorkspace, Workspace};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime};

fn wait_until_idle_timeout_exceeded(timestamp: &SharedActivityTimestamp, timeout: Duration) {
    timestamp.store(0, Ordering::Release);
    while !is_idle_timeout_exceeded(timestamp, timeout) {
        std::thread::yield_now();
    }
}

#[derive(Debug)]
struct ReadDirCountingWorkspace {
    inner: MemoryWorkspace,
    read_dir_calls: std::sync::atomic::AtomicUsize,
}

impl ReadDirCountingWorkspace {
    fn new(inner: MemoryWorkspace) -> Self {
        Self {
            inner,
            read_dir_calls: std::sync::atomic::AtomicUsize::new(0),
        }
    }

    fn read_dir_calls(&self) -> usize {
        self.read_dir_calls
            .load(std::sync::atomic::Ordering::Acquire)
    }
}

impl Workspace for ReadDirCountingWorkspace {
    fn root(&self) -> &std::path::Path {
        self.inner.root()
    }

    fn read(&self, relative: &std::path::Path) -> std::io::Result<String> {
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &std::path::Path) -> std::io::Result<Vec<u8>> {
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &std::path::Path, content: &str) -> std::io::Result<()> {
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &std::path::Path, content: &[u8]) -> std::io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &std::path::Path, content: &[u8]) -> std::io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &std::path::Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &std::path::Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &std::path::Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn remove(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &std::path::Path) -> std::io::Result<Vec<DirEntry>> {
        self.read_dir_calls
            .fetch_add(1, std::sync::atomic::Ordering::AcqRel);
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &std::path::Path, to: &std::path::Path) -> std::io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &std::path::Path, content: &str) -> std::io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.set_writable(relative)
    }
}

#[test]
fn monitor_prevents_timeout_with_file_activity() {
    // Setup: Process with no stdout output but files being written within recency window
    let timestamp = new_activity_timestamp();

    // Ensure we deterministically exercise the file-activity gating path.
    // This avoids tests passing vacuously when the monotonic epoch is still < timeout.
    let timeout = Duration::from_millis(50);
    wait_until_idle_timeout_exceeded(&timestamp, timeout);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    // Create workspace with a recently modified file (within the 500ms window)
    let workspace = Arc::new(ReadDirCountingWorkspace::new(
        MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Progress"),
    ));
    let workspace_arc: Arc<dyn crate::workspace::Workspace> = workspace.clone();

    let file_activity_config = Some(FileActivityConfig {
        tracker: new_file_activity_tracker(),
        workspace: Arc::clone(&workspace_arc),
    });

    let mock_child = MockAgentChild::new(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor_impl = Arc::new(MockProcessExecutor::new());
    let executor: Arc<dyn crate::executor::ProcessExecutor> = executor_impl.clone();

    // Use a short timeout so idle checks are reached immediately.
    let config = MonitorConfig {
        timeout,
        check_interval: Duration::ZERO,
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

    // Wait until we observe the monitor actually performed a directory scan, proving
    // it exercised the file-activity gating path (as opposed to returning early).
    for _ in 0..100_000 {
        if workspace.read_dir_calls() > 0 {
            break;
        }
        std::thread::yield_now();
    }

    assert!(
        workspace.read_dir_calls() > 0,
        "monitor should scan .agent/ to evaluate file-activity gating"
    );

    // Signal monitor to stop
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");

    // Should complete normally because file activity prevented timeout
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "Monitor should not timeout when files are within recency window"
    );

    assert!(
        executor_impl.execute_calls_for("kill").is_empty(),
        "monitor should not send kill signals when recent file activity is detected"
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
fn monitor_times_out_when_file_activity_check_errors() {
    // Setup: `.agent` exists as a file (not directory), causing read_dir(".agent") to error.
    // The monitor must fail closed: treat file-activity errors as "no activity" and proceed
    // with timeout enforcement so a persistent workspace issue cannot disable the idle timeout.
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

    let (mock_child, controller) = MockAgentChild::new_running(0);
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

    // Wait until we observe a kill attempt, then simulate process exit so the
    // kill path can complete without waiting for a long grace period.
    let mut observed_kill = false;
    for _ in 0..100_000 {
        if !executor_impl.execute_calls_for("kill").is_empty() {
            observed_kill = true;
            controller.store(false, Ordering::Release);
            break;
        }
        std::thread::yield_now();
    }

    if !observed_kill {
        // Old behavior would skip timeout enforcement on file-activity errors and
        // loop forever; stop the monitor so the test fails fast instead of hanging.
        should_stop.store(true, Ordering::Release);
    }

    let result = handle.join().expect("Monitor thread panicked");

    assert!(
        observed_kill,
        "monitor should enforce timeout (kill) when file activity check errors"
    );

    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "monitor should time out when file activity check errors"
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
