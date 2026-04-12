/// Shared test infrastructure for handler tests.
///
/// Provides `TestFixture` to eliminate `PhaseContext` construction
/// boilerplate across handler test modules.
use crate::agents::session::AuditTrail;
use crate::agents::AgentRegistry;
use crate::checkpoint::execution_history::ExecutionHistory;
use crate::checkpoint::RunContext;
use crate::config::Config;
use crate::executor::MockProcessExecutor;
use crate::logger::{Colors, Logger};
use crate::pipeline::Timer;
use crate::prompts::template_context::TemplateContext;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::PathBuf;
use std::sync::Arc;

/// Holds all owned data needed to construct a `PhaseContext`.
///
/// Call `ctx()` to get a `PhaseContext` that borrows from this struct.
/// For tests that need a custom workspace type (e.g. `ReadFailingWorkspace`),
/// use `with_dyn_workspace()` to supply a trait-object workspace.
pub(super) struct TestFixture {
    pub config: Config,
    pub registry: AgentRegistry,
    pub colors: Colors,
    pub logger: Logger,
    pub timer: Timer,
    pub template_context: TemplateContext,
    pub executor: Arc<MockProcessExecutor>,
    pub workspace: MemoryWorkspace,
    pub workspace_arc: Arc<dyn crate::workspace::Workspace>,
    pub repo_root: PathBuf,
    pub run_log_context: crate::logging::RunLogContext,
    pub cloud: crate::config::types::CloudConfig,
    pub git_env: crate::runtime::environment::mock::MockGitEnvironment,
}

impl TestFixture {
    /// Creates a fixture with default test values and a blank `MemoryWorkspace`.
    pub(super) fn new() -> Self {
        Self::with_workspace(MemoryWorkspace::new_test())
    }

    /// Creates a fixture with the given workspace.
    pub(super) fn with_workspace(workspace: MemoryWorkspace) -> Self {
        let workspace_arc = Arc::new(workspace.clone()) as Arc<dyn crate::workspace::Workspace>;
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
        Self {
            config: Config::default(),
            registry: AgentRegistry::new().unwrap(),
            colors,
            logger,
            timer: Timer::new(),
            template_context: TemplateContext::default(),
            executor: Arc::new(MockProcessExecutor::new()),
            workspace,
            workspace_arc,
            repo_root: PathBuf::from("/mock/repo"),
            run_log_context,
            cloud: crate::config::types::CloudConfig::disabled(),
            git_env: crate::runtime::environment::mock::MockGitEnvironment::new(),
        }
    }

    /// Builds a `PhaseContext` whose `workspace` field points to a custom
    /// trait-object workspace (e.g. an error-injecting wrapper) instead of
    /// the fixture's owned `MemoryWorkspace`.
    pub(super) fn ctx_with_workspace<'a>(
        &'a mut self,
        workspace: &'a dyn crate::workspace::Workspace,
    ) -> crate::phases::PhaseContext<'a> {
        crate::phases::PhaseContext {
            config: &self.config,
            registry: &self.registry,
            logger: &self.logger,
            colors: &self.colors,
            timer: &mut self.timer,
            developer_agent: "dev",
            reviewer_agent: "rev",
            review_guidelines: None,
            template_context: &self.template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: self.executor.as_ref(),
            executor_arc: Arc::clone(&self.executor) as Arc<dyn crate::executor::ProcessExecutor>,
            repo_root: self.repo_root.as_path(),
            workspace,
            workspace_arc: Arc::clone(&self.workspace_arc),
            run_log_context: &self.run_log_context,
            cloud_reporter: None,
            cloud: &self.cloud,
            env: &self.git_env,
            active_session: None,
            audit_trail: AuditTrail::new(),
        }
    }

    /// Builds a `PhaseContext` that borrows from this fixture.
    pub(super) fn ctx(&mut self) -> crate::phases::PhaseContext<'_> {
        crate::phases::PhaseContext {
            config: &self.config,
            registry: &self.registry,
            logger: &self.logger,
            colors: &self.colors,
            timer: &mut self.timer,
            developer_agent: "dev",
            reviewer_agent: "rev",
            review_guidelines: None,
            template_context: &self.template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: self.executor.as_ref(),
            executor_arc: Arc::clone(&self.executor) as Arc<dyn crate::executor::ProcessExecutor>,
            repo_root: self.repo_root.as_path(),
            workspace: &self.workspace,
            workspace_arc: Arc::clone(&self.workspace_arc),
            run_log_context: &self.run_log_context,
            cloud_reporter: None,
            cloud: &self.cloud,
            env: &self.git_env,
            active_session: None,
            audit_trail: AuditTrail::new(),
        }
    }
}

#[test]
fn test_fixture_produces_valid_context() {
    let mut fixture = TestFixture::new();
    // Verify the workspace is accessible for test assertions.
    assert_eq!(fixture.workspace.root(), std::path::Path::new("/test/repo"));
    let ctx = fixture.ctx();
    assert_eq!(ctx.developer_agent, "dev");
    assert_eq!(ctx.reviewer_agent, "rev");
    assert!(ctx.review_guidelines.is_none());
    assert!(ctx.cloud_reporter.is_none());
}
