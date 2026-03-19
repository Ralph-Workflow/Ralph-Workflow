//! Plumbing command boundary module.
//!
//! This module provides boundary functions for plumbing commands that involve
//! runtime creation and execution. As a boundary module, it is exempt from
//! functional programming lints.

use crate::pipeline::PipelineRuntime;

pub fn run_pipeline_for_commit_message<'a>(
    timer: &'a mut crate::pipeline::Timer,
    config: &'a crate::app::plumbing::CommitGenerationConfig<'a>,
) -> anyhow::Result<PipelineRuntime<'a>> {
    let executor_ref: &dyn crate::executor::ProcessExecutor = &*config.executor;
    let runtime = crate::app::runtime_factory::create_pipeline_runtime(
        timer,
        config.logger,
        &config.colors,
        config.config,
        executor_ref,
        std::sync::Arc::clone(&config.executor),
        config.workspace,
        std::sync::Arc::clone(&config.workspace_arc),
    );
    Ok(runtime)
}
