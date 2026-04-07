//! Integration tests for file-activity-aware idle timeout behavior.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** These tests follow the integration test style guide in
//! **[../INTEGRATION_TESTS.md](../INTEGRATION_TESTS.md)**.
//! - Tests verify observable timeout behavior
//! - Uses mocked process execution (`MockProcessExecutor`)
//! - Uses in-memory filesystem (`MemoryWorkspace`)

use crate::test_timeout::with_default_timeout;
use ralph_workflow::pipeline::idle_timeout::{
    is_idle_timeout_exceeded, monitor_idle_timeout_with_interval_and_kill_config,
    new_activity_timestamp, new_file_activity_tracker, touch_activity, FileActivityConfig,
    KillConfig, MonitorConfig, MonitorResult,
};
use ralph_workflow::workspace::{DirEntry, MemoryWorkspace, Workspace};
use ralph_workflow::{AgentChild, MockAgentChild, MockProcessExecutor, ProcessExecutor};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

fn wait_until_idle_timeout_exceeded(
    timestamp: &Arc<std::sync::atomic::AtomicU64>,
    timeout: Duration,
) {
    timestamp.store(0, Ordering::Release);
    while !is_idle_timeout_exceeded(timestamp, timeout) {
        std::thread::yield_now();
    }
}

#[derive(Debug)]
struct ReadDirCountingWorkspace {
    inner: MemoryWorkspace,
    read_dir_calls: std::sync::atomic::AtomicUsize,
    gate: Option<Arc<ReadDirGate>>,
}

impl ReadDirCountingWorkspace {
    const fn new(inner: MemoryWorkspace) -> Self {
        Self {
            inner,
            read_dir_calls: std::sync::atomic::AtomicUsize::new(0),
            gate: None,
        }
    }

    const fn with_gate(inner: MemoryWorkspace, gate: Arc<ReadDirGate>) -> Self {
        Self {
            inner,
            read_dir_calls: std::sync::atomic::AtomicUsize::new(0),
            gate: Some(gate),
        }
    }

    fn read_dir_calls(&self) -> usize {
        self.read_dir_calls
            .load(std::sync::atomic::Ordering::Acquire)
    }
}

#[derive(Debug)]
struct ReadDirGate {
    scan_started: AtomicBool,
    released: Mutex<bool>,
    release_condvar: std::sync::Condvar,
}

impl ReadDirGate {
    const fn new() -> Self {
        Self {
            scan_started: AtomicBool::new(false),
            released: Mutex::new(false),
            release_condvar: std::sync::Condvar::new(),
        }
    }

    fn wait_for_scan_to_start(&self) {
        while !self.scan_started.load(Ordering::Acquire) {
            std::thread::yield_now();
        }
    }

    fn release_scan(&self) {
        {
            let mut released = self
                .released
                .lock()
                .expect("read_dir gate mutex should not be poisoned");
            *released = true;
        }
        self.release_condvar.notify_all();
    }

    fn block_until_released(&self) {
        self.scan_started.store(true, Ordering::Release);
        {
            let mut released = self
                .released
                .lock()
                .expect("read_dir gate mutex should not be poisoned");
            while !*released {
                released = self
                    .release_condvar
                    .wait(released)
                    .expect("read_dir gate wait should not be poisoned");
            }
            drop(released);
        }
    }
}

impl Workspace for ReadDirCountingWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> std::io::Result<String> {
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &Path) -> std::io::Result<Vec<u8>> {
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &Path, content: &str) -> std::io::Result<()> {
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> std::io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> std::io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn remove(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> std::io::Result<Vec<DirEntry>> {
        self.read_dir_calls
            .fetch_add(1, std::sync::atomic::Ordering::AcqRel);
        if let Some(gate) = &self.gate {
            gate.block_until_released();
        }
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> std::io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> std::io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.set_writable(relative)
    }
}

#[derive(Debug)]
struct KillNotifyingExecutor {
    inner: Arc<MockProcessExecutor>,
    controller: Option<Arc<std::sync::atomic::AtomicBool>>,
}

impl KillNotifyingExecutor {
    const fn new(
        inner: Arc<MockProcessExecutor>,
        controller: Option<Arc<std::sync::atomic::AtomicBool>>,
    ) -> Self {
        Self { inner, controller }
    }
}

impl ProcessExecutor for KillNotifyingExecutor {
    fn spawn(
        &self,
        command: &str,
        args: &[&str],
        env: &[(String, String)],
        workdir: Option<&Path>,
    ) -> std::io::Result<ralph_workflow::executor::SpawnedProcess> {
        self.inner.spawn(command, args, env, workdir)
    }

    fn spawn_agent(
        &self,
        config: &ralph_workflow::executor::AgentSpawnConfig,
    ) -> std::io::Result<ralph_workflow::executor::AgentChildHandle> {
        self.inner.spawn_agent(config)
    }

    fn execute(
        &self,
        command: &str,
        args: &[&str],
        env: &[(String, String)],
        workdir: Option<&Path>,
    ) -> std::io::Result<ralph_workflow::executor::ProcessOutput> {
        let out = self.inner.execute(command, args, env, workdir);

        if command == "kill" {
            let saw_term = args.contains(&"-TERM");
            if saw_term {
                if let Some(controller) = &self.controller {
                    controller.store(false, Ordering::Release);
                }
            }
        }

        out
    }

    fn get_child_process_info(
        &self,
        parent_pid: u32,
    ) -> ralph_workflow::executor::ChildProcessInfo {
        self.inner.get_child_process_info(parent_pid)
    }
}

const fn fast_kill_config() -> KillConfig {
    KillConfig::new(
        Duration::from_millis(20),
        Duration::from_millis(5),
        Duration::from_millis(20),
        Duration::from_millis(200),
        Duration::from_millis(20),
    )
}

#[test]
fn active_ai_file_updates_prevent_timeout() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        let timeout = Duration::from_millis(50);
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));
        let should_stop_for_monitor = Arc::clone(&should_stop);

        let workspace = Arc::new(ReadDirCountingWorkspace::new(
            MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# in progress"),
        ));
        let workspace_dyn: Arc<dyn Workspace> = workspace.clone();

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace: workspace_dyn,
        });

        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor: Arc<dyn ProcessExecutor> = executor_impl.clone();

        let handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                file_activity_config.as_ref(),
                &child,
                &should_stop_for_monitor,
                &executor,
                MonitorConfig {
                    timeout,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

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
        should_stop.store(true, Ordering::Release);

        let result = handle.join().expect("monitor thread panicked");
        assert_eq!(
            result,
            MonitorResult::ProcessCompleted,
            "recent PLAN.md updates should keep run active"
        );

        assert!(
            executor_impl.execute_calls_for("kill").is_empty(),
            "recent AI file activity should prevent kill signals"
        );
    });
}

#[test]
fn log_only_activity_does_not_prevent_timeout() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        let timeout = Duration::from_millis(50);
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));

        let workspace: Arc<dyn Workspace> = Arc::new(ReadDirCountingWorkspace::new(
            MemoryWorkspace::new_test().with_file(".agent/pipeline.log", "log churn"),
        ));

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace,
        });

        let (mock_child, controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor_dyn: Arc<dyn ProcessExecutor> = Arc::new(KillNotifyingExecutor::new(
            executor_impl.clone(),
            Some(Arc::clone(&controller)),
        ));

        let monitor_handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                file_activity_config.as_ref(),
                &child,
                &should_stop,
                &executor_dyn,
                MonitorConfig {
                    timeout,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        let result = monitor_handle.join().expect("monitor thread panicked");

        assert!(
            matches!(result, MonitorResult::TimedOut { .. }),
            "log-only updates should still time out"
        );

        let kill_calls = executor_impl.execute_calls_for("kill");
        assert!(
            kill_calls
                .iter()
                .any(|(_, args, _, _)| args.iter().any(|a| a == "-TERM")),
            "timeout enforcement should send SIGTERM via kill"
        );
    });
}

#[test]
fn no_output_and_no_ai_files_times_out() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        let timeout = Duration::from_millis(50);
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));
        let workspace: Arc<dyn Workspace> = Arc::new(MemoryWorkspace::new_test());

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace,
        });

        let (mock_child, controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor_dyn: Arc<dyn ProcessExecutor> = Arc::new(KillNotifyingExecutor::new(
            executor_impl.clone(),
            Some(Arc::clone(&controller)),
        ));

        let monitor_handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                file_activity_config.as_ref(),
                &child,
                &should_stop,
                &executor_dyn,
                MonitorConfig {
                    timeout,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        let result = monitor_handle.join().expect("monitor thread panicked");

        assert!(
            matches!(result, MonitorResult::TimedOut { .. }),
            "no output and no AI files should time out"
        );

        let kill_calls = executor_impl.execute_calls_for("kill");
        assert!(
            kill_calls
                .iter()
                .any(|(_, args, _, _)| args.iter().any(|a| a == "-TERM")),
            "timeout enforcement should send SIGTERM via kill"
        );
    });
}

#[test]
fn continuous_file_updates_prevent_timeout_over_extended_period() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        // Use a generous freshness window so the test is robust under system load.
        // The 50ms window used by other tests is too tight when the test suite
        // runs many tests in parallel: sleep() calls may be deferred beyond 50ms,
        // letting files go stale before the update thread can refresh them.
        let timeout = Duration::from_millis(500);
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));
        let should_stop_for_monitor = Arc::clone(&should_stop);

        let workspace = Arc::new(ReadDirCountingWorkspace::new(
            MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Initial plan"),
        ));
        let workspace_dyn: Arc<dyn Workspace> = workspace.clone();

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace: Arc::clone(&workspace_dyn),
        });

        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor: Arc<dyn ProcessExecutor> = executor_impl.clone();

        // Write PLAN.md at a bounded interval until should_stop is set.
        // yield_now() is not sufficient: the OS can defer the thread for longer
        // than the 50ms timeout window, letting the file go stale. A 5ms sleep
        // guarantees writes happen frequently enough to stay within the window.
        let workspace_for_updates = Arc::clone(&workspace);
        let should_stop_for_updates = Arc::clone(&should_stop);
        let update_handle = thread::spawn(move || {
            let mut i = 0usize;
            while !should_stop_for_updates.load(Ordering::Acquire) {
                let _ = workspace_for_updates.write(
                    Path::new(".agent/PLAN.md"),
                    &format!("# Updated plan iteration {i}"),
                );
                i += 1;
                std::thread::sleep(Duration::from_millis(5));
            }
        });

        let handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                file_activity_config.as_ref(),
                &child,
                &should_stop_for_monitor,
                &executor,
                MonitorConfig {
                    timeout,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        // Wait for at least a few file-activity scans, then stop the monitor and
        // updates together. PLAN.md is being written continuously up to this point,
        // so the monitor will always find it fresh on its last check.
        for _ in 0..100_000 {
            if workspace.read_dir_calls() >= 3 {
                break;
            }
            std::thread::yield_now();
        }
        assert!(
            workspace.read_dir_calls() >= 1,
            "monitor should scan .agent/ to evaluate file-activity gating"
        );
        should_stop.store(true, Ordering::Release);

        let result = handle.join().expect("monitor thread panicked");
        update_handle.join().expect("update thread panicked");
        assert_eq!(
            result,
            MonitorResult::ProcessCompleted,
            "continuous file updates should prevent timeout"
        );

        assert!(
            executor_impl.execute_calls_for("kill").is_empty(),
            "file activity should prevent kill signals"
        );
    });
}

#[test]
fn mixed_output_and_file_activity_prevents_timeout() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        let timeout = Duration::from_millis(50);
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));
        let should_stop_for_monitor = Arc::clone(&should_stop);

        let workspace = Arc::new(ReadDirCountingWorkspace::new(
            MemoryWorkspace::new_test().with_file(".agent/NOTES.md", "# Notes"),
        ));
        let workspace_dyn: Arc<dyn Workspace> = workspace.clone();

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace: Arc::clone(&workspace_dyn),
        });

        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor: Arc<dyn ProcessExecutor> = executor_impl.clone();

        let timestamp_for_updates = timestamp.clone();
        let workspace_for_updates = Arc::clone(&workspace);

        let handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                file_activity_config.as_ref(),
                &child,
                &should_stop_for_monitor,
                &executor,
                MonitorConfig {
                    timeout,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        // Wait until file-activity gating is observed, then add stdout + file churn.
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

        for i in 0..4 {
            if i % 2 == 0 {
                touch_activity(&timestamp_for_updates);
            } else {
                let _ = workspace_for_updates
                    .write(Path::new(".agent/NOTES.md"), &format!("# Notes update {i}"));
            }
            std::thread::yield_now();
        }

        should_stop.store(true, Ordering::Release);

        let result = handle.join().expect("monitor thread panicked");
        assert_eq!(
            result,
            MonitorResult::ProcessCompleted,
            "mixed activity should prevent timeout"
        );

        assert!(
            executor_impl.execute_calls_for("kill").is_empty(),
            "mixed activity should prevent kill signals"
        );
    });
}

#[test]
fn workspace_source_file_update_prevents_timeout() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        let timeout = Duration::from_millis(50);
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));
        let should_stop_for_monitor = Arc::clone(&should_stop);

        let workspace = Arc::new(ReadDirCountingWorkspace::new(
            MemoryWorkspace::new_test().with_file("src/lib.rs", "fn main() {}"),
        ));
        let workspace_dyn: Arc<dyn Workspace> = workspace.clone();

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace: workspace_dyn,
        });

        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor: Arc<dyn ProcessExecutor> = executor_impl.clone();

        let handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                file_activity_config.as_ref(),
                &child,
                &should_stop_for_monitor,
                &executor,
                MonitorConfig {
                    timeout,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        // Wait for monitor to perform at least one file activity scan.
        for _ in 0..100_000 {
            if workspace.read_dir_calls() > 0 {
                break;
            }
            std::thread::yield_now();
        }

        assert!(
            workspace.read_dir_calls() > 0,
            "monitor should scan workspace to evaluate file-activity gating"
        );
        should_stop.store(true, Ordering::Release);

        let result = handle.join().expect("monitor thread panicked");
        assert_eq!(
            result,
            MonitorResult::ProcessCompleted,
            "recently modified src/lib.rs should prevent timeout"
        );

        assert!(
            executor_impl.execute_calls_for("kill").is_empty(),
            "workspace source file activity should prevent kill signals"
        );
    });
}

#[test]
fn only_excluded_workspace_files_still_produce_timeout() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        let timeout = Duration::from_millis(50);
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));

        let workspace: Arc<dyn Workspace> = Arc::new(ReadDirCountingWorkspace::new(
            MemoryWorkspace::new_test()
                .with_file("pipeline.log", "log churn")
                .with_file("target/debug/binary", "ELF binary"),
        ));

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace,
        });

        let (mock_child, controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor_dyn: Arc<dyn ProcessExecutor> = Arc::new(KillNotifyingExecutor::new(
            executor_impl.clone(),
            Some(Arc::clone(&controller)),
        ));

        let monitor_handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                file_activity_config.as_ref(),
                &child,
                &should_stop,
                &executor_dyn,
                MonitorConfig {
                    timeout,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        let result = monitor_handle.join().expect("monitor thread panicked");

        assert!(
            matches!(result, MonitorResult::TimedOut { .. }),
            "excluded workspace files only should still time out"
        );

        let kill_calls = executor_impl.execute_calls_for("kill");
        assert!(
            kill_calls
                .iter()
                .any(|(_, args, _, _)| args.iter().any(|a| a == "-TERM")),
            "timeout enforcement should send SIGTERM via kill"
        );
    });
}

#[test]
fn deep_nested_source_file_prevents_timeout() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        let timeout = Duration::from_millis(50);
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));
        let should_stop_for_monitor = Arc::clone(&should_stop);

        // File at depth 4: workspace_root/crate/src/pipeline/idle_timeout/file.rs
        let workspace = Arc::new(ReadDirCountingWorkspace::new(
            MemoryWorkspace::new_test().with_file(
                "ralph-workflow/src/pipeline/idle_timeout/file_activity.rs",
                "// recent edit",
            ),
        ));
        let workspace_dyn: Arc<dyn Workspace> = workspace.clone();

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace: workspace_dyn,
        });

        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor: Arc<dyn ProcessExecutor> = executor_impl.clone();

        let handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                file_activity_config.as_ref(),
                &child,
                &should_stop_for_monitor,
                &executor,
                MonitorConfig {
                    timeout,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        // Wait for at least one workspace scan.
        for _ in 0..100_000 {
            if workspace.read_dir_calls() > 0 {
                break;
            }
            std::thread::yield_now();
        }

        assert!(
            workspace.read_dir_calls() > 0,
            "monitor should perform workspace scan"
        );
        should_stop.store(true, Ordering::Release);

        let result = handle.join().expect("monitor thread panicked");
        assert_eq!(
            result,
            MonitorResult::ProcessCompleted,
            "recently modified deep source file should prevent timeout"
        );

        assert!(
            executor_impl.execute_calls_for("kill").is_empty(),
            "deep file activity should prevent kill signals"
        );
    });
}

#[test]
fn confirmed_file_activity_prevents_kill_on_subsequent_check() {
    // Scenario: a fresh source file is present when the monitor starts.
    // Without the fix (age < timeout, no last_file_activity), the monitor kills the
    // process when the file ages past the timeout window (~timeout ms after test start).
    // With the fix (age <= timeout + widened window, plus last_file_activity tracking),
    // the monitor never kills during the observation window.
    with_default_timeout(|| {
        let timeout = Duration::from_millis(80);

        // Fresh file: mtime = now, so age starts at ~0ms and grows during the test.
        // Without the fix, once file age > timeout (80ms), the monitor kills.
        // With the fix, the file is continuously detected (age <= widened window)
        // and last_file_activity prevents redundant re-scans.
        let workspace: Arc<dyn Workspace> =
            Arc::new(MemoryWorkspace::new_test().with_file("src/lib.rs", "fn main() {}"));

        let timestamp = new_activity_timestamp();
        // Make output appear idle by waiting past timeout.
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));
        let should_stop_for_monitor = Arc::clone(&should_stop);

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace: Arc::clone(&workspace),
        });

        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));
        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor: Arc<dyn ProcessExecutor> = executor_impl.clone();

        let handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                file_activity_config.as_ref(),
                &child,
                &should_stop_for_monitor,
                &executor,
                MonitorConfig {
                    timeout,
                    // check_interval = 0 so the monitor re-checks as fast as possible,
                    // exercising many iterations within the sleep window.
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        // Run for 3× timeout: long enough for the file to age past the bare timeout
        // window (which would cause a kill without the fix).
        std::thread::sleep(timeout * 3);

        // Signal the monitor to stop cleanly.
        should_stop.store(true, Ordering::Release);

        let result = handle.join().expect("monitor thread panicked");
        assert_eq!(
            result,
            MonitorResult::ProcessCompleted,
            "monitor must not kill while source file was recently modified"
        );
        assert!(
            executor_impl.execute_calls_for("kill").is_empty(),
            "no kill signals should be sent when file activity is continuously detected"
        );
    });
}

#[test]
fn output_activity_during_file_scan_prevents_kill() {
    // Scenario: the output timestamp is reset (agent produced output) AFTER the
    // file scan returns false but BEFORE the kill is issued. The monitor must
    // re-check the output timestamp before killing.
    with_default_timeout(|| {
        let timeout = Duration::from_millis(80);

        // Empty workspace - file scan always returns false.
        let gate = Arc::new(ReadDirGate::new());
        let workspace: Arc<dyn Workspace> = Arc::new(ReadDirCountingWorkspace::with_gate(
            MemoryWorkspace::new_test(),
            Arc::clone(&gate),
        ));

        let timestamp = new_activity_timestamp();
        wait_until_idle_timeout_exceeded(&timestamp, timeout);

        let should_stop = Arc::new(AtomicBool::new(false));
        let should_stop_for_monitor = Arc::clone(&should_stop);
        let timestamp_for_monitor = Arc::clone(&timestamp);

        let file_activity_config = Some(FileActivityConfig {
            tracker: new_file_activity_tracker(),
            workspace,
        });

        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));
        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor: Arc<dyn ProcessExecutor> = executor_impl.clone();

        let handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp_for_monitor,
                file_activity_config.as_ref(),
                &child,
                &should_stop_for_monitor,
                &executor,
                MonitorConfig {
                    timeout,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        gate.wait_for_scan_to_start();

        // Simulate output activity arriving after the file scan started but
        // before the monitor can proceed to the kill path.
        touch_activity(&timestamp);
        gate.release_scan();

        // Give the monitor a moment to observe the refreshed timestamp, then stop cleanly.
        std::thread::sleep(Duration::from_millis(20));
        should_stop.store(true, Ordering::Release);

        let result = handle.join().expect("monitor thread panicked");
        assert_eq!(
            result,
            MonitorResult::ProcessCompleted,
            "freshly reset output timestamp must prevent kill even if file scan found nothing"
        );
        assert!(
            executor_impl.execute_calls_for("kill").is_empty(),
            "no kill signals when output timestamp was reset after file scan"
        );
    });
}

/// When the agent has active child processes with advancing CPU time
/// (e.g. a running `cargo build`), the monitor must not kill it even though
/// stdout/stderr is idle.
#[test]
fn active_subprocess_prevents_idle_kill() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        // Force output to appear idle.
        timestamp.store(0, Ordering::Release);

        let should_stop = Arc::new(AtomicBool::new(false));
        let should_stop_for_monitor = Arc::clone(&should_stop);

        let (mock_child, _controller) = MockAgentChild::new_running(0);
        let child_pid = mock_child.id(); // 12345
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        // Configure the executor to report active child processes for our PID.
        let executor_impl =
            Arc::new(MockProcessExecutor::new().with_active_children_for(child_pid));
        let executor: Arc<dyn ProcessExecutor> = executor_impl.clone();

        executor_impl.set_child_cpu_time(child_pid, 100);

        // Simulate CPU time advancing so the monitor treats children as active.
        let cpu_advancer_executor = executor_impl.clone();
        let cpu_advancer_stop = Arc::clone(&should_stop);
        let cpu_advancer = thread::spawn(move || {
            let mut cpu_ms = 100u64;
            while !cpu_advancer_stop.load(Ordering::Acquire) {
                cpu_ms += 100;
                cpu_advancer_executor.set_child_cpu_time(child_pid, cpu_ms);
                thread::sleep(Duration::from_millis(3));
            }
        });

        let handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                None,
                &child,
                &should_stop_for_monitor,
                &executor,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::from_millis(5),
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        // While children are present with advancing CPU, the monitor must not kill.
        thread::sleep(Duration::from_millis(40));
        assert!(
            executor_impl.execute_calls_for("kill").is_empty(),
            "no kill signals should be sent when active child processes are present"
        );

        should_stop.store(true, Ordering::Release);

        let result = handle.join().expect("monitor thread panicked");
        cpu_advancer.join().expect("cpu advancer thread panicked");
        assert_eq!(
            result,
            MonitorResult::ProcessCompleted,
            "active subprocess should prevent idle kill"
        );
    });
}

/// Historical CPU growth alone must not suppress timeout when the current child
/// snapshot shows no active descendants.
#[test]
fn sleeping_subprocess_with_historical_cpu_still_times_out() {
    use ralph_workflow::executor::ChildProcessInfo;

    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        timestamp.store(0, Ordering::Release);

        let should_stop = Arc::new(AtomicBool::new(false));

        let (mock_child, controller) = MockAgentChild::new_running(0);
        let child_pid = mock_child.id();
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_info(
            child_pid,
            ChildProcessInfo {
                child_count: 1,
                active_child_count: 0,
                cpu_time_ms: 100,
                descendant_pid_signature: 55,
            },
        ));
        let executor_dyn: Arc<dyn ProcessExecutor> = Arc::new(KillNotifyingExecutor::new(
            executor_impl.clone(),
            Some(Arc::clone(&controller)),
        ));

        let cpu_history_updater = executor_impl.clone();
        let cpu_history = thread::spawn(move || {
            let mut cpu_ms = 100u64;
            for _ in 0..10 {
                cpu_ms += 25;
                cpu_history_updater.add_active_children_info(
                    child_pid,
                    ChildProcessInfo {
                        child_count: 1,
                        active_child_count: 0,
                        cpu_time_ms: cpu_ms,
                        descendant_pid_signature: 55,
                    },
                );
                thread::sleep(Duration::from_millis(3));
            }
        });

        let monitor_handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                None,
                &child,
                &should_stop,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::from_millis(5),
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 1,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        let result = monitor_handle.join().expect("monitor thread panicked");
        cpu_history.join().expect("cpu history thread panicked");

        assert!(
            matches!(result, MonitorResult::TimedOut { .. }),
            "historical CPU alone should not keep a sleeping subprocess alive"
        );
        assert!(
            !executor_impl.execute_calls_for("kill").is_empty(),
            "timeout enforcement should proceed when children are present but not currently active"
        );
    });
}

/// Repeated child snapshots that stay marked active but never show fresh CPU
/// progress must still time out.
#[test]
fn active_flag_without_fresh_subprocess_progress_still_times_out() {
    use ralph_workflow::executor::ChildProcessInfo;

    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        timestamp.store(0, Ordering::Release);

        let should_stop = Arc::new(AtomicBool::new(false));

        let (mock_child, controller) = MockAgentChild::new_running(0);
        let child_pid = mock_child.id();
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_info(
            child_pid,
            ChildProcessInfo {
                child_count: 1,
                active_child_count: 1,
                cpu_time_ms: 5_000,
                descendant_pid_signature: 155,
            },
        ));
        let executor_dyn: Arc<dyn ProcessExecutor> = Arc::new(KillNotifyingExecutor::new(
            executor_impl.clone(),
            Some(Arc::clone(&controller)),
        ));

        let monitor_handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                None,
                &child,
                &should_stop,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::from_millis(5),
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        let result = monitor_handle.join().expect("monitor thread panicked");

        assert!(
            matches!(result, MonitorResult::TimedOut { .. }),
            "an active flag without fresh child progress must not keep the run alive"
        );
        assert!(
            !executor_impl.execute_calls_for("kill").is_empty(),
            "timeout enforcement should still trigger when child snapshots stay active but stale"
        );
    });
}

/// When output is idle, no file activity is present, and no child processes are
/// running, the monitor must enforce the idle timeout.
#[test]
fn no_active_subprocess_and_no_file_activity_times_out() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        timestamp.store(0, Ordering::Release);

        let should_stop = Arc::new(AtomicBool::new(false));

        let (mock_child, controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        // No children configured; the mock executor reports no active children.
        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor_dyn: Arc<dyn ProcessExecutor> = Arc::new(KillNotifyingExecutor::new(
            executor_impl,
            Some(Arc::clone(&controller)),
        ));

        let monitor_handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                None,
                &child,
                &should_stop,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 1,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        let result = monitor_handle.join().expect("monitor thread panicked");

        assert!(
            matches!(result, MonitorResult::TimedOut { .. }),
            "no active subprocess and no file activity should time out"
        );
    });
}

/// Timeout with stalled children includes `child_status_at_timeout` in result.
#[test]
fn stalled_subprocess_timeout_includes_child_status() {
    use ralph_workflow::executor::ChildProcessInfo;

    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        timestamp.store(0, Ordering::Release);

        let should_stop = Arc::new(AtomicBool::new(false));

        let (mock_child, controller) = MockAgentChild::new_running(0);
        let child_pid = mock_child.id();
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        // Stalled children: fixed CPU time that never advances.
        let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_info(
            child_pid,
            ChildProcessInfo {
                child_count: 2,
                active_child_count: 0,
                cpu_time_ms: 4200,
                descendant_pid_signature: 77,
            },
        ));
        let executor_dyn: Arc<dyn ProcessExecutor> = Arc::new(KillNotifyingExecutor::new(
            executor_impl,
            Some(Arc::clone(&controller)),
        ));

        let monitor_handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                None,
                &child,
                &should_stop,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        let result = monitor_handle.join().expect("monitor thread panicked");

        match result {
            MonitorResult::TimedOut {
                child_status_at_timeout: Some(info),
                ..
            } => {
                assert_eq!(info.child_count, 2);
                assert_eq!(info.cpu_time_ms, 4200);
            }
            other => panic!("expected TimedOut with child_status_at_timeout=Some, got {other:?}"),
        }
    });
}

/// Reappearing children without fresh work must not restart startup grace.
#[test]
fn reappearing_stalled_subprocess_does_not_reset_idle_confirmation() {
    use ralph_workflow::executor::ChildProcessInfo;

    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        timestamp.store(0, Ordering::Release);

        let should_stop = Arc::new(AtomicBool::new(false));
        let should_stop_for_monitor = Arc::clone(&should_stop);

        let (mock_child, controller) = MockAgentChild::new_running(0);
        let child_pid = mock_child.id();
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new().with_active_children_info(
            child_pid,
            ChildProcessInfo {
                child_count: 1,
                active_child_count: 0,
                cpu_time_ms: 0,
                descendant_pid_signature: 33,
            },
        ));
        let executor_dyn: Arc<dyn ProcessExecutor> = Arc::new(KillNotifyingExecutor::new(
            executor_impl.clone(),
            Some(Arc::clone(&controller)),
        ));

        let monitor_handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                None,
                &child,
                &should_stop_for_monitor,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::from_millis(20),
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 4,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        thread::sleep(Duration::from_millis(25));
        executor_impl.remove_active_children_for(child_pid);
        thread::sleep(Duration::from_millis(25));
        executor_impl.add_active_children_info(
            child_pid,
            ChildProcessInfo {
                child_count: 1,
                active_child_count: 0,
                cpu_time_ms: 0,
                descendant_pid_signature: 44,
            },
        );

        let result = monitor_handle.join().expect("monitor thread panicked");
        match result {
            MonitorResult::TimedOut {
                child_status_at_timeout: Some(info),
                ..
            } => {
                assert_eq!(info.child_count, 1);
                assert_eq!(info.cpu_time_ms, 0);
                assert_eq!(
                    info.descendant_pid_signature, 44,
                    "timeout should reflect the reappeared stalled child subtree rather than timing out before it was observed"
                );
            }
            other => panic!(
                "expected timed out result that observed the reappeared stalled child, got {other:?}"
            ),
        }
    });
}

/// Event round-trip: `child_status_at_timeout` survives serialization.
#[test]
fn child_status_at_timeout_survives_event_serde_round_trip() {
    with_default_timeout(|| {
        use ralph_workflow::executor::ChildProcessInfo;
        use ralph_workflow::reducer::event::{PipelineEvent, TimeoutOutputKind};

        let info = ChildProcessInfo {
            child_count: 2,
            active_child_count: 0,
            cpu_time_ms: 5000,
            descendant_pid_signature: 88,
        };
        let event = PipelineEvent::agent_timed_out(
            ralph_workflow::agents::AgentRole::Developer,
            "test-agent".into(),
            TimeoutOutputKind::PartialResult,
            Some("/tmp/test.log".to_string()),
            Some(info),
        );

        let json = serde_json::to_string(&event).expect("serialize");
        let restored: PipelineEvent = serde_json::from_str(&json).expect("deserialize");

        // Verify the event round-trips correctly
        let json2 = serde_json::to_string(&restored).expect("re-serialize");
        assert_eq!(json, json2, "event should survive JSON round-trip");
    });
}

/// Backward compatibility: `TimedOut` events without `child_status_at_timeout` deserialize as None.
#[test]
fn timed_out_event_without_child_status_deserializes_as_none() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::event::PipelineEvent;

        // JSON representing a TimedOut event from before the child_status_at_timeout field existed
        let old_json = r#"{"Agent":{"TimedOut":{"role":"Developer","agent":"old-agent","output_kind":"NoOutput","logfile_path":"/tmp/old.log"}}}"#;

        let event: PipelineEvent = serde_json::from_str(old_json)
            .expect("old-format TimedOut event should still deserialize");

        let json = serde_json::to_string(&event).expect("serialize");
        // The deserialized event should include child_status_at_timeout: null
        assert!(
            json.contains("child_status_at_timeout"),
            "serialized event should include child_status_at_timeout field"
        );
    });
}

/// Timeout with no children has `child_status_at_timeout: None`.
#[test]
fn no_subprocess_timeout_has_none_child_status() {
    with_default_timeout(|| {
        let timestamp = new_activity_timestamp();
        timestamp.store(0, Ordering::Release);

        let should_stop = Arc::new(AtomicBool::new(false));

        let (mock_child, controller) = MockAgentChild::new_running(0);
        let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

        let executor_impl = Arc::new(MockProcessExecutor::new());
        let executor_dyn: Arc<dyn ProcessExecutor> = Arc::new(KillNotifyingExecutor::new(
            executor_impl,
            Some(Arc::clone(&controller)),
        ));

        let monitor_handle = thread::spawn(move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                None,
                &child,
                &should_stop,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::ZERO,
                    kill_config: fast_kill_config(),
                    required_idle_confirmations: 1,
                    check_child_processes: true,
                    completion_check: None,

                    partial_completion_check: None,
                },
            )
        });

        let result = monitor_handle.join().expect("monitor thread panicked");

        match result {
            MonitorResult::TimedOut {
                child_status_at_timeout: None,
                ..
            } => {}
            other => panic!("expected TimedOut with child_status_at_timeout=None, got {other:?}"),
        }
    });
}
