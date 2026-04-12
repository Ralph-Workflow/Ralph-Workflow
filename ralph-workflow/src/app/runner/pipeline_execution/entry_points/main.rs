// Main entry point functions for the pipeline.
//
// This module contains:
// - run: Main application entry point
// - run_with_config: Test-only entry point with pre-built Config
// - run_with_config_and_resolver: Test-only entry point with custom path resolver
// - run_with_config_and_handlers: Test-only entry point with both handlers
// - RunWithHandlersParams: Parameters for test entry points

use crate::app::config_init::{self, initialize_config};
use crate::app::runner::command_handlers::handle_listing_commands;
use crate::app::validation::{resolve_required_agents, validate_agent_chains};
use crate::cli::{handle_diagnose, Args};
use crate::logger::{Colors, Logger};
use crate::prompts::TemplateContext;
use crate::ProcessExecutor;

use crate::agents::ConfigSource;
use crate::app::pipeline_setup::{PipelineAndRepoRoot, RunPipelineWithHandlerParams};
use crate::config::Config;

// run_pipeline is in scope via include!

/// Main application entry point.
///
/// Orchestrates the entire Ralph pipeline:
/// 1. Configuration initialization
/// 2. Agent validation
/// 3. Plumbing commands (if requested)
/// 4. Development phase
/// 5. Review & fix phase
/// 6. Final validation
/// 7. Commit phase
///
/// # Arguments
///
/// * `args` - The parsed CLI arguments
/// * `executor` - Process executor for external process execution
///
/// # Returns
///
/// Returns `Ok(())` on success or an error if any phase fails.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn run(args: Args, executor: std::sync::Arc<dyn ProcessExecutor>) -> anyhow::Result<()> {
    let colors = Colors::new();
    let logger = Logger::new(colors);
    if let Some(ref override_dir) = args.working_dir_override {
        crate::app::env_access::set_current_dir(override_dir)?;
    }
    let Some(init_result) = initialize_config(&args, colors, &logger)? else {
        return Ok(());
    };
    run_with_init_result(args, executor, init_result, colors, logger)
}

fn run_with_init_result(
    args: Args,
    executor: std::sync::Arc<dyn ProcessExecutor>,
    init_result: config_init::ConfigInitResult,
    colors: Colors,
    logger: Logger,
) -> anyhow::Result<()> {
    if handle_listing_commands(&args, &init_result.registry, colors) {
        return Ok(());
    }
    if args.recovery.diagnose {
        run_diagnose_and_exit(
            &init_result.config,
            &init_result.registry,
            &init_result.config_path,
            &init_result.config_sources,
            &*executor,
            colors,
        );
        return Ok(());
    }
    run_validated_pipeline(args, executor, init_result, colors, logger)
}

fn run_diagnose_and_exit(
    config: &Config,
    registry: &AgentRegistry,
    config_path: &std::path::Path,
    config_sources: &[ConfigSource],
    executor: &dyn ProcessExecutor,
    colors: Colors,
) {
    let diagnose_workspace =
        crate::workspace::WorkspaceFs::new(crate::app::env_access::get_current_dir());
    handle_diagnose(
        &mut std::io::stdout(),
        colors,
        config,
        registry,
        crate::cli::ConfigInfo {
            path: config_path,
            sources: config_sources,
        },
        executor,
        &diagnose_workspace,
    );
}

fn run_validated_pipeline(
    args: Args,
    executor: std::sync::Arc<dyn ProcessExecutor>,
    init_result: config_init::ConfigInitResult,
    colors: Colors,
    logger: Logger,
) -> anyhow::Result<()> {
    let config_init::ConfigInitResult {
        config,
        registry,
        config_path,
        agent_resolution_sources,
        ..
    } = init_result;
    let validated = resolve_required_agents(&config, &agent_resolution_sources)?;
    let developer_agent = validated.developer_agent;
    let reviewer_agent = validated.reviewer_agent;
    validate_agent_chains(&registry, &agent_resolution_sources, &logger);
    let template_context =
        TemplateContext::from_user_templates_dir(config.user_templates_dir().cloned());
    let developer_display = registry.display_name(&developer_agent);
    let reviewer_display = registry.display_name(&reviewer_agent);
    let params = RunPipelineWithHandlerParams {
        args,
        config,
        registry,
        developer_agent,
        reviewer_agent,
        developer_display,
        reviewer_display,
        config_path,
        colors,
        logger,
        executor,
        template_context,
    };
    let Some(PipelineAndRepoRoot { ctx, repo_root: _ }) =
        crate::app::pipeline_setup::run_pipeline_with_handler_boundary(params)?
    else {
        return Ok(());
    };
    if ctx.args.recovery.inspect_checkpoint {
        crate::app::resume::inspect_checkpoint(ctx.workspace.as_ref(), &ctx.logger)?;
        return Ok(());
    }
    run_pipeline(&ctx)
}
