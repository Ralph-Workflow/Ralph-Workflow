use super::*;

#[test]
#[cfg(unix)]
fn test_run_with_agent_spawn_does_not_hang_when_stdout_closes_early_and_idle_timeout_triggers() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::atomic::AtomicBool;
    use std::sync::{mpsc, Arc};
    use std::time::Duration;

    use std::os::unix::process::ExitStatusExt;

    #[derive(Debug)]
    struct SharedRunningChild {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::AgentChild for SharedRunningChild {
        fn id(&self) -> u32 {
            12345
        }

        fn wait(&mut self) -> io::Result<ExitStatus> {
            while self.still_running.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(10));
            }
            Ok(ExitStatus::from_raw(0))
        }

        fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
            if self.still_running.load(Ordering::Acquire) {
                return Ok(None);
            }
            Ok(Some(ExitStatus::from_raw(0)))
        }
    }

    #[derive(Debug)]
    struct HangingAgentExecutor {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::ProcessExecutor for HangingAgentExecutor {
        fn execute(
            &self,
            command: &str,
            args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            if command == "kill" && args.contains(&"-KILL") {
                self.still_running.store(false, Ordering::Release);
            }

            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }

        fn spawn_agent(
            &self,
            _config: &crate::executor::AgentSpawnConfig,
        ) -> io::Result<crate::executor::AgentChildHandle> {
            let stdout = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let stderr = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let inner: Box<dyn crate::executor::AgentChild> = Box::new(SharedRunningChild {
                still_running: Arc::clone(&self.still_running),
            });

            Ok(crate::executor::AgentChildHandle {
                stdout,
                stderr,
                inner,
            })
        }
    }

    let workspace = MemoryWorkspace::new_test();
    let logger = test_logger();
    let colors = Colors::new();
    let config = Config::test_default();
    let mut timer = Timer::new();

    let still_running = Arc::new(AtomicBool::new(true));
    let executor = Arc::new(HangingAgentExecutor {
        still_running: Arc::clone(&still_running),
    });
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let env_vars: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let cmd = PromptCommand {
        label: "test",
        display_name: "test",
        cmd_str: "mock-agent",
        prompt: "hello",
        log_prefix: ".agent/logs/test",
        model_index: None,
        attempt: None,
        logfile: ".agent/logs/test.log",
        parser_type: JsonParserType::Generic,
        env_vars: &env_vars,
        completion_output_path: None,
    };

    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor.as_ref(),
        executor_arc,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
    };

    std::thread::scope(|scope| {
        let (tx, rx) = mpsc::channel();
        scope.spawn(move || {
            let result = run_with_agent_spawn_with_monitor_config(
                &cmd,
                &runtime,
                &[],
                Duration::ZERO,
                Duration::from_millis(10),
                crate::pipeline::idle_timeout::KillConfig::new(
                    Duration::from_millis(20),
                    Duration::from_millis(1),
                    Duration::from_millis(20),
                    Duration::from_secs(2),
                    Duration::from_millis(50),
                ),
            );
            let _ = tx.send(result);
        });

        let exit_code = rx
            .recv_timeout(Duration::from_secs(10))
            .ok()
            .map(|result| result.expect("expected successful CommandResult").exit_code);

        still_running.store(false, Ordering::Release);
        assert_eq!(exit_code, Some(143));
    });
}

#[test]
#[cfg(unix)]
fn test_run_with_agent_spawn_cancels_stderr_collector_on_idle_timeout() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::atomic::AtomicBool;
    use std::time::Duration;

    use std::os::unix::process::ExitStatusExt;

    #[derive(Debug)]
    struct SharedRunningChild {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::AgentChild for SharedRunningChild {
        fn id(&self) -> u32 {
            12345
        }

        fn wait(&mut self) -> io::Result<ExitStatus> {
            while self.still_running.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(10));
            }
            Ok(ExitStatus::from_raw(0))
        }

        fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
            if self.still_running.load(Ordering::Acquire) {
                return Ok(None);
            }
            Ok(Some(ExitStatus::from_raw(0)))
        }
    }

    #[derive(Debug, Clone)]
    struct WouldBlockForever {
        stop: Arc<AtomicBool>,
        reads: Arc<AtomicUsize>,
    }

    impl Read for WouldBlockForever {
        fn read(&mut self, _buf: &mut [u8]) -> io::Result<usize> {
            self.reads.fetch_add(1, Ordering::SeqCst);
            if self.stop.load(Ordering::Acquire) {
                return Ok(0);
            }
            Err(io::Error::from(io::ErrorKind::WouldBlock))
        }
    }

    #[derive(Debug)]
    struct HangingAgentExecutor {
        still_running: Arc<AtomicBool>,
        stderr_stop: Arc<AtomicBool>,
        stderr_reads: Arc<AtomicUsize>,
    }

    impl crate::executor::ProcessExecutor for HangingAgentExecutor {
        fn execute(
            &self,
            command: &str,
            args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            if command == "kill" && args.contains(&"-KILL") {
                self.still_running.store(false, Ordering::Release);
            }

            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }

        fn spawn_agent(
            &self,
            _config: &crate::executor::AgentSpawnConfig,
        ) -> io::Result<crate::executor::AgentChildHandle> {
            let stdout = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let stderr = Box::new(WouldBlockForever {
                stop: Arc::clone(&self.stderr_stop),
                reads: Arc::clone(&self.stderr_reads),
            }) as Box<dyn io::Read + Send>;
            let inner: Box<dyn crate::executor::AgentChild> = Box::new(SharedRunningChild {
                still_running: Arc::clone(&self.still_running),
            });

            Ok(crate::executor::AgentChildHandle {
                stdout,
                stderr,
                inner,
            })
        }
    }

    let workspace = MemoryWorkspace::new_test();
    let logger = test_logger();
    let colors = Colors::new();
    let config = Config::test_default();
    let mut timer = Timer::new();

    let still_running = Arc::new(AtomicBool::new(true));
    let stderr_stop = Arc::new(AtomicBool::new(false));
    let stderr_reads = Arc::new(AtomicUsize::new(0));
    let executor = Arc::new(HangingAgentExecutor {
        still_running: Arc::clone(&still_running),
        stderr_stop: Arc::clone(&stderr_stop),
        stderr_reads: Arc::clone(&stderr_reads),
    });
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let env_vars: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let cmd = PromptCommand {
        label: "test",
        display_name: "test",
        cmd_str: "mock-agent",
        prompt: "hello",
        log_prefix: ".agent/logs/test",
        model_index: None,
        attempt: None,
        logfile: ".agent/logs/test.log",
        parser_type: JsonParserType::Generic,
        env_vars: &env_vars,
        completion_output_path: None,
    };

    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor.as_ref(),
        executor_arc,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
    };

    let result = run_with_agent_spawn_with_monitor_config(
        &cmd,
        &runtime,
        &[],
        Duration::ZERO,
        Duration::from_millis(10),
        crate::pipeline::idle_timeout::KillConfig::new(
            Duration::from_millis(20),
            Duration::from_millis(1),
            Duration::from_millis(20),
            Duration::from_secs(2),
            Duration::from_millis(50),
        ),
    )
    .expect("expected successful CommandResult");

    assert_eq!(result.exit_code, 143);

    let reads_at_return = stderr_reads.load(Ordering::Acquire);
    assert!(
        reads_at_return > 0,
        "expected stderr collector to poll at least once"
    );
    std::thread::sleep(Duration::from_millis(30));
    let reads_after = stderr_reads.load(Ordering::Acquire);

    stderr_stop.store(true, Ordering::Release);
    still_running.store(false, Ordering::Release);

    assert_eq!(
        reads_after, reads_at_return,
        "stderr collector appears to still be polling after idle-timeout return"
    );
}

#[test]
#[cfg(unix)]
fn test_run_with_agent_spawn_regains_control_when_child_never_exits_after_sigkill() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::atomic::AtomicBool;
    use std::sync::{mpsc, Arc};
    use std::time::Duration;

    use std::os::unix::process::ExitStatusExt;

    #[derive(Debug)]
    struct UnkillableChild {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::AgentChild for UnkillableChild {
        fn id(&self) -> u32 {
            12345
        }

        fn wait(&mut self) -> io::Result<ExitStatus> {
            while self.still_running.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(10));
            }
            Ok(ExitStatus::from_raw(0))
        }

        fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
            if self.still_running.load(Ordering::Acquire) {
                return Ok(None);
            }
            Ok(Some(ExitStatus::from_raw(0)))
        }
    }

    #[derive(Debug)]
    struct UnkillableExecutor {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::ProcessExecutor for UnkillableExecutor {
        fn execute(
            &self,
            _command: &str,
            _args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }

        fn spawn_agent(
            &self,
            _config: &crate::executor::AgentSpawnConfig,
        ) -> io::Result<crate::executor::AgentChildHandle> {
            let stdout = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let stderr = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let inner: Box<dyn crate::executor::AgentChild> = Box::new(UnkillableChild {
                still_running: Arc::clone(&self.still_running),
            });
            Ok(crate::executor::AgentChildHandle {
                stdout,
                stderr,
                inner,
            })
        }
    }

    let workspace = MemoryWorkspace::new_test();
    let logger = test_logger();
    let colors = Colors::new();
    let config = Config::test_default();
    let mut timer = Timer::new();

    let still_running = Arc::new(AtomicBool::new(true));
    let executor = Arc::new(UnkillableExecutor {
        still_running: Arc::clone(&still_running),
    });
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let env_vars: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let cmd = PromptCommand {
        label: "test",
        display_name: "test",
        cmd_str: "mock-agent",
        prompt: "hello",
        log_prefix: ".agent/logs/test",
        model_index: None,
        attempt: None,
        logfile: ".agent/logs/test.log",
        parser_type: JsonParserType::Generic,
        env_vars: &env_vars,
        completion_output_path: None,
    };

    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor.as_ref(),
        executor_arc,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
    };

    std::thread::scope(|scope| {
        let (tx, rx) = mpsc::channel();
        scope.spawn(move || {
            let result = run_with_agent_spawn_with_monitor_config(
                &cmd,
                &runtime,
                &[],
                Duration::ZERO,
                Duration::from_millis(10),
                crate::pipeline::idle_timeout::KillConfig::new(
                    Duration::from_millis(20),
                    Duration::from_millis(5),
                    Duration::from_millis(20),
                    Duration::from_millis(100),
                    Duration::from_millis(20),
                ),
            );
            let _ = tx.send(result);
        });

        let received = rx.recv_timeout(Duration::from_secs(5));
        still_running.store(false, Ordering::Release);

        let result = received.expect("expected run to return without hanging");
        let result = result.expect("expected successful CommandResult");
        assert_eq!(result.exit_code, 143);
    });
}

#[test]
#[cfg(unix)]
fn test_run_with_agent_spawn_regains_control_when_stdout_read_blocks_and_idle_timeout_triggers() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::atomic::AtomicBool;
    use std::sync::{mpsc, Arc};
    use std::time::Duration;

    use std::os::unix::process::ExitStatusExt;

    #[derive(Debug)]
    struct SharedRunningChild {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::AgentChild for SharedRunningChild {
        fn id(&self) -> u32 {
            12345
        }

        fn wait(&mut self) -> io::Result<ExitStatus> {
            while self.still_running.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(10));
            }
            Ok(ExitStatus::from_raw(0))
        }

        fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
            if self.still_running.load(Ordering::Acquire) {
                return Ok(None);
            }
            Ok(Some(ExitStatus::from_raw(0)))
        }
    }

    #[derive(Debug, Clone)]
    struct BlockingUntilReleased {
        released: Arc<AtomicBool>,
    }

    impl Read for BlockingUntilReleased {
        fn read(&mut self, _buf: &mut [u8]) -> io::Result<usize> {
            while !self.released.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(10));
            }
            Ok(0)
        }
    }

    #[derive(Debug)]
    struct HangingStdoutExecutor {
        still_running: Arc<AtomicBool>,
        stdout_released: Arc<AtomicBool>,
    }

    impl crate::executor::ProcessExecutor for HangingStdoutExecutor {
        fn execute(
            &self,
            command: &str,
            args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            if command == "kill" && args.contains(&"-KILL") {
                self.still_running.store(false, Ordering::Release);
            }

            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }

        fn spawn_agent(
            &self,
            _config: &crate::executor::AgentSpawnConfig,
        ) -> io::Result<crate::executor::AgentChildHandle> {
            let stdout = Box::new(BlockingUntilReleased {
                released: Arc::clone(&self.stdout_released),
            }) as Box<dyn io::Read + Send>;
            let stderr = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let inner: Box<dyn crate::executor::AgentChild> = Box::new(SharedRunningChild {
                still_running: Arc::clone(&self.still_running),
            });

            Ok(crate::executor::AgentChildHandle {
                stdout,
                stderr,
                inner,
            })
        }
    }

    let workspace = MemoryWorkspace::new_test();
    let logger = test_logger();
    let colors = Colors::new();
    let config = Config::test_default();
    let mut timer = Timer::new();

    let still_running = Arc::new(AtomicBool::new(true));
    let stdout_released = Arc::new(AtomicBool::new(false));
    let executor = Arc::new(HangingStdoutExecutor {
        still_running: Arc::clone(&still_running),
        stdout_released: Arc::clone(&stdout_released),
    });
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let env_vars: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let cmd = PromptCommand {
        label: "test",
        display_name: "test",
        cmd_str: "mock-agent",
        prompt: "hello",
        log_prefix: ".agent/logs/test",
        model_index: None,
        attempt: None,
        logfile: ".agent/logs/test.log",
        parser_type: JsonParserType::Generic,
        env_vars: &env_vars,
        completion_output_path: None,
    };

    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor.as_ref(),
        executor_arc,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
    };

    std::thread::scope(|scope| {
        let (tx, rx) = mpsc::channel();
        scope.spawn(move || {
            let result = run_with_agent_spawn_with_monitor_config(
                &cmd,
                &runtime,
                &[],
                Duration::ZERO,
                Duration::from_millis(10),
                crate::pipeline::idle_timeout::KillConfig::new(
                    Duration::from_millis(20),
                    Duration::from_millis(1),
                    Duration::from_millis(20),
                    Duration::from_millis(100),
                    Duration::from_millis(20),
                ),
            );
            let _ = tx.send(result);
        });

        let received = rx.recv_timeout(Duration::from_secs(3));

        // Ensure the worker thread can unwind even if the assertion fails.
        stdout_released.store(true, Ordering::Release);
        still_running.store(false, Ordering::Release);
        let _ = rx.recv_timeout(Duration::from_secs(3));

        let result = received.expect("expected run to regain control and return");
        let result = result.expect("expected successful CommandResult");
        assert_eq!(result.exit_code, 143);
    });
}

#[test]
#[cfg(unix)]
fn test_run_with_agent_spawn_logs_child_activity_timeout_suppression() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::atomic::AtomicBool;
    use std::sync::Mutex;
    use std::time::Duration;

    use std::os::unix::process::ExitStatusExt;

    #[derive(Debug)]
    struct SharedRunningChild {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::AgentChild for SharedRunningChild {
        fn id(&self) -> u32 {
            12345
        }

        fn wait(&mut self) -> io::Result<ExitStatus> {
            while self.still_running.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(5));
            }
            Ok(ExitStatus::from_raw(0))
        }

        fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
            if self.still_running.load(Ordering::Acquire) {
                return Ok(None);
            }
            Ok(Some(ExitStatus::from_raw(0)))
        }
    }

    #[derive(Debug)]
    struct ChildActivityExecutor {
        still_running: Arc<AtomicBool>,
        child_info: Arc<Mutex<crate::executor::ChildProcessInfo>>,
    }

    impl crate::executor::ProcessExecutor for ChildActivityExecutor {
        fn execute(
            &self,
            _command: &str,
            _args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }

        fn spawn_agent(
            &self,
            _config: &crate::executor::AgentSpawnConfig,
        ) -> io::Result<crate::executor::AgentChildHandle> {
            let stdout = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let stderr = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let inner: Box<dyn crate::executor::AgentChild> = Box::new(SharedRunningChild {
                still_running: Arc::clone(&self.still_running),
            });

            Ok(crate::executor::AgentChildHandle {
                stdout,
                stderr,
                inner,
            })
        }

        fn get_child_process_info(&self, _parent_pid: u32) -> crate::executor::ChildProcessInfo {
            *self
                .child_info
                .lock()
                .expect("child info mutex should not be poisoned")
        }
    }

    let workspace = MemoryWorkspace::new_test();
    let log_workspace = Arc::new(workspace.clone());
    let logger = Logger::new(Colors::with_enabled(false)).with_workspace_log(
        Arc::clone(&log_workspace) as Arc<dyn Workspace>,
        ".agent/tmp/child-activity.log",
    );
    let colors = Colors::with_enabled(false);
    let config = Config::test_default();
    let mut timer = Timer::new();

    let still_running = Arc::new(AtomicBool::new(true));
    let child_info = Arc::new(Mutex::new(crate::executor::ChildProcessInfo {
        child_count: 1,
        active_child_count: 1,
        cpu_time_ms: 0,
        descendant_pid_signature: 41,
    }));
    let executor = Arc::new(ChildActivityExecutor {
        still_running: Arc::clone(&still_running),
        child_info: Arc::clone(&child_info),
    });
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let child_info_updater = Arc::clone(&child_info);
    let still_running_for_worker = Arc::clone(&still_running);
    let child_worker = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(30));
        child_info_updater
            .lock()
            .expect("child info mutex should not be poisoned")
            .cpu_time_ms = 100;
        std::thread::sleep(Duration::from_millis(30));
        child_info_updater
            .lock()
            .expect("child info mutex should not be poisoned")
            .cpu_time_ms = 250;
        std::thread::sleep(Duration::from_millis(30));
        child_info_updater
            .lock()
            .expect("child info mutex should not be poisoned")
            .cpu_time_ms = 400;
        still_running_for_worker.store(false, Ordering::Release);
    });

    let env_vars: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let cmd = PromptCommand {
        label: "test",
        display_name: "test",
        cmd_str: "mock-agent",
        prompt: "hello",
        log_prefix: ".agent/logs/test",
        model_index: None,
        attempt: None,
        logfile: ".agent/logs/test.log",
        parser_type: JsonParserType::Generic,
        env_vars: &env_vars,
        completion_output_path: None,
    };

    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor.as_ref(),
        executor_arc,
        workspace: &workspace,
        workspace_arc: Arc::new(workspace.clone()),
    };

    let result = run_with_agent_spawn_with_monitor_config(
        &cmd,
        &runtime,
        &[],
        Duration::ZERO,
        Duration::from_millis(20),
        crate::pipeline::idle_timeout::KillConfig::new(
            Duration::from_millis(20),
            Duration::from_millis(1),
            Duration::from_millis(20),
            Duration::from_millis(100),
            Duration::from_millis(20),
        ),
    )
    .expect("expected successful CommandResult");

    child_worker
        .join()
        .expect("child activity worker thread should not panic");

    assert_eq!(result.exit_code, 0, "process should complete normally");

    let log_output = log_workspace
        .read(std::path::Path::new(".agent/tmp/child-activity.log"))
        .expect("expected workspace log output");
    assert!(
        log_output.contains(
            "idle timeout suppression: child processes showed fresh progress and remained relevant"
        ),
        "structured logger output should explain child-process timeout suppression"
    );
}

#[test]
#[cfg(unix)]
fn test_run_with_agent_spawn_logs_stalled_child_timeout_reason() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::atomic::AtomicBool;
    use std::sync::Mutex;
    use std::time::Duration;

    use std::os::unix::process::ExitStatusExt;

    #[derive(Debug)]
    struct SharedRunningChild {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::AgentChild for SharedRunningChild {
        fn id(&self) -> u32 {
            12345
        }

        fn wait(&mut self) -> io::Result<ExitStatus> {
            while self.still_running.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(5));
            }
            Ok(ExitStatus::from_raw(0))
        }

        fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
            if self.still_running.load(Ordering::Acquire) {
                return Ok(None);
            }
            Ok(Some(ExitStatus::from_raw(0)))
        }
    }

    #[derive(Debug)]
    struct StalledChildExecutor {
        still_running: Arc<AtomicBool>,
        child_info: Arc<Mutex<crate::executor::ChildProcessInfo>>,
    }

    impl crate::executor::ProcessExecutor for StalledChildExecutor {
        fn execute(
            &self,
            command: &str,
            args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            if command == "kill" && args.contains(&"-TERM") {
                self.still_running.store(false, Ordering::Release);
            }

            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }

        fn spawn_agent(
            &self,
            _config: &crate::executor::AgentSpawnConfig,
        ) -> io::Result<crate::executor::AgentChildHandle> {
            let stdout = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let stderr = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let inner: Box<dyn crate::executor::AgentChild> = Box::new(SharedRunningChild {
                still_running: Arc::clone(&self.still_running),
            });

            Ok(crate::executor::AgentChildHandle {
                stdout,
                stderr,
                inner,
            })
        }

        fn get_child_process_info(&self, _parent_pid: u32) -> crate::executor::ChildProcessInfo {
            *self
                .child_info
                .lock()
                .expect("child info mutex should not be poisoned")
        }
    }

    let workspace = MemoryWorkspace::new_test();
    let log_workspace = Arc::new(workspace.clone());
    let logger = Logger::new(Colors::with_enabled(false)).with_workspace_log(
        Arc::clone(&log_workspace) as Arc<dyn Workspace>,
        ".agent/tmp/stalled-child-timeout.log",
    );
    let colors = Colors::with_enabled(false);
    let config = Config::test_default();
    let mut timer = Timer::new();

    let still_running = Arc::new(AtomicBool::new(true));
    let child_info = Arc::new(Mutex::new(crate::executor::ChildProcessInfo {
        child_count: 2,
        active_child_count: 0,
        cpu_time_ms: 4200,
        descendant_pid_signature: 57,
    }));
    let executor = Arc::new(StalledChildExecutor {
        still_running: Arc::clone(&still_running),
        child_info,
    });
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let env_vars: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let cmd = PromptCommand {
        label: "test",
        display_name: "test",
        cmd_str: "mock-agent",
        prompt: "hello",
        log_prefix: ".agent/logs/test",
        model_index: None,
        attempt: None,
        logfile: ".agent/logs/test.log",
        parser_type: JsonParserType::Generic,
        env_vars: &env_vars,
        completion_output_path: None,
    };

    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor.as_ref(),
        executor_arc,
        workspace: &workspace,
        workspace_arc: Arc::new(workspace.clone()),
    };

    let result = run_with_agent_spawn_with_monitor_config(
        &cmd,
        &runtime,
        &[],
        Duration::ZERO,
        Duration::from_millis(10),
        crate::pipeline::idle_timeout::KillConfig::new(
            Duration::from_millis(20),
            Duration::from_millis(1),
            Duration::from_millis(20),
            Duration::from_millis(100),
            Duration::from_millis(20),
        ),
    )
    .expect("expected successful CommandResult");

    assert_eq!(
        result.exit_code,
        super::SIGTERM_EXIT_CODE,
        "stalled descendants should not suppress idle timeout"
    );

    let log_output = log_workspace
        .read(std::path::Path::new(".agent/tmp/stalled-child-timeout.log"))
        .expect("expected workspace log output");
    assert!(
        log_output.contains("child processes present but not currently active"),
        "structured logger output should distinguish stalled descendants from runs with no qualifying children"
    );
}

#[test]
#[cfg(unix)]
fn test_run_with_agent_spawn_logs_stale_active_child_timeout_reason() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::atomic::AtomicBool;
    use std::sync::Mutex;
    use std::time::Duration;

    use std::os::unix::process::ExitStatusExt;

    #[derive(Debug)]
    struct SharedRunningChild {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::AgentChild for SharedRunningChild {
        fn id(&self) -> u32 {
            12345
        }

        fn wait(&mut self) -> io::Result<ExitStatus> {
            while self.still_running.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(5));
            }
            Ok(ExitStatus::from_raw(0))
        }

        fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
            if self.still_running.load(Ordering::Acquire) {
                return Ok(None);
            }
            Ok(Some(ExitStatus::from_raw(0)))
        }
    }

    #[derive(Debug)]
    struct StaleActiveChildExecutor {
        still_running: Arc<AtomicBool>,
        child_info: Arc<Mutex<crate::executor::ChildProcessInfo>>,
    }

    impl crate::executor::ProcessExecutor for StaleActiveChildExecutor {
        fn execute(
            &self,
            command: &str,
            args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            if command == "kill" && args.contains(&"-TERM") {
                self.still_running.store(false, Ordering::Release);
            }

            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }

        fn spawn_agent(
            &self,
            _config: &crate::executor::AgentSpawnConfig,
        ) -> io::Result<crate::executor::AgentChildHandle> {
            let stdout = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let stderr = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let inner: Box<dyn crate::executor::AgentChild> = Box::new(SharedRunningChild {
                still_running: Arc::clone(&self.still_running),
            });

            Ok(crate::executor::AgentChildHandle {
                stdout,
                stderr,
                inner,
            })
        }

        fn get_child_process_info(&self, _parent_pid: u32) -> crate::executor::ChildProcessInfo {
            *self
                .child_info
                .lock()
                .expect("child info mutex should not be poisoned")
        }
    }

    let workspace = MemoryWorkspace::new_test();
    let log_workspace = Arc::new(workspace.clone());
    let logger = Logger::new(Colors::with_enabled(false)).with_workspace_log(
        Arc::clone(&log_workspace) as Arc<dyn Workspace>,
        ".agent/tmp/stale-active-child-timeout.log",
    );
    let colors = Colors::with_enabled(false);
    let config = Config::test_default();
    let mut timer = Timer::new();

    let still_running = Arc::new(AtomicBool::new(true));
    let child_info = Arc::new(Mutex::new(crate::executor::ChildProcessInfo {
        child_count: 1,
        active_child_count: 1,
        cpu_time_ms: 8_400,
        descendant_pid_signature: 59,
    }));
    let executor = Arc::new(StaleActiveChildExecutor {
        still_running: Arc::clone(&still_running),
        child_info,
    });
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let env_vars: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let cmd = PromptCommand {
        label: "test",
        display_name: "test",
        cmd_str: "mock-agent",
        prompt: "hello",
        log_prefix: ".agent/logs/test",
        model_index: None,
        attempt: None,
        logfile: ".agent/logs/test.log",
        parser_type: JsonParserType::Generic,
        env_vars: &env_vars,
        completion_output_path: None,
    };

    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor.as_ref(),
        executor_arc,
        workspace: &workspace,
        workspace_arc: Arc::new(workspace.clone()),
    };

    let result = run_with_agent_spawn_with_monitor_config(
        &cmd,
        &runtime,
        &[],
        Duration::ZERO,
        Duration::from_millis(10),
        crate::pipeline::idle_timeout::KillConfig::new(
            Duration::from_millis(20),
            Duration::from_millis(1),
            Duration::from_millis(20),
            Duration::from_millis(100),
            Duration::from_millis(20),
        ),
    )
    .expect("expected successful CommandResult");

    assert_eq!(
        result.exit_code,
        super::SIGTERM_EXIT_CODE,
        "child snapshots that stay active but stale must not suppress idle timeout"
    );

    let log_output = log_workspace
        .read(std::path::Path::new(
            ".agent/tmp/stale-active-child-timeout.log",
        ))
        .expect("expected workspace log output");
    assert!(
        log_output.contains("child processes still looked active but showed no fresh progress"),
        "structured logger output should explain when stale active child snapshots stop suppressing timeout"
    );
}

/// Bug 3 regression: A Codex `item.started` event emitted on stdout causes the parser to set
/// the tool-activity flag, which suppresses the idle-timeout monitor during the quiet period
/// that follows (e.g. while a file-write tool is executing with no further output).
///
/// The test verifies the end-to-end wiring:
///   parser sees item.started → sets tool_active = true → monitor sees tool_active = true →
///   suppresses timeout → child eventually exits → result is ProcessCompleted, not TimedOut.
#[test]
#[cfg(unix)]
fn test_codex_item_started_event_suppresses_idle_timeout() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::Arc;
    use std::time::Duration;

    use std::os::unix::process::ExitStatusExt;

    // Executor that emits a single Codex item.started JSON line then goes quiet.
    // The child keeps running until `still_running` is set to false.
    #[derive(Debug)]
    struct CodexToolStartExecutor {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::AgentChild for CodexToolStartChild {
        fn id(&self) -> u32 {
            77777
        }

        fn wait(&mut self) -> io::Result<ExitStatus> {
            while self.still_running.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(10));
            }
            Ok(ExitStatus::from_raw(0))
        }

        fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
            if self.still_running.load(Ordering::Acquire) {
                return Ok(None);
            }
            Ok(Some(ExitStatus::from_raw(0)))
        }
    }

    #[derive(Debug)]
    struct CodexToolStartChild {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::ProcessExecutor for CodexToolStartExecutor {
        fn execute(
            &self,
            command: &str,
            args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            if command == "kill" && args.contains(&"-KILL") {
                self.still_running.store(false, Ordering::Release);
            }
            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }

        fn spawn_agent(
            &self,
            _config: &crate::executor::AgentSpawnConfig,
        ) -> io::Result<crate::executor::AgentChildHandle> {
            // Emit a single Codex item.started JSON event on stdout — this causes the Codex
            // parser to set tool_active = true via apply_tool_activity_for_event().
            // After this line, stdout closes (EOF). The child keeps running so the monitor
            // can observe the tool_active flag before the process exits.
            let codex_item_started = b"{\"type\":\"item.started\",\"item\":{\"type\":\"file_write\",\"path\":\"/tmp/test.xml\"}}\n";
            let stdout =
                Box::new(Cursor::new(codex_item_started.to_vec())) as Box<dyn io::Read + Send>;
            let stderr = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let inner: Box<dyn crate::executor::AgentChild> = Box::new(CodexToolStartChild {
                still_running: Arc::clone(&self.still_running),
            });
            Ok(crate::executor::AgentChildHandle {
                stdout,
                stderr,
                inner,
            })
        }
    }

    let workspace = MemoryWorkspace::new_test();
    let logger = test_logger();
    let colors = Colors::new();
    let config = Config::test_default();
    let mut timer = Timer::new();

    let still_running = Arc::new(AtomicBool::new(true));
    let executor = Arc::new(CodexToolStartExecutor {
        still_running: Arc::clone(&still_running),
    });
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let env_vars: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let cmd = PromptCommand {
        label: "test",
        display_name: "test",
        cmd_str: "mock-agent",
        prompt: "hello",
        log_prefix: ".agent/logs/test",
        model_index: None,
        attempt: None,
        logfile: ".agent/logs/test.log",
        // Use Codex parser so item.started events are processed and update the tool-activity flag.
        parser_type: JsonParserType::Codex,
        env_vars: &env_vars,
        completion_output_path: None,
    };

    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor.as_ref(),
        executor_arc,
        workspace: &workspace,
        workspace_arc: Arc::new(workspace.clone()),
    };

    // Stop the child shortly after the parser has had time to process the item.started event
    // and set tool_active = true. With the tool-activity suppressor engaged, the idle monitor
    // (timeout = ZERO) should not kill the process.
    let still_running_for_stopper = Arc::clone(&still_running);
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(200));
        still_running_for_stopper.store(false, Ordering::Release);
    });

    let result = run_with_agent_spawn_with_monitor_config(
        &cmd,
        &runtime,
        &[],
        // Idle timeout is ZERO — fires immediately unless suppressed.
        Duration::ZERO,
        Duration::from_millis(20),
        crate::pipeline::idle_timeout::KillConfig::new(
            Duration::from_millis(20),
            Duration::from_millis(1),
            Duration::from_millis(20),
            Duration::from_millis(100),
            Duration::from_millis(20),
        ),
    )
    .expect("expected successful CommandResult");

    // The Codex item.started event should have suppressed the idle timeout.
    // Child exits cleanly → result should be exit code 0, NOT SIGTERM (143).
    assert_ne!(
        result.exit_code,
        super::SIGTERM_EXIT_CODE,
        "Codex item.started event must suppress idle timeout; child should exit cleanly, not be killed"
    );
    assert_eq!(
        result.exit_code, 0,
        "child should exit with code 0 after item.started suppresses idle timeout"
    );
}

/// Bug 3 regression: Once the tool-activity suppressor is cleared (item.completed or
/// turn.completed received), the idle-timeout monitor resumes normal enforcement.
///
/// The test verifies:
///   item.started sets tool_active = true → timeout suppressed during write
///   item.completed clears tool_active = false → timeout fires when child is still running and quiet
#[test]
#[cfg(unix)]
fn test_codex_item_completed_clears_suppressor_and_timeout_fires() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::Arc;
    use std::time::Duration;

    use std::os::unix::process::ExitStatusExt;

    #[derive(Debug)]
    struct CodexToolCompleteExecutor {
        still_running: Arc<AtomicBool>,
    }

    #[derive(Debug)]
    struct CodexToolCompleteChild {
        still_running: Arc<AtomicBool>,
    }

    impl crate::executor::AgentChild for CodexToolCompleteChild {
        fn id(&self) -> u32 {
            88888
        }

        fn wait(&mut self) -> io::Result<ExitStatus> {
            while self.still_running.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(5));
            }
            Ok(ExitStatus::from_raw(0))
        }

        fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
            if self.still_running.load(Ordering::Acquire) {
                Ok(None)
            } else {
                Ok(Some(ExitStatus::from_raw(0)))
            }
        }
    }

    impl crate::executor::ProcessExecutor for CodexToolCompleteExecutor {
        fn execute(
            &self,
            command: &str,
            _args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            if command == "kill" {
                self.still_running.store(false, Ordering::Release);
            }
            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }

        fn spawn_agent(
            &self,
            _config: &crate::executor::AgentSpawnConfig,
        ) -> io::Result<crate::executor::AgentChildHandle> {
            // Emit item.started then immediately item.completed so the tool_active flag
            // is set and then cleared. After stdout closes, the monitor should time out
            // because there is no active tool, no file activity, and no output.
            let events = concat!(
                "{\"type\":\"item.started\",\"item\":{\"type\":\"file_write\",\"path\":\"/tmp/out.xml\"}}\n",
                "{\"type\":\"item.completed\",\"item\":{\"type\":\"file_write\",\"path\":\"/tmp/out.xml\"}}\n",
            );
            let stdout =
                Box::new(Cursor::new(events.as_bytes().to_vec())) as Box<dyn io::Read + Send>;
            let stderr = Box::new(Cursor::new(Vec::<u8>::new())) as Box<dyn io::Read + Send>;
            let inner: Box<dyn crate::executor::AgentChild> = Box::new(CodexToolCompleteChild {
                still_running: Arc::clone(&self.still_running),
            });
            Ok(crate::executor::AgentChildHandle {
                stdout,
                stderr,
                inner,
            })
        }
    }

    let workspace = MemoryWorkspace::new_test();
    let logger = test_logger();
    let colors = Colors::new();
    let config = Config::test_default();
    let mut timer = Timer::new();

    let still_running = Arc::new(AtomicBool::new(true));
    let executor = Arc::new(CodexToolCompleteExecutor {
        still_running: Arc::clone(&still_running),
    });
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let env_vars: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let cmd = PromptCommand {
        label: "test",
        display_name: "test",
        cmd_str: "mock-agent",
        prompt: "hello",
        log_prefix: ".agent/logs/test",
        model_index: None,
        attempt: None,
        logfile: ".agent/logs/test.log",
        parser_type: JsonParserType::Codex,
        env_vars: &env_vars,
        completion_output_path: None,
    };

    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor.as_ref(),
        executor_arc,
        workspace: &workspace,
        workspace_arc: Arc::new(workspace.clone()),
    };

    let result = run_with_agent_spawn_with_monitor_config(
        &cmd,
        &runtime,
        &[],
        // Idle timeout is ZERO — fires as soon as tool_active is cleared.
        Duration::ZERO,
        Duration::from_millis(5),
        crate::pipeline::idle_timeout::KillConfig::new(
            Duration::from_millis(10),
            Duration::from_millis(1),
            Duration::from_millis(10),
            Duration::from_millis(50),
            Duration::from_millis(10),
        ),
    )
    .expect("expected successful CommandResult");

    // After item.completed clears the suppressor, the idle timeout should fire and kill
    // the still-running child via SIGTERM (exit code 143).
    assert_eq!(
        result.exit_code,
        super::SIGTERM_EXIT_CODE,
        "after item.completed clears tool_active, idle timeout must fire and kill the child"
    );
}
