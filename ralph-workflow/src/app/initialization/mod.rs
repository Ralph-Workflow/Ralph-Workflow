//! Initialization boundary module.
//!
//! This module provides boundary functions for pipeline initialization that involve
//! imperative setup and configuration restoration. As a boundary module, it is
//! exempt from functional programming lints.

use crate::agents::{AgentRegistry, ConfigSource};
use crate::config::UnifiedConfig;
use std::path::Path;

/// Restore configuration from a checkpoint by applying checkpoint overrides.
///
/// This function performs imperative config restoration which requires mutable access.
/// It is placed in a boundary module for functional code compliance.
pub fn restore_config_from_checkpoint(
    mut config: crate::config::Config,
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
) -> crate::config::Config {
    crate::checkpoint::apply_checkpoint_to_config(&mut config, checkpoint);
    config
}

/// Load agent registry with optional unified config.
///
/// This boundary function handles the I/O and mutation involved in registry loading.
pub fn load_agent_registry_boundary<L: crate::agents::opencode_api::CatalogLoader>(
    unified: Option<&UnifiedConfig>,
    config_path: &Path,
    catalog_loader: &L,
) -> anyhow::Result<(AgentRegistry, Vec<ConfigSource>)> {
    let mut registry = AgentRegistry::new()?;
    let sources = registry.apply_unified_config(unified, config_path)?;

    let opencode_refs =
        crate::agents::validation::get_opencode_refs_in_resolved_drains(registry.resolved_drains());
    if !opencode_refs.is_empty() {
        let catalog = catalog_loader.load().map_err(|e| {
            anyhow::anyhow!(
                "Failed to load OpenCode API catalog. \
                 This is required for the following agent references: {opencode_refs:?}. \
                 Error: {e}"
            )
        })?;
        registry.set_opencode_catalog(catalog.clone());

        crate::agents::validation::validate_opencode_agents_in_resolved_drains(
            registry.resolved_drains(),
            &catalog,
        )
        .map_err(|e| anyhow::anyhow!("{e}"))?;
    }

    Ok((registry, sources))
}
