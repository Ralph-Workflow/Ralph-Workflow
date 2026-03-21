// Lint policy: fix code to match the style guide rather than weakening the lint.
//
// See `CODE_STYLE.md`, `docs/code-style/boundaries.md`,
// `docs/code-style/coding-patterns.md`, and `docs/code-style/testing.md`.
//
// This binary is a CLI boundary, so the crate root keeps only the rules that are
// universally correct for entrypoint code. Boundary-sensitive rules stay documented
// in the library and in dylint.
//
// `clippy::cargo` stays off because it reports dependency graph conflicts that are
// not actionable style-guide violations.
#![deny(warnings)]
#![deny(clippy::all)]
#![forbid(unsafe_code)]
#![deny(
    // No explicit iterator loops when a more idiomatic form exists
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    // Keep entrypoint code free of accidental stdout logging and debug leftovers,
    // but do not over-apply domain-only restrictions to the CLI boundary.
    clippy::print_stdout,
    clippy::dbg_macro,
    // Push toward combinators instead of hand-written control flow
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    clippy::needless_collect
)]
//! Ralph: PROMPT-driven agent loop for git repos
//!
//! Runs:
//! - Developer agent: iterative progress against PROMPT.md
//! - Reviewer agent: review → fix → review passes
//! - Optional fast/full checks
//! - Final `git add -A` + `git commit -m <msg>`

use clap::Parser;
use ralph_workflow::app;
use ralph_workflow::cli::Args;
use ralph_workflow::exit_pause;
use ralph_workflow::interrupt;
use ralph_workflow::RealProcessExecutor;

fn main() -> anyhow::Result<()> {
    // Set up Ctrl+C handler for graceful checkpoint save on interrupt
    interrupt::setup_interrupt_handler();

    // Create real process executor for production use
    let args = Args::parse();
    let pause_mode = args.pause_on_exit;
    let executor = std::sync::Arc::new(RealProcessExecutor::new());
    let result = app::run(args, executor);

    let interrupted = interrupt::take_exit_130_after_run();
    let outcome = if interrupted {
        exit_pause::ExitOutcome::Interrupted
    } else if result.is_err() {
        exit_pause::ExitOutcome::Failure
    } else {
        exit_pause::ExitOutcome::Success
    };

    let launch_context = exit_pause::detect_launch_context_with(
        exit_pause::StdEnvironment,
        exit_pause::StdProcessSpawner,
    );
    if exit_pause::should_pause_before_exit(pause_mode, outcome, &launch_context) {
        let _ = exit_pause::pause_for_enter();
    }

    // If the pipeline requested a SIGINT exit code, exit after cleanup has completed.
    if interrupted {
        std::process::exit(130);
    }

    result
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::Arc;

    use ralph_workflow::agents::{AgentDrain, AgentRegistry};
    use ralph_workflow::checkpoint::execution_history::ExecutionHistory;
    use ralph_workflow::checkpoint::RunContext;
    use ralph_workflow::config::Config;
    use ralph_workflow::logger::{Colors, Logger};
    use ralph_workflow::logging::RunLogContext;
    use ralph_workflow::phases::PhaseContext;
    use ralph_workflow::pipeline::Timer;
    use ralph_workflow::prompts::template_context::TemplateContext;
    use ralph_workflow::reducer::boundary::MainEffectHandler;
    use ralph_workflow::reducer::effect::{Effect, EffectHandler};
    use ralph_workflow::reducer::event::{AgentEvent, PipelineEvent};
    use ralph_workflow::runtime::environment::RealGitEnvironment;
    use ralph_workflow::workspace::{Workspace, WorkspaceFs};
    use ralph_workflow::RealProcessExecutor;
    use tempfile::TempDir;

    struct TestFixture {
        config: Config,
        registry: AgentRegistry,
        colors: Colors,
        logger: Logger,
        timer: Timer,
        template_context: TemplateContext,
        executor: Arc<RealProcessExecutor>,
        _temp_dir: TempDir,
        workspace: WorkspaceFs,
        workspace_arc: Arc<dyn Workspace>,
        repo_root: PathBuf,
        run_log_context: RunLogContext,
        cloud: ralph_workflow::config::types::CloudConfig,
        mock_env: RealGitEnvironment,
    }

    impl TestFixture {
        fn new(config_toml: &str) -> Self {
            let temp_dir = TempDir::new().unwrap();
            let workspace = WorkspaceFs::new(temp_dir.path().to_path_buf());
            let workspace_arc = Arc::new(workspace.clone()) as Arc<dyn Workspace>;
            let colors = Colors::new();
            let logger = Logger::new(colors);
            let run_log_context = RunLogContext::new(&workspace).unwrap();
            let unified: ralph_workflow::config::UnifiedConfig =
                toml::from_str(config_toml).unwrap();
            let registry = AgentRegistry::new()
                .unwrap()
                .apply_unified_config(&unified)
                .unwrap();

            Self {
                config: Config::default(),
                registry,
                colors,
                logger,
                timer: Timer::new(),
                template_context: TemplateContext::default(),
                executor: Arc::new(RealProcessExecutor::new()),
                _temp_dir: temp_dir,
                workspace,
                workspace_arc,
                repo_root: PathBuf::from("/mock/repo"),
                run_log_context,
                cloud: ralph_workflow::config::types::CloudConfig::disabled(),
                mock_env: RealGitEnvironment,
            }
        }

        #[cfg(any(test, feature = "test-utils"))]
        fn ctx(&mut self) -> PhaseContext<'_> {
            PhaseContext {
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
                executor_arc: Arc::clone(&self.executor)
                    as Arc<dyn ralph_workflow::executor::ProcessExecutor>,
                repo_root: self.repo_root.as_path(),
                workspace: &self.workspace,
                workspace_arc: Arc::clone(&self.workspace_arc),
                run_log_context: &self.run_log_context,
                cloud_reporter: None,
                cloud: &self.cloud,
                env: &self.mock_env,
            }
        }
    }

    fn initialized_agents_for_drain(mut fixture: TestFixture, drain: AgentDrain) -> Vec<String> {
        let result =
            MainEffectHandler::new(ralph_workflow::reducer::state::PipelineState::initial(1, 1))
                .execute(Effect::InitializeAgentChain { drain }, &mut fixture.ctx())
                .unwrap();

        match result.event {
            PipelineEvent::Agent(AgentEvent::ChainInitialized { agents, .. }) => agents,
            event => panic!("expected ChainInitialized event, got {event:?}"),
        }
    }

    #[test]
    fn initialize_agent_chain_uses_resolved_drain_bindings_without_context_repair() {
        let review_config = r#"
            [agent_chains]
            dev = ["codex"]
            review_chain = ["claude"]

            [agent_drains]
            planning = "dev"
            development = "dev"
            review = "review_chain"
            fix = "review_chain"
        "#;
        let commit_config = r#"
            [agent_chains]
            dev = ["codex"]
            review_chain = ["claude"]
            commit_chain = ["opencode"]

            [agent_drains]
            planning = "dev"
            development = "dev"
            review = "review_chain"
            fix = "review_chain"
            commit = "commit_chain"
        "#;

        let review_fixture = TestFixture::new(review_config);
        let review_agents = initialized_agents_for_drain(review_fixture, AgentDrain::Review);
        assert_eq!(review_agents, vec!["claude".to_string()]);

        let commit_fixture = TestFixture::new(commit_config);
        let commit_agents = initialized_agents_for_drain(commit_fixture, AgentDrain::Commit);
        assert_eq!(commit_agents, vec!["opencode".to_string()]);
    }
}
