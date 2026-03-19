//! Phase execution context.
//!
//! This module defines the shared context that is passed to each phase
//! of the pipeline. It contains references to configuration, registry,
//! logging utilities, and runtime state that all phases need access to.

use crate::agents::{AgentDrain, AgentRegistry};
use crate::checkpoint::execution_history::ExecutionHistory;
use crate::checkpoint::RunContext;
use crate::config::Config;
use crate::executor::ProcessExecutor;
use crate::guidelines::ReviewGuidelines;
use crate::logger::{Colors, Logger};
use crate::logging::RunLogContext;
use crate::pipeline::Timer;
use crate::prompts::template_context::TemplateContext;
use crate::runtime::environment::GitEnvironment;
use crate::workspace::Workspace;
// MemoryWorkspace is used in test fixtures for proper DI
#[cfg(test)]
use crate::workspace::MemoryWorkspace;
use std::path::Path;

/// Shared context for all pipeline phases.
///
/// This struct holds references to all the shared state that phases need
/// to access. It is passed by mutable reference to each phase function.
///
/// # Phase Name Convention
///
/// When working with phase names (e.g., for log file naming), use **lowercase**
/// identifiers with underscores for multi-word phases. The canonical phase names are:
/// - `"planning"` - Planning phase
/// - `"analysis"` - Analysis sub-phase of development (when role == `AgentRole::Analysis`)
/// - `"developer"` - Development phase (when role == `AgentRole::Developer`)
/// - `"reviewer"` - Review phase
/// - `"commit"` - Commit message generation phase
/// - `"final_validation"` - Final validation phase
/// - `"finalizing"` - Finalizing phase
/// - `"complete"` - Complete phase
/// - `"awaiting_dev_fix"` - Awaiting dev fix phase
/// - `"interrupted"` - Interrupted phase
///
/// These phase names are used for log file naming under `.agent/logs-<run_id>/agents/`
/// (e.g., `planning_1.log`, `developer_2_a1.log`).
///
/// When adding new phases or extending the phase system, maintain this lowercase
/// convention for consistency.
pub struct PhaseContext<'a> {
    /// Configuration settings for the pipeline.
    pub config: &'a Config,
    /// Agent registry for looking up agent configurations.
    pub registry: &'a AgentRegistry,
    /// Logger for output and diagnostics.
    pub logger: &'a Logger,
    /// Terminal color configuration.
    pub colors: &'a Colors,
    /// Timer for tracking elapsed time.
    pub timer: &'a mut Timer,
    /// Name of the developer agent.
    pub developer_agent: &'a str,
    /// Name of the reviewer agent.
    pub reviewer_agent: &'a str,
    /// Review guidelines based on detected project stack.
    pub review_guidelines: Option<&'a ReviewGuidelines>,
    /// Template context for loading user templates.
    pub template_context: &'a TemplateContext,
    /// Run context for tracking execution lineage and state.
    pub run_context: RunContext,
    /// Execution history for tracking pipeline steps.
    pub execution_history: ExecutionHistory,
    /// Process executor for external process execution.
    pub executor: &'a dyn ProcessExecutor,
    /// Arc-wrapped executor for spawning into threads (e.g., idle timeout monitor).
    pub executor_arc: std::sync::Arc<dyn ProcessExecutor>,
    /// Repository root path for explicit file operations.
    ///
    /// This eliminates CWD dependencies by providing an explicit path for all
    /// file operations. Code should use `repo_root.join("relative/path")` instead
    /// of `Path::new("relative/path")`.
    pub repo_root: &'a Path,
    /// Workspace for explicit path resolution and file operations.
    ///
    /// Provides convenient methods for file operations and path resolution
    /// without depending on the current working directory.
    ///
    /// This uses trait object (`&dyn Workspace`) for proper dependency injection:
    /// - Production code passes `&WorkspaceFs` (real filesystem)
    /// - Tests can pass `&MemoryWorkspace` (in-memory storage)
    pub workspace: &'a dyn Workspace,
    /// Arc-wrapped workspace for spawning into threads (e.g., file activity monitor).
    pub workspace_arc: std::sync::Arc<dyn Workspace>,
    /// Run log context for per-run log path resolution.
    ///
    /// Provides paths to all log files under the per-run directory
    /// (`.agent/logs-<run_id>/`). This ensures all logs from a single
    /// pipeline invocation are grouped together for easy debugging.
    pub run_log_context: &'a RunLogContext,
    /// Cloud reporter for progress updates (None in CLI mode).
    ///
    /// When cloud mode is disabled, this is None and no cloud reporting occurs.
    /// When enabled, this is Some(&dyn `CloudReporter`) for API communication.
    pub cloud_reporter: Option<&'a dyn crate::cloud::CloudReporter>,
    /// Cloud configuration.
    ///
    /// When cloud mode is disabled (enabled=false), all cloud-specific
    /// logic is skipped throughout the pipeline.
    pub cloud: &'a crate::config::types::CloudConfig,
    /// Git environment for configuring authentication variables.
    ///
    /// Used by cloud handlers to configure GIT_SSH_COMMAND and GIT_TERMINAL_PROMPT
    /// without calling `std::env::set_var` directly.
    pub env: &'a dyn GitEnvironment,
}

impl PhaseContext<'_> {
    /// Record a completed developer iteration.
    pub fn record_developer_iteration(&mut self) {
        self.run_context = self.run_context.clone().record_developer_iteration();
    }

    /// Record a completed reviewer pass.
    pub fn record_reviewer_pass(&mut self) {
        self.run_context = self.run_context.clone().record_reviewer_pass();
    }
}

/// Get the primary commit agent from the registry.
///
/// This function returns the name of the primary commit agent.
/// If a commit-specific agent is configured, it uses that. Otherwise, it falls back
/// to using the reviewer chain (since commit generation is typically done after review).
#[must_use]
pub fn get_primary_commit_agent(ctx: &PhaseContext<'_>) -> Option<String> {
    if let Some(commit_binding) = ctx.registry.resolved_drain(AgentDrain::Commit) {
        return commit_binding.agents.first().cloned();
    }

    // Fallback to using reviewer agents for commit generation
    let reviewer_agents = ctx
        .registry
        .resolved_drain(AgentDrain::Review)
        .map_or(&[] as &[String], |binding| binding.agents.as_slice());
    if !reviewer_agents.is_empty() {
        return reviewer_agents.first().cloned();
    }

    // Last resort: use the current reviewer agent
    Some(ctx.reviewer_agent.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use std::path::PathBuf;

    /// Test fixture for creating `PhaseContext` in tests.
    ///
    /// Uses `MemoryWorkspace` instead of `WorkspaceFs` for proper dependency injection.
    /// This allows tests to run without touching the real filesystem.
    struct TestFixture {
        config: Config,
        colors: Colors,
        logger: Logger,
        timer: Timer,
        template_context: TemplateContext,
        executor_arc: std::sync::Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: PathBuf,
        workspace: MemoryWorkspace,
        workspace_arc: std::sync::Arc<dyn Workspace>,
        run_log_context: crate::logging::RunLogContext,
    }

    impl TestFixture {
        fn new() -> Self {
            let colors = Colors { enabled: false };
            let executor_arc = std::sync::Arc::new(MockProcessExecutor::new())
                as std::sync::Arc<dyn crate::executor::ProcessExecutor>;
            let repo_root = PathBuf::from("/test/repo");
            // Use MemoryWorkspace for testing - no real filesystem access
            let workspace = MemoryWorkspace::new(repo_root.clone());
            let workspace_arc =
                std::sync::Arc::new(workspace.clone()) as std::sync::Arc<dyn Workspace>;
            let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
            Self {
                config: Config::default(),
                colors,
                logger: Logger::new(colors),
                timer: Timer::new(),
                template_context: TemplateContext::default(),
                executor_arc,
                repo_root,
                workspace,
                workspace_arc,
                run_log_context,
            }
        }
    }

    #[test]
    fn test_get_primary_commit_agent_uses_commit_chain_first() {
        let toml_str = r#"
            [agent_chain]
            commit = ["commit-agent-1", "commit-agent-2"]
            reviewer = ["reviewer-agent"]
            developer = ["developer-agent"]
        "#;
        let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();
        let registry = AgentRegistry::new()
            .unwrap()
            .apply_unified_config(&unified)
            .unwrap();

        let fixture = TestFixture::new();
        let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
        let ctx = PhaseContext {
            config: &fixture.config,
            registry: &registry,
            logger: &fixture.logger,
            colors: &fixture.colors,
            timer: &mut fixture.timer,
            developer_agent: "developer-agent",
            reviewer_agent: "reviewer-agent",
            review_guidelines: None,
            template_context: &fixture.template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: fixture.executor_arc.as_ref(),
            executor_arc: std::sync::Arc::clone(&fixture.executor_arc),
            repo_root: &fixture.repo_root,
            workspace: &fixture.workspace,
            workspace_arc: std::sync::Arc::clone(&fixture.workspace_arc),
            run_log_context: &fixture.run_log_context,
            cloud_reporter: None,
            cloud: &crate::config::types::CloudConfig::disabled(),
            env: &git_env,
        };

        let result = get_primary_commit_agent(&ctx);
        assert_eq!(
            result,
            Some("commit-agent-1".to_string()),
            "Should use first agent from commit chain when configured"
        );
    }

    #[test]
    fn test_get_primary_commit_agent_falls_back_to_reviewer_chain() {
        let toml_str = r#"
            [agent_chain]
            reviewer = ["reviewer-agent-1", "reviewer-agent-2"]
            developer = ["developer-agent"]
        "#;
        let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();
        let registry = AgentRegistry::new()
            .unwrap()
            .apply_unified_config(&unified)
            .unwrap();

        let fixture = TestFixture::new();
        let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
        let ctx = PhaseContext {
            config: &fixture.config,
            registry: &registry,
            logger: &fixture.logger,
            colors: &fixture.colors,
            timer: &mut fixture.timer,
            developer_agent: "developer-agent",
            reviewer_agent: "reviewer-agent-1",
            review_guidelines: None,
            template_context: &fixture.template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: fixture.executor_arc.as_ref(),
            executor_arc: std::sync::Arc::clone(&fixture.executor_arc),
            repo_root: &fixture.repo_root,
            workspace: &fixture.workspace,
            workspace_arc: std::sync::Arc::clone(&fixture.workspace_arc),
            run_log_context: &fixture.run_log_context,
            cloud_reporter: None,
            cloud: &crate::config::types::CloudConfig::disabled(),
            env: &git_env,
        };

        let result = get_primary_commit_agent(&ctx);
        assert_eq!(
            result,
            Some("reviewer-agent-1".to_string()),
            "Should fall back to first agent from reviewer chain when commit chain is not configured"
        );
    }

    #[test]
    fn test_get_primary_commit_agent_uses_context_reviewer_as_last_resort() {
        let registry = AgentRegistry::new().unwrap();
        // Default registry with no custom chains configured

        let fixture = TestFixture::new();
        let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
        let ctx = PhaseContext {
            config: &fixture.config,
            registry: &registry,
            logger: &fixture.logger,
            colors: &fixture.colors,
            timer: &mut fixture.timer,
            developer_agent: "fallback-developer",
            reviewer_agent: "fallback-reviewer",
            review_guidelines: None,
            template_context: &fixture.template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: fixture.executor_arc.as_ref(),
            executor_arc: std::sync::Arc::clone(&fixture.executor_arc),
            repo_root: &fixture.repo_root,
            workspace: &fixture.workspace,
            workspace_arc: std::sync::Arc::clone(&fixture.workspace_arc),
            run_log_context: &fixture.run_log_context,
            cloud_reporter: None,
            cloud: &crate::config::types::CloudConfig::disabled(),
            env: &git_env,
        };

        let result = get_primary_commit_agent(&ctx);

        // When no chains are configured, it should fall back to the context's reviewer_agent
        // OR the default reviewer from the registry (if it has a default)
        // The key point is it should NOT use developer agent
        assert!(
            result.is_some(),
            "Should return Some agent even with no chains configured"
        );

        // Verify it's not using the developer agent
        assert_ne!(
            result.as_deref(),
            Some("fallback-developer"),
            "Should NOT fall back to developer agent - should use reviewer"
        );
    }
}
