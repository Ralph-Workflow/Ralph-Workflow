//! Shared test fixtures and helpers for failure completion marker tests.
//!
//! This module provides common infrastructure used across all failure completion
//! marker tests, including:
//! - `Fixture` - Standard test setup with workspace, logger, and context
//! - `FailingWorkspace` - Workspace that simulates I/O failures
//! - `StalledAwaitingDevFixHandler` - Mock handler for testing timeout behavior

use ralph_workflow::agents::AgentRegistry;
use ralph_workflow::checkpoint::{ExecutionHistory, RunContext};
use ralph_workflow::config::Config;
use ralph_workflow::executor::MockProcessExecutor;
use ralph_workflow::logger::{Colors, Logger};
use ralph_workflow::pipeline::Timer;
use ralph_workflow::prompts::template_context::TemplateContext;
use ralph_workflow::reducer::effect::{Effect, EffectResult};
use ralph_workflow::reducer::event::{ErrorEvent, PipelineEvent};
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::runtime::environment::mock::MockGitEnvironment;
use ralph_workflow::workspace::{MemoryWorkspace, Workspace};
use std::io;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Standard test fixture with workspace, logger, and phase context.
pub struct Fixture {
    pub config: Config,
    pub colors: Colors,
    pub logger: Logger,
    pub timer: Timer,
    pub template_context: TemplateContext,
    pub registry: AgentRegistry,
    pub executor: Arc<MockProcessExecutor>,
    pub repo_root: PathBuf,
    pub workspace: Arc<dyn Workspace>,
    pub run_log_context: ralph_workflow::logging::RunLogContext,
    pub cloud: ralph_workflow::config::CloudConfig,
    pub git_env: MockGitEnvironment,
}

impl Fixture {
    /// Creates a new fixture with a memory-backed workspace.
    pub fn new() -> Self {
        let repo_root = PathBuf::from("/test/repo");
        let workspace: Arc<dyn Workspace> = Arc::new(MemoryWorkspace::new(repo_root));
        Self::with_workspace(workspace)
    }

    /// Creates a fixture using a custom workspace implementation.
    pub fn with_workspace(workspace: Arc<dyn Workspace>) -> Self {
        let config = Config::default();
        let colors = Colors::new();
        let repo_root = workspace.root().to_path_buf();
        let logger = Logger::new(colors);
        let registry = AgentRegistry::new().unwrap();
        let executor = Arc::new(MockProcessExecutor::new());
        let run_log_context = ralph_workflow::logging::RunLogContext::new(workspace.as_ref())
            .expect("Failed to create run log context");

        Self {
            config,
            colors,
            logger,
            timer: Timer::new(),
            template_context: TemplateContext::default(),
            registry,
            executor,
            repo_root,
            workspace,
            run_log_context,
            cloud: ralph_workflow::config::CloudConfig::disabled(),
            git_env: MockGitEnvironment::new(),
        }
    }

    /// Creates a phase context for use in effect handlers.
    pub fn ctx(&mut self) -> ralph_workflow::phases::PhaseContext<'_> {
        ralph_workflow::phases::PhaseContext {
            config: &self.config,
            registry: &self.registry,
            logger: &self.logger,
            colors: &self.colors,
            timer: &mut self.timer,
            developer_agent: "test-developer",
            reviewer_agent: "test-reviewer",
            review_guidelines: None,
            template_context: &self.template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: &*self.executor,
            executor_arc: Arc::clone(&self.executor)
                as Arc<dyn ralph_workflow::executor::ProcessExecutor>,
            repo_root: &self.repo_root,
            workspace: self.workspace.as_ref(),
            workspace_arc: Arc::clone(&self.workspace)
                as Arc<dyn ralph_workflow::workspace::Workspace>,
            run_log_context: &self.run_log_context,
            cloud_reporter: None,
            cloud: &self.cloud,
            env: &self.git_env,
        }
    }
}

/// Workspace that simulates I/O failures for testing error handling.
///
/// Wraps a `MemoryWorkspace` and selectively fails operations based on
/// configuration. Used to test error recovery paths in the event loop.
#[derive(Debug)]
pub struct FailingWorkspace {
    inner: MemoryWorkspace,
    fail_marker_write: bool,
}

impl FailingWorkspace {
    /// Creates a failing workspace.
    ///
    /// # Arguments
    ///
    /// * `inner` - The underlying memory workspace
    /// * `fail_marker_write` - If true, writes to `.agent/tmp/completion_marker` will fail
    pub const fn new(inner: MemoryWorkspace, fail_marker_write: bool) -> Self {
        Self {
            inner,
            fail_marker_write,
        }
    }

    fn should_fail_marker_write(&self, path: &Path) -> bool {
        self.fail_marker_write && path == Path::new(".agent/tmp/completion_marker")
    }
}

impl Workspace for FailingWorkspace {
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
        if self.should_fail_marker_write(relative) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "simulated completion marker write failure",
            ));
        }
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        if self.should_fail_marker_write(relative) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "simulated completion marker write failure",
            ));
        }
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
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
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<ralph_workflow::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        if self.should_fail_marker_write(relative) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "simulated completion marker write failure",
            ));
        }
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_writable(relative)
    }
}

/// Behavior for `SaveCheckpoint` effect in mock handler.
#[derive(Debug, Clone, Copy)]
pub enum SaveBehavior {
    /// `SaveCheckpoint` succeeds
    Ok,
    /// `SaveCheckpoint` returns error event
    ErrorEvent,
    /// `SaveCheckpoint` panics
    Panic,
}

/// Mock handler that simulates being stuck in `AwaitingDevFix` phase.
///
/// Used to test max iteration handling and forced completion logic.
/// This handler responds to `TriggerDevFixFlow` but never advances past
/// `AwaitingDevFix`, allowing tests to exercise timeout behavior.
#[derive(Debug)]
pub struct StalledAwaitingDevFixHandler {
    pub state: PipelineState,
    save_behavior: SaveBehavior,
    pub save_attempts: usize,
}

impl StalledAwaitingDevFixHandler {
    /// Creates a new stalled handler.
    ///
    /// # Arguments
    ///
    /// * `state` - Initial pipeline state
    /// * `save_behavior` - How `SaveCheckpoint` effect should behave
    pub const fn new(state: PipelineState, save_behavior: SaveBehavior) -> Self {
        Self {
            state,
            save_behavior,
            save_attempts: 0,
        }
    }
}

impl ralph_workflow::reducer::effect::EffectHandler<'_> for StalledAwaitingDevFixHandler {
    fn execute(
        &mut self,
        effect: Effect,
        _ctx: &mut ralph_workflow::phases::PhaseContext<'_>,
    ) -> anyhow::Result<EffectResult> {
        match effect {
            Effect::TriggerDevFixFlow {
                failed_phase,
                failed_role,
                ..
            } => Ok(EffectResult::event(PipelineEvent::AwaitingDevFix(
                ralph_workflow::reducer::event::AwaitingDevFixEvent::DevFixTriggered {
                    failed_phase,
                    failed_role,
                },
            ))),
            Effect::SaveCheckpoint { trigger } => {
                self.save_attempts += 1;
                match self.save_behavior {
                    SaveBehavior::Ok => Ok(EffectResult::event(PipelineEvent::checkpoint_saved(
                        trigger,
                    ))),
                    SaveBehavior::ErrorEvent => Err(ErrorEvent::WorkspaceWriteFailed {
                        path: ".agent/checkpoint.json".to_string(),
                        kind: ralph_workflow::reducer::event::WorkspaceIoErrorKind::Other,
                    }
                    .into()),
                    SaveBehavior::Panic => panic!("simulated SaveCheckpoint panic"),
                }
            }
            other => Err(anyhow::anyhow!("unexpected effect: {other:?}")),
        }
    }
}

impl ralph_workflow::app::event_loop::StatefulHandler for StalledAwaitingDevFixHandler {
    fn update_state(&mut self, state: PipelineState) {
        self.state = state;
    }
}
