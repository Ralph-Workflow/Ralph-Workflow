//! Configuration loading and agent registry initialization.
//!
//! This module handles:
//! - Loading configuration from the unified config file (~/.config/ralph-workflow.toml)
//! - Applying environment variable and CLI overrides
//! - Selecting default agents from fallback chains
//! - Loading agent registry data from unified config
//! - Fetching and caching `OpenCode` API catalog for dynamic provider/model resolution
//!
//! # Dependency Injection
//!
//! The [`initialize_config_with`] function accepts both a [`CatalogLoader`] and a
//! [`ConfigEnvironment`] for full dependency injection. This enables testing without
//! network calls or environment variable dependencies.

mod registry;

use crate::agents::opencode_api::{CatalogLoader, RealCatalogLoader};
use crate::agents::ConfigSource;
use crate::cli::{
    apply_args_to_config, handle_check_config_with, handle_extended_help,
    handle_generate_completion, handle_init_global_with, handle_init_local_config_with,
    handle_list_work_guides, handle_smart_init_with, Args,
};
use crate::config::{
    loader, unified_config_path, Config, ConfigEnvironment, RealConfigEnvironment,
};
use crate::logger::Colors;
use crate::logger::Logger;
use std::path::PathBuf;

use crate::agents::AgentRegistry;
use registry::{apply_default_agents, load_agent_registry, resolve_agent_config_source_path};

/// Result of configuration initialization.
pub struct ConfigInitResult {
    /// The loaded configuration with CLI args applied.
    pub config: Config,
    /// The agent registry with merged configs.
    pub registry: AgentRegistry,
    /// The resolved path to the unified config file (for diagnostics/errors).
    pub config_path: PathBuf,
    /// Sources from which agent configs were loaded.
    pub config_sources: Vec<ConfigSource>,
    /// Description of config sources searched when resolving required agents.
    pub agent_resolution_sources: AgentResolutionSources,
}

/// Describes which config sources were consulted for agent resolution.
#[derive(Debug, Clone)]
pub struct AgentResolutionSources {
    /// Path to local config if local config lookup was active in this run.
    pub local_config_path: Option<PathBuf>,
    /// Path to global config if global config lookup was active in this run.
    pub global_config_path: Option<PathBuf>,
    /// Whether built-in defaults were part of resolution.
    pub built_in_defaults: bool,
}

impl AgentResolutionSources {
    /// Render a user-facing source list for diagnostics.
    #[must_use]
    pub fn describe_searched_sources(&self) -> String {
        let sources: Vec<String> = [
            self.local_config_path
                .as_ref()
                .map(|path| format!("local config ({})", path.display())),
            self.global_config_path
                .as_ref()
                .map(|path| format!("global config ({})", path.display())),
            self.built_in_defaults
                .then(|| "built-in defaults".to_string()),
        ]
        .into_iter()
        .flatten()
        .collect();

        if sources.is_empty() {
            "none".to_string()
        } else {
            sources.join(", ")
        }
    }
}

/// Initializes configuration and agent registry.
///
/// This function performs the following steps:
/// 1. Loads config from unified config file (~/.config/ralph-workflow.toml)
/// 2. Applies environment variable overrides
/// 3. Applies CLI arguments to config
/// 4. Handles --list-work-guides, --init/--init-global if set
/// 5. Loads agent registry from built-ins + unified config
/// 6. Selects default agents from fallback chains
///
/// # Arguments
///
/// * `args` - The parsed CLI arguments
/// * `colors` - Color configuration for output
/// * `logger` - Logger for info/warning messages
///
/// # Returns
///
/// Returns `Ok(Some(result))` on success, `Ok(None)` if an early exit was triggered
/// (e.g., --init, --list-templates), or an error if initialization fails.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn initialize_config(
    args: &Args,
    colors: Colors,
    logger: &Logger,
) -> anyhow::Result<Option<ConfigInitResult>> {
    initialize_config_with(
        args,
        colors,
        logger,
        &RealCatalogLoader::default(),
        &RealConfigEnvironment,
    )
}

/// Initializes configuration and agent registry with full dependency injection.
///
/// This is the same as [`initialize_config`] but accepts both a [`CatalogLoader`]
/// and a [`ConfigEnvironment`] for full dependency injection. This enables testing
/// without network calls or environment variable dependencies.
///
/// # Arguments
///
/// * `args` - The parsed CLI arguments
/// * `colors` - Color configuration for output
/// * `logger` - Logger for info/warning messages
#[expect(clippy::print_stderr, reason = "CLI error output to user")]
#[expect(clippy::print_stdout, reason = "CLI help output to user")]
/// * `catalog_loader` - Loader for the `OpenCode` API catalog
/// * `path_resolver` - Resolver for configuration file paths
///
/// # Returns
///
/// Returns `Ok(Some(result))` on success, `Ok(None)` if an early exit was triggered
/// (e.g., --init, --list-templates), or an error if initialization fails.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn initialize_config_with<L: CatalogLoader, P: ConfigEnvironment>(
    args: &Args,
    colors: Colors,
    logger: &Logger,
    catalog_loader: &L,
    path_resolver: &P,
) -> anyhow::Result<Option<ConfigInitResult>> {
    // Load configuration from unified config file (with env overrides)
    // Uses the provided path_resolver for filesystem operations instead of std::fs directly
    let (config, unified, warnings) =
        match loader::load_config_from_path_with_env(args.config.as_deref(), path_resolver) {
            Ok(result) => result,
            Err(e) => {
                // Config validation failed - display error and exit
                // Per requirements: Ralph refuses to start pipeline if ANY config file has errors
                eprintln!("{}", e.format_errors());
                return Err(anyhow::anyhow!("Configuration validation failed"));
            }
        };

    // Display any deprecation warnings from config loading
    warnings.iter().for_each(|warning| logger.warn(warning));

    let config_path = args
        .config
        .clone()
        .or_else(unified_config_path)
        .unwrap_or_else(|| PathBuf::from("~/.config/ralph-workflow.toml"));

    // Apply CLI arguments to config
    let config = apply_args_to_config(args, config, colors);

    // Handle --generate-completion flag: generate shell completion script and exit
    if let Some(shell) = args.completion.generate_completion {
        if handle_generate_completion(shell) {
            return Ok(None);
        }
    }

    // Handle --extended-help / --man flag: display extended help and exit.
    // If combined with --list-work-guides, show both to reduce surprises.
    if args.recovery.extended_help {
        handle_extended_help();
        if args.work_guide_list.list_work_guides {
            println!();
            let _ = handle_list_work_guides(colors);
        }
        return Ok(None);
    }

    // Handle --list-work-guides / --list-templates flag: display available Work Guides and exit
    if args.work_guide_list.list_work_guides && handle_list_work_guides(colors) {
        return Ok(None);
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
        return Ok(None);
    }

    // Handle --init-config flag: explicit config creation and exit
    if args.unified_init.init_config && handle_init_global_with(colors, path_resolver)? {
        return Ok(None);
    }

    // Handle --init-global flag: create unified config if it doesn't exist and exit
    if args.unified_init.init_global && handle_init_global_with(colors, path_resolver)? {
        return Ok(None);
    }

    // Handle --init-local-config flag: create local project config and exit
    if args.unified_init.init_local_config
        && handle_init_local_config_with(colors, path_resolver, args.unified_init.force_init)?
    {
        return Ok(None);
    }

    // Handle --check-config flag: validate and display effective settings
    if args.unified_init.check_config
        && handle_check_config_with(colors, path_resolver, args.debug_verbosity.debug)?
    {
        return Ok(None);
    }

    let local_config_path = path_resolver.local_config_path();
    let global_config_path = args
        .config
        .clone()
        .or_else(|| path_resolver.unified_config_path());

    let agent_resolution_sources = AgentResolutionSources {
        local_config_path: if args.config.is_none() {
            local_config_path.clone()
        } else {
            None
        },
        global_config_path,
        built_in_defaults: true,
    };

    // Initialize agent registry with built-in defaults + unified config.
    let config_source_path = resolve_agent_config_source_path(
        config_path.as_path(),
        args.config.as_deref(),
        local_config_path.as_deref(),
        path_resolver,
    );
    let (registry, config_sources) = load_agent_registry(
        unified.as_ref(),
        config_source_path.as_path(),
        catalog_loader,
    )?;

    // Apply default agents from fallback chains
    let config = apply_default_agents(&config, &registry);

    Ok(Some(ConfigInitResult {
        config,
        registry,
        config_path,
        config_sources,
        agent_resolution_sources,
    }))
}

#[cfg(test)]
mod tests {
    use super::{initialize_config_with, AgentResolutionSources};
    use crate::agents::opencode_api::{
        ApiCatalog, CacheError, CatalogLoader, DEFAULT_CACHE_TTL_SECONDS,
    };
    use crate::cli::Args;
    use crate::config::MemoryConfigEnvironment;
    use crate::logger::{Colors, Logger};
    use clap::Parser;
    use std::collections::HashMap;
    use std::path::PathBuf;

    struct StaticCatalogLoader;

    impl CatalogLoader for StaticCatalogLoader {
        fn load(&self) -> Result<ApiCatalog, CacheError> {
            Ok(ApiCatalog {
                providers: HashMap::new(),
                models: HashMap::new(),
                cached_at: None,
                ttl_seconds: DEFAULT_CACHE_TTL_SECONDS,
            })
        }
    }

    #[test]
    fn test_explicit_config_does_not_report_local_source() {
        let args = Args::try_parse_from(["ralph", "--config", "/test/config/ralph-workflow.toml"])
            .expect("args should parse");
        let logger = Logger::new(Colors::new());
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml")
            .with_file(
                "/test/repo/.agent/ralph-workflow.toml",
                "[agent_chain]\ndeveloper = [\"codex\"]\n",
            );

        let result =
            initialize_config_with(&args, Colors::new(), &logger, &StaticCatalogLoader, &env)
                .expect("initialization should succeed")
                .expect("normal execution should return config init result");

        assert!(
            result.config_sources.is_empty(),
            "with explicit --config and no explicit file present, local config should not be consulted"
        );
        assert_eq!(result.agent_resolution_sources.local_config_path, None);
        assert_eq!(
            result.agent_resolution_sources.global_config_path,
            Some(PathBuf::from("/test/config/ralph-workflow.toml"))
        );
    }

    #[test]
    fn test_agent_resolution_sources_include_local_when_no_explicit_config() {
        let args = Args::try_parse_from(["ralph"]).expect("args should parse");
        let logger = Logger::new(Colors::new());
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml");

        let result =
            initialize_config_with(&args, Colors::new(), &logger, &StaticCatalogLoader, &env)
                .expect("initialization should succeed")
                .expect("normal execution should return config init result");

        assert_eq!(
            result.agent_resolution_sources.local_config_path,
            Some(PathBuf::from("/test/repo/.agent/ralph-workflow.toml"))
        );
        assert_eq!(
            result.agent_resolution_sources.global_config_path,
            Some(PathBuf::from("/test/config/ralph-workflow.toml"))
        );
        assert!(result.agent_resolution_sources.built_in_defaults);
    }

    #[test]
    fn test_agent_resolution_sources_exclude_local_with_explicit_config() {
        let args = Args::try_parse_from(["ralph", "--config", "/custom/path.toml"])
            .expect("args should parse");
        let logger = Logger::new(Colors::new());
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml");

        let result =
            initialize_config_with(&args, Colors::new(), &logger, &StaticCatalogLoader, &env)
                .expect("initialization should succeed")
                .expect("normal execution should return config init result");

        assert_eq!(result.agent_resolution_sources.local_config_path, None);
        assert_eq!(
            result.agent_resolution_sources.global_config_path,
            Some(PathBuf::from("/custom/path.toml"))
        );
    }

    #[test]
    fn test_agent_resolution_sources_description_omits_missing_sources() {
        let sources = AgentResolutionSources {
            local_config_path: None,
            global_config_path: Some(PathBuf::from("/custom/path.toml")),
            built_in_defaults: true,
        };

        assert_eq!(
            sources.describe_searched_sources(),
            "global config (/custom/path.toml), built-in defaults"
        );
    }
}
