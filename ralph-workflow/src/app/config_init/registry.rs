//! Agent registry loading helpers for configuration initialization.
//!
//! This module provides internal helper functions for loading and configuring
//! the agent registry during startup, including `OpenCode` API catalog setup.

use crate::agents::opencode_api::CatalogLoader;
use crate::agents::{validation as agent_validation, AgentRegistry, AgentRole, ConfigSource};
use crate::config::{Config, ConfigEnvironment, UnifiedConfig};
use std::path::PathBuf;

pub(super) fn resolve_agent_config_source_path(
    config_path: &std::path::Path,
    explicit_config_path: Option<&std::path::Path>,
    local_config_path: Option<&std::path::Path>,
    env: &dyn ConfigEnvironment,
) -> PathBuf {
    if env.file_exists(config_path) {
        return config_path.to_path_buf();
    }

    if explicit_config_path.is_some() {
        return config_path.to_path_buf();
    }

    local_config_path
        .filter(|path| env.file_exists(path))
        .map_or_else(|| config_path.to_path_buf(), std::path::Path::to_path_buf)
}

pub(super) fn load_agent_registry<L: CatalogLoader>(
    unified: Option<&UnifiedConfig>,
    config_path: &std::path::Path,
    catalog_loader: &L,
) -> anyhow::Result<(AgentRegistry, Vec<ConfigSource>)> {
    let mut registry = AgentRegistry::new().map_err(|e| {
        anyhow::anyhow!("Failed to load built-in default agents config (examples/agents.toml): {e}")
    })?;

    let mut sources = Vec::new();

    // Agent configuration is loaded ONLY from:
    // 1. Built-in defaults (from AgentRegistry::new())
    // 2. Unified config file (~/.config/ralph-workflow.toml)
    // 3. OpenCode API catalog (for opencode/* references)
    //
    // Legacy agent config files (.agent/agents.toml, ~/.config/ralph/agents.toml)
    // are no longer supported. Use --init-global to create a unified config.

    if let Some(unified_cfg) = unified {
        let loaded = registry.apply_unified_config(unified_cfg);
        if loaded > 0 || unified_cfg.agent_chain.is_some() {
            sources.push(ConfigSource {
                path: config_path.to_path_buf(),
                agents_loaded: loaded,
            });
        }
    }

    // Load OpenCode API catalog if there are any opencode/* references
    setup_opencode_catalog(&mut registry, unified, catalog_loader)?;

    Ok((registry, sources))
}

/// Setup `OpenCode` API catalog for dynamic provider/model resolution.
///
/// This function:
/// 1. Checks if there are any `opencode/*` references in the configured agent chains
/// 2. If yes, fetches/loads the cached `OpenCode` API catalog
/// 3. Sets the catalog on the registry for dynamic agent resolution
/// 4. Validates all opencode/* references and reports errors with suggestions
pub(super) fn setup_opencode_catalog<L: CatalogLoader>(
    registry: &mut AgentRegistry,
    unified: Option<&UnifiedConfig>,
    catalog_loader: &L,
) -> anyhow::Result<()> {
    // Collect fallback config from unified config or registry defaults
    let fallback = unified
        .and_then(|u| u.agent_chain.as_ref())
        .cloned()
        .unwrap_or_else(|| registry.fallback_config().clone());

    // Check if there are any opencode/* references
    let opencode_refs = agent_validation::get_opencode_refs(&fallback);
    if opencode_refs.is_empty() {
        // No opencode references, skip catalog loading
        return Ok(());
    }

    // Load the API catalog using the injected loader
    let catalog = catalog_loader.load().map_err(|e| {
        anyhow::anyhow!(
            "Failed to load OpenCode API catalog. \
            This is required for the following agent references: {opencode_refs:?}. \
            Error: {e}"
        )
    })?;

    // Set the catalog on the registry for dynamic resolution
    registry.set_opencode_catalog(catalog.clone());

    // Validate all opencode/* references
    agent_validation::validate_opencode_agents(&fallback, &catalog)
        .map_err(|e| anyhow::anyhow!("{e}"))?;

    Ok(())
}

/// Applies default agent selection from fallback chains.
///
/// If no agent was explicitly selected via CLI/env/preset, uses the first entry
/// from the `agent_chain` configuration.
pub(super) fn apply_default_agents(config: &mut Config, registry: &AgentRegistry) {
    if config.developer_agent.is_none() {
        config.developer_agent = registry
            .fallback_config()
            .get_fallbacks(AgentRole::Developer)
            .first()
            .cloned();
    }
    if config.reviewer_agent.is_none() {
        config.reviewer_agent = registry
            .fallback_config()
            .get_fallbacks(AgentRole::Reviewer)
            .first()
            .cloned();
    }
}
