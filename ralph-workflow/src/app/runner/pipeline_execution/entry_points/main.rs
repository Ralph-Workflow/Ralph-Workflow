// Main entry point functions for the pipeline.
//
// This module contains:
// - run: Main application entry point
// - run_with_config: Test-only entry point with pre-built Config
// - run_with_config_and_resolver: Test-only entry point with custom path resolver
// - run_with_config_and_handlers: Test-only entry point with both handlers
// - RunWithHandlersParams: Parameters for test entry points

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
        std::env::set_current_dir(override_dir)?;
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
        let diagnose_workspace = crate::workspace::WorkspaceFs::new(
            std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from(".")),
        );
        handle_diagnose(
            colors,
            &config,
            &registry,
            &config_path,
            &config_sources,
            &*executor,
            &diagnose_workspace,
        );
        return Ok(());
    }

    // Validate agent chains
    validate_agent_chains(&registry, &agent_resolution_sources, colors);

    // Create effect handler for production operations
    let mut handler = effect_handler::RealAppEffectHandler::new();

    // Get repo root early for workspace creation (needed by plumbing commands)
    // This uses the same logic as setup_working_dir_via_handler but captures the repo_root.
    let early_repo_root =
        discover_repo_root_for_workspace(args.working_dir_override.as_deref(), &mut handler)?;

    // Create workspace for plumbing commands (and later for the full pipeline)
    let workspace: std::sync::Arc<dyn crate::workspace::Workspace> =
        std::sync::Arc::new(crate::workspace::WorkspaceFs::new(early_repo_root));

    // Handle plumbing commands with workspace support
    if handle_plumbing_commands(
        &args,
        &logger,
        colors,
        &mut handler,
        Some(workspace.as_ref()),
    )? {
        return Ok(());
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
        return Ok(());
    }

    if args.recovery.inspect_checkpoint {
        crate::app::resume::inspect_checkpoint(workspace.as_ref(), &logger)?;
        return Ok(());
    }

    // Validate agents and set up git repo and PROMPT.md
    // Note: repo_root is discovered again here (same as early_repo_root) but also
    // does additional setup like PROMPT.md creation that plumbing commands don't need
    let Some(repo_root) = validate_and_setup_agents(
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
        &mut handler,
    )?
    else {
        return Ok(());
    };

    // Prepare pipeline context or exit early
    // Note: Reuse workspace created earlier (same repo root)
    (prepare_pipeline_or_exit(PipelinePreparationParams {
        args,
        config,
        registry,
        developer_agent,
        reviewer_agent,
        repo_root,
        logger,
        colors,
        executor,
        handler: &mut handler,
        workspace,
    })?)
    .map_or_else(|| Ok(()), |ctx| run_pipeline(&ctx))
}
