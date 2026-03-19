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
use crate::executor::ProcessExecutor;
use crate::logger::{Colors, Logger};
use crate::prompts::TemplateContext;

use crate::app::pipeline_setup::PipelineAndRepoRoot;
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

    // Set working directory first if override is provided
    // This ensures all subsequent operations (including config init) use the correct directory
    if let Some(ref override_dir) = args.working_dir_override {
        crate::app::env_access::set_current_dir(override_dir)?;
    }

    // Initialize configuration and agent registry
    let Some(init_result) = initialize_config(&args, colors, &logger)? else {
        return Ok(()); // Early exit (--init/--init-global)
    };

    let config_init::ConfigInitResult {
        config,
        registry,
        config_path,
        config_sources,
        agent_resolution_sources,
    } = init_result;

    // Resolve required agent names
    let validated = resolve_required_agents(&config, &agent_resolution_sources)?;
    let developer_agent = validated.developer_agent;
    let reviewer_agent = validated.reviewer_agent;

    // Handle listing commands (these can run without git repo)
    if handle_listing_commands(&args, &registry, colors) {
        return Ok(());
    }

    // Handle --diagnose
    if args.recovery.diagnose {
        let diagnose_workspace =
            crate::workspace::WorkspaceFs::new(crate::app::env_access::get_current_dir());
        handle_diagnose(
            &mut std::io::stdout(),
            colors,
            &config,
            &registry,
            crate::cli::ConfigInfo {
                path: &config_path,
                sources: &config_sources,
            },
            &*executor,
            &diagnose_workspace,
        );
        return Ok(());
    }

    // Validate agent chains
    validate_agent_chains(&registry, &agent_resolution_sources, &logger);

    let template_context = crate::prompts::TemplateContext::from_user_templates_dir(
        config.user_templates_dir().cloned(),
    );

    // Run the full pipeline with handler creation inside the boundary
    let PipelineAndRepoRoot { ctx, repo_root: _ } =
        crate::app::pipeline_setup::run_pipeline_with_handler_boundary(
            args,
            config,
            registry,
            developer_agent,
            reviewer_agent,
            registry.display_name(&developer_agent),
            registry.display_name(&reviewer_agent),
            config_path,
            colors,
            logger,
            executor,
            template_context,
        )?;

    if ctx.args.recovery.inspect_checkpoint {
        crate::app::resume::inspect_checkpoint(ctx.workspace.as_ref(), &ctx.logger)?;
        return Ok(());
    }

    // Run the pipeline
    run_pipeline(&ctx)
}
