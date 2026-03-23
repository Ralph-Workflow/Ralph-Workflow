//! Agent registry loading helpers for configuration initialization.
//!
//! This module provides internal helper functions for loading and configuring
//! the agent registry during startup, including `OpenCode` API catalog setup.

use crate::agents::opencode_api::CatalogLoader;
use crate::agents::{AgentDrain, AgentRegistry, ConfigSource};
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
    crate::app::initialization::load_agent_registry_boundary(unified, config_path, catalog_loader)
}

/// Applies default agent selection from fallback chains.
///
/// If no agent was explicitly selected via CLI/env/preset, uses the first entry
/// from the `agent_chain` configuration.
pub(super) fn apply_default_agents(config: &Config, registry: &AgentRegistry) -> Config {
    let developer_agent = config.developer_agent.clone().or_else(|| {
        registry
            .resolved_drain(AgentDrain::Development)
            .and_then(|binding| binding.agents.first())
            .cloned()
    });
    let reviewer_agent = config.reviewer_agent.clone().or_else(|| {
        registry
            .resolved_drain(AgentDrain::Review)
            .and_then(|binding| binding.agents.first())
            .cloned()
    });

    Config {
        developer_agent,
        reviewer_agent,
        ..config.clone()
    }
}
