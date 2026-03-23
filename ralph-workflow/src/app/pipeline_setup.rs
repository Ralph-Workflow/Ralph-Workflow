//! Pipeline setup boundary module.
//!
//! This module contains boundary functions for imperative pipeline setup operations.
//! As a boundary module, it is exempt from functional programming lints.

use crate::agents::AgentRegistry;
use crate::app::config::{EventLoopConfig, MAX_EVENT_LOOP_ITERATIONS};
use crate::app::context::PipelineContext;
use crate::app::effect::AppEffectHandler;
use crate::checkpoint::{PipelineCheckpoint, RunContext};
use crate::cli::Args;
use crate::config::Config;
use crate::git_helpers::GitHelpers;
use crate::guidelines::ReviewGuidelines;
use crate::logger::Colors;
use crate::phases::PhaseContext;
use crate::pipeline::Timer;
use crate::reducer::MainEffectHandler;
use crate::reducer::PipelineState;
use crate::workspace::Workspace;
use std::path::PathBuf;
use std::sync::Arc;

pub struct PhaseContextWithTimer<'ctx> {
    pub phase_ctx: PhaseContext<'ctx>,
    pub timer: Timer,
}

fn prepare_git_helpers_for_workspace(
    repo_root: &std::path::Path,
    workspace: &dyn crate::workspace::Workspace,
    logger: &crate::logger::Logger,
    mut git_helpers: GitHelpers,
    restore_prompt_permissions: bool,
) -> GitHelpers {
    crate::app::runner::pipeline_execution::prepare_agent_phase_for_workspace(
        repo_root,
        workspace,
        logger,
        &mut git_helpers,
        restore_prompt_permissions,
    );
    git_helpers
}

fn mark_cleanup_guard_owned<'a>(
    mut cleanup_guard: crate::app::runner::CommandExitCleanupGuard<'a>,
) -> crate::app::runner::CommandExitCleanupGuard<'a> {
    cleanup_guard.mark_owned();
    cleanup_guard
}

pub fn setup_git_and_agent_phase<'a>(
    git_helpers: &'a mut GitHelpers,
    ctx: &'a PipelineContext,
) -> crate::pipeline::AgentPhaseGuard<'a> {
    crate::app::runner::pipeline_execution::prepare_agent_phase_for_workspace(
        &ctx.repo_root,
        &*ctx.workspace,
        &ctx.logger,
        git_helpers,
        true,
    );
    crate::pipeline::AgentPhaseGuard::new(git_helpers, &ctx.logger, &*ctx.workspace)
}

pub fn setup_phase_context_with_timer<'ctx>(
    timer: &'ctx mut crate::pipeline::Timer,
    ctx: &'ctx PipelineContext,
    config: &'ctx Config,
    review_guidelines: Option<&'ctx ReviewGuidelines>,
    run_context: &'ctx RunContext,
    resume_checkpoint: Option<&'ctx PipelineCheckpoint>,
    cloud_reporter: &'ctx dyn crate::cloud::CloudReporter,
) -> crate::phases::PhaseContext<'ctx> {
    crate::app::runner::pipeline_execution::create_phase_context_with_config(
        ctx,
        config,
        timer,
        review_guidelines,
        run_context,
        resume_checkpoint,
        cloud_reporter,
    )
}

pub fn create_main_effect_handler_boundary(initial_state: PipelineState) -> MainEffectHandler {
    crate::app::runtime_factory::create_main_effect_handler(initial_state)
}

pub fn run_event_loop_with_handler_boundary<'r, 'ctx>(
    phase_ctx: &'r mut PhaseContext<'ctx>,
    initial_state: PipelineState,
) -> anyhow::Result<crate::app::EventLoopResult> {
    let event_loop_config = EventLoopConfig {
        max_iterations: MAX_EVENT_LOOP_ITERATIONS,
    };
    crate::app::core::run_event_loop(phase_ctx, Some(initial_state), event_loop_config)
}

pub fn create_cleanup_guard_boundary<'a>(
    logger: &'a crate::logger::Logger,
    workspace: &'a dyn crate::workspace::Workspace,
    owned: bool,
) -> crate::app::runner::CommandExitCleanupGuard<'a> {
    crate::app::runtime_factory::create_cleanup_guard(logger, workspace, owned)
}

pub fn create_git_helpers_boundary() -> GitHelpers {
    crate::app::runtime_factory::create_git_helpers()
}

pub fn create_effect_handler_boundary() -> crate::app::effect_handler::RealAppEffectHandler {
    crate::app::runtime_factory::create_effect_handler()
}

pub fn setup_agent_phase_for_workspace_boundary(
    repo_root: &std::path::Path,
    workspace: &dyn crate::workspace::Workspace,
    logger: &crate::logger::Logger,
) -> GitHelpers {
    prepare_git_helpers_for_workspace(
        repo_root,
        workspace,
        logger,
        crate::app::runtime_factory::create_git_helpers(),
        false,
    )
}

pub struct RepoCommandBoundaryParams<'a> {
    pub args: &'a Args,
    pub config: &'a Config,
    pub registry: &'a AgentRegistry,
    pub developer_agent: &'a str,
    pub reviewer_agent: &'a str,
    pub logger: &'a crate::logger::Logger,
    pub colors: Colors,
    pub executor: &'a std::sync::Arc<dyn crate::executor::ProcessExecutor>,
    pub repo_root: &'a std::path::Path,
    pub workspace: &'a std::sync::Arc<dyn crate::workspace::Workspace>,
}

pub fn handle_repo_commands_boundary(
    params: RepoCommandBoundaryParams<'_>,
) -> anyhow::Result<bool> {
    let RepoCommandBoundaryParams {
        args,
        config,
        registry,
        developer_agent,
        reviewer_agent,
        logger,
        colors,
        executor,
        repo_root,
        workspace,
    } = params;

    if args.recovery.dry_run {
        crate::cli::handle_dry_run(
            logger,
            colors,
            config,
            developer_agent,
            reviewer_agent,
            repo_root,
            workspace.as_ref(),
        )?;
        return Ok(true);
    }

    if args.rebase_flags.rebase_only {
        let _cleanup_guard = mark_cleanup_guard_owned(
            crate::app::runtime_factory::create_cleanup_guard(logger, workspace.as_ref(), false),
        );
        let _git_helpers = prepare_git_helpers_for_workspace(
            repo_root,
            workspace.as_ref(),
            logger,
            crate::app::runtime_factory::create_git_helpers(),
            false,
        );
        let template_context = crate::prompts::TemplateContext::from_user_templates_dir(
            config.user_templates_dir().cloned(),
        );
        crate::app::runner::pipeline_execution::handle_rebase_only(
            args,
            config,
            &template_context,
            logger,
            colors,
            executor,
            repo_root,
        )?;
        return Ok(true);
    }

    if args.commit_plumbing.generate_commit_msg {
        let _cleanup_guard = mark_cleanup_guard_owned(
            crate::app::runtime_factory::create_cleanup_guard(logger, workspace.as_ref(), false),
        );
        let _git_helpers = prepare_git_helpers_for_workspace(
            repo_root,
            workspace.as_ref(),
            logger,
            crate::app::runtime_factory::create_git_helpers(),
            false,
        );
        let template_context = crate::prompts::TemplateContext::from_user_templates_dir(
            config.user_templates_dir().cloned(),
        );
        crate::app::plumbing::handle_generate_commit_msg(
            &crate::app::plumbing::CommitGenerationConfig {
                config,
                template_context: &template_context,
                workspace: workspace.as_ref(),
                workspace_arc: std::sync::Arc::clone(workspace),
                registry,
                logger,
                colors,
                developer_agent,
                reviewer_agent,
                executor: std::sync::Arc::clone(executor),
            },
        )?;
        return Ok(true);
    }

    Ok(false)
}

pub struct PipelineAndRepoRoot {
    pub ctx: PipelineContext,
    pub repo_root: PathBuf,
}

pub struct RunPipelineWithHandlerParams {
    pub args: Args,
    pub config: Config,
    pub registry: AgentRegistry,
    pub developer_agent: String,
    pub reviewer_agent: String,
    pub developer_display: String,
    pub reviewer_display: String,
    pub config_path: std::path::PathBuf,
    pub colors: crate::logger::Colors,
    pub logger: crate::logger::Logger,
    pub executor: Arc<dyn crate::executor::ProcessExecutor>,
    pub template_context: crate::prompts::TemplateContext,
}

pub fn run_pipeline_with_handler_boundary(
    params: RunPipelineWithHandlerParams,
) -> anyhow::Result<PipelineAndRepoRoot> {
    use crate::app::effect::{AppEffect, AppEffectResult};
    use crate::app::runner::command_handlers::handle_plumbing_commands;
    use crate::app::runner::pipeline_execution::{
        command_requires_prompt_setup, handle_repo_commands_without_prompt_setup,
        prepare_pipeline_or_exit,
    };
    use crate::app::runner::pipeline_execution::{PipelinePreparationParams, RepoCommandParams};
    use crate::app::runner::AgentSetupParams;

    let RunPipelineWithHandlerParams {
        args,
        config,
        registry,
        developer_agent,
        reviewer_agent,
        developer_display: _,
        reviewer_display: _,
        config_path,
        colors,
        logger,
        executor,
        template_context: _,
    } = params;

    let handler = &mut crate::app::runtime_factory::create_effect_handler();

    let early_repo_root = {
        if let Some(dir) = args.working_dir_override.as_deref() {
            match handler.execute(AppEffect::SetCurrentDir {
                path: dir.to_path_buf(),
            }) {
                AppEffectResult::Ok => {}
                AppEffectResult::Error(e) => anyhow::bail!(e),
                other => anyhow::bail!("unexpected result from SetCurrentDir: {other:?}"),
            }
        }

        match handler.execute(AppEffect::GitRequireRepo) {
            AppEffectResult::Ok => {}
            AppEffectResult::Error(e) => anyhow::bail!("Not in a git repository: {e}"),
            other => anyhow::bail!("unexpected result from GitRequireRepo: {other:?}"),
        }

        match handler.execute(AppEffect::GitGetRepoRoot) {
            AppEffectResult::Path(p) => p,
            AppEffectResult::Error(e) => anyhow::bail!("Failed to get repo root: {e}"),
            other => anyhow::bail!("unexpected result from GitGetRepoRoot: {other:?}"),
        }
    };

    let workspace: Arc<dyn Workspace> =
        Arc::new(crate::workspace::WorkspaceFs::new(early_repo_root.clone()));

    if handle_plumbing_commands(&args, &logger, colors, handler, Some(workspace.as_ref()))? {
        anyhow::bail!("plumbing commands should not return from run_pipeline");
    }

    if !command_requires_prompt_setup(&args)
        && handle_repo_commands_without_prompt_setup(RepoCommandParams {
            args: &args,
            config: &config,
            registry: &registry,
            developer_agent: &developer_agent,
            reviewer_agent: &reviewer_agent,
            logger: &logger,
            colors,
            executor: &executor,
            repo_root: workspace.root(),
            workspace: &workspace,
        })?
    {
        anyhow::bail!("repo commands should not return from run_pipeline");
    }

    let repo_root = match crate::app::runner::validate_and_setup_agents(
        &AgentSetupParams {
            config: &config,
            registry: &registry,
            developer_agent: &developer_agent,
            reviewer_agent: &reviewer_agent,
            config_path: &config_path,
            colors,
            logger: &logger,
            working_dir_override: args.working_dir_override.as_deref(),
        },
        handler,
    )? {
        Some(root) => root,
        None => anyhow::bail!("agent validation/setup failed"),
    };

    let params = PipelinePreparationParams {
        args,
        config,
        registry,
        developer_agent,
        reviewer_agent,
        colors,
        logger,
        executor,
        repo_root,
        workspace,
        handler,
    };

    let ctx = prepare_pipeline_or_exit(params)?
        .ok_or_else(|| anyhow::anyhow!("pipeline preparation returned None"))?;

    Ok(PipelineAndRepoRoot {
        ctx,
        repo_root: early_repo_root,
    })
}
