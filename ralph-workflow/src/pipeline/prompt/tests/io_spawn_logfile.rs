use super::*;
use crate::executor::MockProcessExecutor;
use std::path::Path;
use std::sync::Arc;

#[test]
fn test_run_with_agent_spawn_creates_parent_directory_for_logfile() {
    #[derive(Debug, Clone)]
    struct StrictLogsWorkspace {
        inner: MemoryWorkspace,
        logs_created: Arc<AtomicBool>,
    }

    impl StrictLogsWorkspace {
        fn new(inner: MemoryWorkspace) -> Self {
            Self {
                inner,
                logs_created: Arc::new(AtomicBool::new(false)),
            }
        }
    }

    impl Workspace for StrictLogsWorkspace {
        fn root(&self) -> &Path {
            self.inner.root()
        }

        fn read(&self, relative: &Path) -> io::Result<String> {
            self.inner.read(relative)
        }

        fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
            self.inner.read_bytes(relative)
        }

        fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
            if relative == Path::new(".agent/logs/test.log")
                && !self.logs_created.load(Ordering::Acquire)
            {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    "parent dir missing (strict workspace)",
                ));
            }
            self.inner.write(relative, content)
        }

        fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.write_bytes(relative, content)
        }

        fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            if relative == Path::new(".agent/logs/test.log")
                && !self.logs_created.load(Ordering::Acquire)
            {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    "parent dir missing (strict workspace)",
                ));
            }
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

        fn remove(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove(relative)
        }

        fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_if_exists(relative)
        }

        fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all(relative)
        }

        fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all_if_exists(relative)
        }

        fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
            if relative == Path::new(".agent/logs") {
                self.logs_created.store(true, Ordering::Release);
            }
            self.inner.create_dir_all(relative)
        }

        fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
            self.inner.read_dir(relative)
        }

        fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
            self.inner.rename(from, to)
        }

        fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write_atomic(relative, content)
        }

        fn set_readonly(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_readonly(relative)
        }

        fn set_writable(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_writable(relative)
        }
    }

    let workspace = StrictLogsWorkspace::new(MemoryWorkspace::new_test());
    let logger = test_logger();
    let colors = Colors::new();
    let config = Config::default();
    let mut timer = Timer::new();

    let executor: Arc<MockProcessExecutor> = Arc::new(MockProcessExecutor::new());
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

    let result = run_with_agent_spawn(&cmd, &runtime, &[]);
    assert!(result.is_ok(), "expected agent run to succeed");

    let content = workspace
        .read(std::path::Path::new(cmd.logfile))
        .expect("logfile should be created");
    assert!(!content.is_empty(), "logfile should contain agent output");
}
