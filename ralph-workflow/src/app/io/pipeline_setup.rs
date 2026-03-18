//! Pipeline setup boundary module.
//!
//! This module contains boundary functions for imperative pipeline setup operations.
//! As a boundary module, it is exempt from functional programming lints.

use crate::app::runner::pipeline_execution::helpers::PhaseContext;
use crate::app::runner::PipelineContext;
use crate::checkpoint::{PipelineCheckpoint, RunContext};
use crate::config::Config;
use crate::git_helpers::GitHelpers;
use crate::guidelines::ReviewGuidelines;
use crate::pipeline::Timer;

pub struct GitHelpersAndGuard<'a> {
    pub git_helpers: GitHelpers,
    pub agent_phase_guard: crate::app::runner::AgentPhaseGuard<'a>,
}

pub struct PhaseContextWithTimer<'ctx> {
    pub phase_ctx: PhaseContext<'ctx>,
    pub timer: Timer,
}

pub fn setup_git_and_agent_phase<'a>(ctx: &'a PipelineContext) -> GitHelpersAndGuard<'a> {
    let mut git_helpers = crate::app::io::runtime_factory::create_git_helpers();
    crate::app::runner::pipeline_execution::pipeline::execution_core_phases::prepare_agent_phase(
        ctx,
        &mut git_helpers,
    );
    let agent_phase_guard =
        crate::app::runner::AgentPhaseGuard::new(&mut git_helpers, &ctx.logger, &*ctx.workspace);
    GitHelpersAndGuard {
        git_helpers,
        agent_phase_guard,
    }
}

pub fn setup_phase_context_with_timer<'ctx>(
    ctx: &'ctx PipelineContext,
    config: &'ctx Config,
    review_guidelines: Option<&'ctx ReviewGuidelines>,
    run_context: &'ctx RunContext,
    resume_checkpoint: Option<&'ctx PipelineCheckpoint>,
    cloud_reporter: &'ctx dyn crate::cloud::CloudReporter,
) -> PhaseContextWithTimer<'ctx> {
    let mut timer = crate::app::io::runtime_factory::create_timer();
    let phase_ctx =
        crate::app::runner::pipeline_execution::helpers::create_phase_context_with_config(
            ctx,
            config,
            &mut timer,
            review_guidelines,
            run_context,
            resume_checkpoint,
            cloud_reporter,
        );
    PhaseContextWithTimer { phase_ctx, timer }
}
