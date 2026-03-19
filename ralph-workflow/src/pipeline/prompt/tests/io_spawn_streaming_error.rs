use super::*;

#[test]
#[cfg(unix)]
fn test_run_with_agent_spawn_terminates_child_and_joins_threads_when_streaming_errors() {
    use std::path::Path;
    use std::process::ExitStatus;
    use std::sync::atomic::AtomicBool;
    use std::sync::{mpsc, Arc, Mutex};
    use std::time::{Duration, Instant};

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
    struct FailingReader {
        failed: bool,
    }

    impl FailingReader {
        fn new() -> Self {
            Self { failed: false }
        }
    }

    impl io::Read for FailingReader {
        fn read(&mut self, _buf: &mut [u8]) -> io::Result<usize> {
            if self.failed {
                return Ok(0);
            }
            self.failed = true;
            Err(io::Error::other("boom"))
        }
    }

    #[derive(Debug)]
    struct StreamingErrorExecutor {
        start: Instant,
        still_running: Arc<AtomicBool>,
        kill_calls_at: Arc<Mutex<Vec<Duration>>>,
    }

    impl crate::executor::ProcessExecutor for StreamingErrorExecutor {
        fn execute(
            &self,
            command: &str,
            args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            if command == "kill" {
                self.kill_calls_at
                    .lock()
                    .unwrap()
                    .push(self.start.elapsed());
                if args.contains(&"-KILL") {
                    self.still_running.store(false, Ordering::Release);
                }
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
            let stdout = Box::new(FailingReader::new()) as Box<dyn io::Read + Send>;
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

    let start = Instant::now();
    let still_running = Arc::new(AtomicBool::new(true));
    let kill_calls_at = Arc::new(Mutex::new(Vec::<Duration>::new()));
    let executor = Arc::new(StreamingErrorExecutor {
        start,
        still_running: Arc::clone(&still_running),
        kill_calls_at: Arc::clone(&kill_calls_at),
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
                Duration::from_secs(1),
                Duration::from_millis(10),
                crate::pipeline::idle_timeout::KillConfig::new(
                    Duration::from_millis(20),
                    Duration::from_millis(1),
                    Duration::from_millis(20),
                    Duration::from_secs(2),
                    Duration::from_millis(50),
                ),
            );
            let returned_at = start.elapsed();
            let _ = tx.send((returned_at, result));
        });

        let (returned_at, result) = rx
            .recv_timeout(Duration::from_secs(10))
            .expect("expected run to return promptly");
        assert!(result.is_err(), "expected streaming to fail");

        std::thread::sleep(Duration::from_millis(50));
        still_running.store(false, Ordering::Release);

        let kill_times = kill_calls_at.lock().unwrap().clone();
        assert!(
            !kill_times.is_empty(),
            "expected the child to be terminated via kill commands"
        );

        for t in kill_times {
            assert!(
                t <= returned_at + Duration::from_millis(100),
                "observed kill call at {t:?} after return at {returned_at:?}"
            );
        }
    });
}
