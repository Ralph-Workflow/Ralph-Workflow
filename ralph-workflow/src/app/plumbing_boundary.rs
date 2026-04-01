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
        crate::app::runtime_factory::PipelineRuntimeFactoryParams {
            timer,
            logger: config.logger,
            colors: &config.colors,
            config: config.config,
            executor: executor_ref,
            executor_arc: std::sync::Arc::clone(&config.executor),
            workspace: config.workspace,
            workspace_arc: std::sync::Arc::clone(&config.workspace_arc),
        },
    );
    Ok(runtime)
}

pub fn generate_commit_message_for_plumbing(
    config: &crate::app::plumbing::CommitGenerationConfig<'_>,
    diff: &str,
    agents: &[String],
) -> anyhow::Result<crate::phases::commit::CommitMessageResult> {
    let result = crate::phases::generate_commit_message_with_chain(
        diff,
        config.registry,
        &mut run_pipeline_for_commit_message(
            &mut crate::app::runtime_factory::create_timer(),
            config,
        )?,
        agents,
        config.template_context,
        config.workspace,
    )
    .map_err(|e| anyhow::anyhow!("Failed to generate commit message: {e}"))?;
    Ok(result)
}
