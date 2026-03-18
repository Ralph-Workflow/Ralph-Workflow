//! Runtime object creation utilities.
//!
//! This module provides boundary functions for creating runtime objects that require
//! I/O operations (Timer, GitHelpers, PipelineRuntime). As a boundary module, it is
//! exempt from functional programming lints.

use crate::config::Config;
use crate::executor::ProcessExecutor;
use crate::logger::{Colors, Logger};
use crate::pipeline::PipelineRuntime;
use crate::prompts::registry::AgentRegistry;
use crate::workspace::Workspace;
use std::sync::Arc;

/// Creates a new AgentRegistry by loading from the default config path.
///
/// This involves file I/O to read the agents configuration.
pub fn create_agent_registry() -> Result<AgentRegistry, anyhow::Error> {
    AgentRegistry::new().map_err(|e| {
        anyhow::anyhow!("Failed to load built-in default agents config (examples/agents.toml): {e}")
    })
}

/// Creates a new Timer for measuring execution time.
///
/// Timer uses std::time for timing operations.
pub fn create_timer() -> crate::pipeline::Timer {
    crate::pipeline::Timer::new()
}

/// Creates a new GitHelpers instance for git operations.
pub fn create_git_helpers() -> crate::git_helpers::GitHelpers {
    crate::git_helpers::GitHelpers::new()
}

/// Creates a PipelineRuntime with the given configuration.
///
/// PipelineRuntime requires mutable references to timer, making it inherently imperative.
/// This boundary function handles the I/O-related construction.
pub fn create_pipeline_runtime<'a>(
    timer: &'a mut crate::pipeline::Timer,
    logger: &'a Logger,
    colors: &'a Colors,
    config: &'a Config,
    executor: &'a dyn ProcessExecutor,
    executor_arc: Arc<dyn ProcessExecutor>,
    workspace: &'a dyn Workspace,
    workspace_arc: Arc<dyn Workspace>,
) -> PipelineRuntime<'a> {
    PipelineRuntime {
        timer,
        logger,
        colors,
        config,
        executor,
        executor_arc,
        workspace,
        workspace_arc,
    }
}

/// Creates a new RealAppEffectHandler for production operations.
pub fn create_effect_handler() -> crate::app::effect_handler::RealAppEffectHandler {
    crate::app::effect_handler::RealAppEffectHandler::new()
}

/// Creates a new MainEffectHandler for the event loop.
pub fn create_main_effect_handler(
    initial_state: crate::reducer::state::PipelineState,
) -> crate::reducer::MainEffectHandler {
    crate::reducer::MainEffectHandler::new(initial_state)
}

/// Creates a new CommandExitCleanupGuard.
pub fn create_cleanup_guard<'a>(
    logger: &'a Logger,
    workspace: &'a dyn crate::workspace::Workspace,
    owned: bool,
) -> crate::app::runner::CommandExitCleanupGuard<'a> {
    crate::app::runner::CommandExitCleanupGuard::new(logger, workspace, owned)
}
