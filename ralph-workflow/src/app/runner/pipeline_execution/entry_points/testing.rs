use crate::agents::AgentRegistry;
use crate::app::effect::AppEffectHandler;

/// Test-only entry point that accepts a pre-built Config.
///
/// This function is for integration testing only. It bypasses environment variable
/// loading and uses the provided Config directly, enabling deterministic tests
/// that don't rely on process-global state.
///
/// This function handles ALL commands including early-exit commands (--init, --diagnose,
/// --reset-start-commit, etc.) so that tests can use a single entry point.
///
/// # Arguments
///
/// * `args` - The parsed CLI arguments
/// * `executor` - Process executor for external process execution
/// * `config` - Pre-built configuration (bypasses env var loading)
/// * `registry` - Pre-built agent registry
///
/// # Returns
///
/// Returns `Ok(())` on success or an error if any phase fails.
///
/// # Errors
///
/// Returns an error if:
/// - Agent validation fails
/// - Pipeline initialization fails
/// - Any pipeline phase execution fails
#[cfg(feature = "test-utils")]
pub fn run_with_config(
    args: Args,
    executor: std::sync::Arc<dyn ProcessExecutor>,
    config: crate::config::Config,
    registry: AgentRegistry,
) -> anyhow::Result<()> {
    // Use real path resolver and effect handler by default
    let mut handler = RealAppEffectHandler::new();
    run_with_config_and_resolver(
        args,
        executor,
        config,
        registry,
        &crate::config::RealConfigEnvironment,
        &mut handler,
        None, // Use default WorkspaceFs
    )
}

/// Test-only entry point that accepts a pre-built Config and a custom path resolver.
///
/// This function is for integration testing only. It bypasses environment variable
/// loading and uses the provided Config and path resolver directly, enabling
/// deterministic tests that don't rely on process-global state or env vars.
///
/// This function handles ALL commands including early-exit commands (--init, --diagnose,
/// --reset-start-commit, etc.) so that tests can use a single entry point.
///
/// # Arguments
///
/// * `args` - The parsed CLI arguments
/// * `executor` - Process executor for external process execution
/// * `config` - Pre-built configuration (bypasses env var loading)
/// * `registry` - Pre-built agent registry
/// * `path_resolver` - Custom path resolver for init commands
/// * `handler` - Effect handler for git/filesystem operations
/// * `workspace` - Optional workspace for file operations (if `None`, uses `WorkspaceFs`)
///
/// # Returns
///
/// Returns `Ok(())` on success or an error if any phase fails.
///
/// # Errors
///
/// Returns an error if:
/// - Agent validation fails
/// - Pipeline initialization fails
/// - Any pipeline phase execution fails
#[cfg(feature = "test-utils")]
pub fn run_with_config_and_resolver<P: crate::config::ConfigEnvironment, H: AppEffectHandler>(
    args: Args,
    executor: std::sync::Arc<dyn ProcessExecutor>,
    config: crate::config::Config,
    registry: AgentRegistry,
    path_resolver: &P,
    handler: &mut H,
    workspace: Option<std::sync::Arc<dyn crate::workspace::Workspace>>,
) -> anyhow::Result<()> {
    use crate::cli::{
        handle_extended_help, handle_init_global_with, handle_init_local_config_with,
        handle_list_work_guides, handle_smart_init_with,
    };

    let colors = Colors::new();
    let logger = Logger::new(colors);

    // Set working directory first if override is provided
    if let Some(ref override_dir) = args.working_dir_override {
        std::env::set_current_dir(override_dir)?;
    }

    // CRITICAL: Validate config files before any operations (fail-fast)
    // This ensures tests match production behavior where invalid config prevents startup
    // Per requirements: Ralph refuses to start pipeline if ANY config file has errors
    if let Err(e) =
        crate::config::loader::load_config_from_path_with_env(args.config.as_deref(), path_resolver)
    {
        eprintln!("{}", e.format_errors());
        return Err(anyhow::anyhow!("Configuration validation failed"));
    }

    // Handle --extended-help / --man flag: display extended help and exit.
    if args.recovery.extended_help {
        handle_extended_help();
        if args.work_guide_list.list_work_guides {
            println!();
            let _ = handle_list_work_guides(colors);
        }
        return Ok(());
    }

    // Handle --list-work-guides / --list-templates flag
    if args.work_guide_list.list_work_guides && handle_list_work_guides(colors) {
        return Ok(());
    }

    // Handle smart --init flag: intelligently determine what to initialize
    if args.unified_init.init.is_some()
        && handle_smart_init_with(
            args.unified_init.init.as_deref(),
            args.unified_init.force_init,
            colors,
            path_resolver,
        )?
    {
        return Ok(());
    }

    // Handle --init-config flag: explicit config creation and exit
    if args.unified_init.init_config && handle_init_global_with(colors, path_resolver)? {
        return Ok(());
    }

    // Handle --init-global flag: create unified config if it doesn't exist and exit
    if args.unified_init.init_global && handle_init_global_with(colors, path_resolver)? {
        return Ok(());
    }

    // Handle --init-local-config flag: create local project config and exit
    if args.unified_init.init_local_config
        && handle_init_local_config_with(colors, path_resolver, args.unified_init.force_init)?
    {
        return Ok(());
    }

    // Use provided config directly (no env var loading)
    let config_path = std::path::PathBuf::from("test-config");

    // Resolve required agent names
    let validated = resolve_required_agents(
        &config,
        &crate::app::config_init::AgentResolutionSources {
            local_config_path: None,
            global_config_path: Some(config_path.clone()),
            built_in_defaults: true,
        },
    )?;
    let developer_agent = validated.developer_agent;
    let reviewer_agent = validated.reviewer_agent;

    // Handle listing commands (these can run without git repo)
    if handle_listing_commands(&args, &registry, colors) {
        return Ok(());
    }

    // Handle --diagnose
    if args.recovery.diagnose {
        let diagnose_workspace = workspace
            .as_ref()
            .map(std::convert::AsRef::as_ref)
            .ok_or_else(|| anyhow::anyhow!("--diagnose requires workspace context"))?;
        handle_diagnose(
            &mut std::io::stdout(),
            colors,
            &config,
            &registry,
            crate::cli::ConfigInfo {
                path: &config_path,
                sources: &[],
            },
            &*executor,
            diagnose_workspace,
        );
        return Ok(());
    }

    let repo_root = workspace
        .as_ref()
        .map(|ws| ws.root().to_path_buf())
        .map_or_else(
            || discover_repo_root_for_workspace(args.working_dir_override.as_deref(), handler),
            Ok,
        )?;

    let workspace = workspace.unwrap_or_else(|| {
        std::sync::Arc::new(crate::workspace::WorkspaceFs::new(repo_root.clone()))
    });

    // Handle plumbing commands (--reset-start-commit, --show-commit-msg, etc.)
    // Pass workspace reference for testability with MemoryWorkspace
    if handle_plumbing_commands(&args, &logger, colors, handler, Some(workspace.as_ref()))? {
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
            repo_root: &repo_root,
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
        handler,
    )?
    else {
        return Ok(());
    };

    // Prepare pipeline context or exit early
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
        handler,
        workspace,
    })?)
    .map_or_else(|| Ok(()), |ctx| run_pipeline(&ctx))
}

/// Parameters for `run_with_config_and_handlers`.
///
/// Groups related parameters to reduce function argument count.
#[cfg(feature = "test-utils")]
pub struct RunWithHandlersParams<'a, 'ctx, P, A, E>
where
    P: crate::config::ConfigEnvironment,
    A: AppEffectHandler,
    E: crate::reducer::EffectHandler<'ctx> + crate::app::runtime::StatefulHandler,
{
    pub args: Args,
    pub executor: std::sync::Arc<dyn ProcessExecutor>,
    pub config: crate::config::Config,
    pub registry: AgentRegistry,
    pub path_resolver: &'a P,
    pub app_handler: &'a mut A,
    pub effect_handler: &'a mut E,
    pub workspace: Option<std::sync::Arc<dyn crate::workspace::Workspace>>,
    /// Phantom data to bind the `'ctx` lifetime from `EffectHandler<'ctx>`.
    pub _marker: std::marker::PhantomData<&'ctx ()>,
}

/// Run with both `AppEffectHandler` AND `EffectHandler` for full isolation.
///
/// This function is the ultimate test entry point that allows injecting BOTH:
/// - `AppEffectHandler` for CLI-layer operations (git require repo, set cwd, etc.)
/// - `EffectHandler` for reducer-layer operations (create commit, run rebase, etc.)
///
/// Using both handlers ensures tests make ZERO real git calls at any layer.
///
/// # Example
///
/// ```ignore
/// use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
/// use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
///
/// let mut app_handler = MockAppEffectHandler::new().with_head_oid("abc123");
/// let mut effect_handler = MockEffectHandler::new(PipelineState::initial(1, 0));
///
/// run_with_config_and_handlers(RunWithHandlersParams {
///     args, executor, config, registry, path_resolver: &env,
///     app_handler: &mut app_handler, effect_handler: &mut effect_handler,
///     workspace: None,
/// })?;
///
/// // Verify no real git operations at either layer
/// assert!(app_handler.captured().iter().any(|e| matches!(e, AppEffect::GitRequireRepo)));
/// assert!(effect_handler.captured_effects().iter().any(|e| matches!(e, Effect::CreateCommit { .. })));
/// ```
///
/// # Errors
///
/// Returns an error if:
/// - Agent validation fails
/// - Pipeline initialization fails
/// - Any pipeline phase execution fails
#[cfg(feature = "test-utils")]
pub fn run_with_config_and_handlers<'a, 'ctx, P, A, E>(
    params: RunWithHandlersParams<'a, 'ctx, P, A, E>,
) -> anyhow::Result<()>
where
    P: crate::config::ConfigEnvironment,
    A: AppEffectHandler,
    E: crate::reducer::EffectHandler<'ctx> + crate::app::runtime::StatefulHandler,
{
    use crate::cli::{
        handle_extended_help, handle_init_global_with, handle_init_local_config_with,
        handle_list_work_guides, handle_smart_init_with,
    };

    let RunWithHandlersParams {
        args,
        executor,
        config,
        registry,
        path_resolver,
        app_handler,
        effect_handler,
        workspace,
        ..
    } = params;

    let colors = Colors::new();
    let logger = Logger::new(colors);

    // Set working directory first if override is provided
    if let Some(ref override_dir) = args.working_dir_override {
        std::env::set_current_dir(override_dir)?;
    }

    // CRITICAL: Validate config files before any operations (fail-fast)
    // This ensures tests match production behavior where invalid config prevents startup
    // Per requirements: Ralph refuses to start pipeline if ANY config file has errors
    if let Err(e) =
        crate::config::loader::load_config_from_path_with_env(args.config.as_deref(), path_resolver)
    {
        eprintln!("{}", e.format_errors());
        return Err(anyhow::anyhow!("Configuration validation failed"));
    }

    // Handle --extended-help / --man flag
    if args.recovery.extended_help {
        handle_extended_help();
        if args.work_guide_list.list_work_guides {
            println!();
            let _ = handle_list_work_guides(colors);
        }
        return Ok(());
    }

    // Handle --list-work-guides / --list-templates flag
    if args.work_guide_list.list_work_guides && handle_list_work_guides(colors) {
        return Ok(());
    }

    // Handle smart --init flag
    if args.unified_init.init.is_some()
        && handle_smart_init_with(
            args.unified_init.init.as_deref(),
            args.unified_init.force_init,
            colors,
            path_resolver,
        )?
    {
        return Ok(());
    }

    // Handle --init-config flag
    if args.unified_init.init_config && handle_init_global_with(colors, path_resolver)? {
        return Ok(());
    }

    // Handle --init-global flag
    if args.unified_init.init_global && handle_init_global_with(colors, path_resolver)? {
        return Ok(());
    }

    // Handle --init-local-config flag: create local project config and exit
    if args.unified_init.init_local_config
        && handle_init_local_config_with(colors, path_resolver, args.unified_init.force_init)?
    {
        return Ok(());
    }

    // Use provided config directly
    let config_path = std::path::PathBuf::from("test-config");

    // Resolve required agent names
    let validated = resolve_required_agents(
        &config,
        &crate::app::config_init::AgentResolutionSources {
            local_config_path: None,
            global_config_path: Some(config_path.clone()),
            built_in_defaults: true,
        },
    )?;
    let developer_agent = validated.developer_agent;
    let reviewer_agent = validated.reviewer_agent;

    // Handle listing commands
    if handle_listing_commands(&args, &registry, colors) {
        return Ok(());
    }

    // Handle --diagnose
    if args.recovery.diagnose {
        let diagnose_workspace = workspace
            .as_ref()
            .map(std::convert::AsRef::as_ref)
            .ok_or_else(|| anyhow::anyhow!("--diagnose requires workspace context"))?;
        handle_diagnose(
            &mut std::io::stdout(),
            colors,
            &config,
            &registry,
            crate::cli::ConfigInfo {
                path: &config_path,
                sources: &[],
            },
            &*executor,
            diagnose_workspace,
        );
        return Ok(());
    }

    let repo_root = workspace
        .as_ref()
        .map(|ws| ws.root().to_path_buf())
        .map_or_else(
            || discover_repo_root_for_workspace(args.working_dir_override.as_deref(), app_handler),
            Ok,
        )?;

    let workspace = workspace.unwrap_or_else(|| {
        std::sync::Arc::new(crate::workspace::WorkspaceFs::new(repo_root.clone()))
    });

    // Handle plumbing commands with app_handler
    // Pass workspace reference for testability with MemoryWorkspace
    if handle_plumbing_commands(
        &args,
        &logger,
        colors,
        app_handler,
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
            repo_root: &repo_root,
            workspace: &workspace,
        })?
    {
        return Ok(());
    }

    if args.recovery.inspect_checkpoint {
        crate::app::resume::inspect_checkpoint(workspace.as_ref(), &logger)?;
        return Ok(());
    }

    // Validate agents and set up git repo with app_handler
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
        app_handler,
    )?
    else {
        return Ok(());
    };

    // Prepare pipeline context or exit early
    let ctx = prepare_pipeline_or_exit(PipelinePreparationParams {
        args,
        config,
        registry,
        developer_agent,
        reviewer_agent,
        repo_root,
        logger,
        colors,
        executor,
        handler: app_handler,
        workspace,
    })?;

    // Run pipeline with the injected effect_handler
    ctx.map_or_else(
        || Ok(()),
        |ctx| run_pipeline_with_effect_handler(&ctx, effect_handler),
    )
}
