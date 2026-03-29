//! Runtime object creation utilities.
//!
//! This module provides boundary functions for creating runtime objects that require
//! I/O operations (Timer, GitHelpers, PipelineRuntime). As a boundary module, it is
//! exempt from functional programming lints.

use crate::config::Config;
use crate::logger::{Colors, Logger};
use crate::pipeline::PipelineRuntime;
use crate::prompts::registry::AgentRegistry;
use crate::workspace::Workspace;
use crate::ProcessExecutor;
use std::sync::Arc;

pub struct PipelineRuntimeFactoryParams<'a> {
    pub timer: &'a mut crate::pipeline::Timer,
    pub logger: &'a Logger,
    pub colors: &'a Colors,
    pub config: &'a Config,
    pub executor: &'a dyn ProcessExecutor,
    pub executor_arc: Arc<dyn ProcessExecutor>,
    pub workspace: &'a dyn Workspace,
    pub workspace_arc: Arc<dyn Workspace>,
}

pub fn create_agent_registry() -> Result<AgentRegistry, anyhow::Error> {
    AgentRegistry::new().map_err(|e| {
        anyhow::anyhow!("Failed to load built-in default agents config (examples/agents.toml): {e}")
    })
}

pub fn create_timer() -> crate::pipeline::Timer {
    crate::pipeline::Timer::new()
}

pub fn create_git_helpers() -> crate::git_helpers::GitHelpers {
    crate::git_helpers::GitHelpers::new()
}

pub fn create_pipeline_runtime<'a>(
    params: PipelineRuntimeFactoryParams<'a>,
) -> PipelineRuntime<'a> {
    let PipelineRuntimeFactoryParams {
        timer,
        logger,
        colors,
        config,
        executor,
        executor_arc,
        workspace,
        workspace_arc,
    } = params;

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

pub fn create_effect_handler() -> crate::app::effect_handler::RealAppEffectHandler {
    crate::app::effect_handler::RealAppEffectHandler::new()
}

pub fn create_effect_handler_with_workspace(
    workspace_root: std::path::PathBuf,
) -> crate::app::effect_handler::RealAppEffectHandler {
    crate::app::effect_handler::RealAppEffectHandler::with_workspace_root(workspace_root)
}

pub fn create_main_effect_handler(
    initial_state: crate::reducer::state::PipelineState,
) -> crate::reducer::MainEffectHandler {
    crate::reducer::MainEffectHandler::new(initial_state)
}

pub fn create_cleanup_guard<'a>(
    logger: &'a Logger,
    workspace: &'a dyn crate::workspace::Workspace,
    owned: bool,
) -> crate::app::runner::pipeline_execution::CommandExitCleanupGuard<'a> {
    let guard = crate::app::runner::pipeline_execution::CommandExitCleanupGuard::new(
        logger, workspace, owned,
    );
    guard
}
