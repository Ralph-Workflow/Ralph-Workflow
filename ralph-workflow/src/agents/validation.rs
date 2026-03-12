//! Startup validation for `OpenCode` agent references.
//!
//! This module provides validation logic for checking that all `opencode/*`
//! agent references in configured agent chains are valid (i.e., the provider
//! and model exist in the `OpenCode` API catalog).
//!
//! Validation errors include helpful suggestions for typos using Levenshtein
//! distance matching.

use crate::agents::fallback::{AgentDrain, FallbackConfig, ResolvedDrainConfig};
use crate::agents::opencode_api::ApiCatalog;
use crate::agents::opencode_resolver::OpenCodeResolver;
use std::collections::BTreeSet;

/// Validate all `OpenCode` agent references in resolved drain bindings.
///
/// This function checks that all `opencode/provider/model` references in the
/// configured drain bindings have valid providers and models in the API catalog.
///
/// Returns `Ok(())` if all references are valid, or `Err(String)` with a
/// user-friendly error message if any validation fails.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn validate_opencode_agents(
    resolved: &ResolvedDrainConfig,
    catalog: &ApiCatalog,
) -> Result<(), String> {
    validate_opencode_agents_in_resolved_drains(resolved, catalog)
}

/// Validate all `OpenCode` agent references from the legacy fallback config.
///
/// This compatibility wrapper exists at the config boundary only.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn validate_opencode_agents_legacy(
    fallback: &FallbackConfig,
    catalog: &ApiCatalog,
) -> Result<(), String> {
    validate_opencode_agents_in_resolved_drains(&fallback.resolve_drains(), catalog)
}

/// Validate all `OpenCode` agent references in resolved drain bindings.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn validate_opencode_agents_in_resolved_drains(
    resolved: &ResolvedDrainConfig,
    catalog: &ApiCatalog,
) -> Result<(), String> {
    let opencode_resolver = OpenCodeResolver::new(catalog.clone());
    let mut errors = Vec::new();

    let all_agents = get_opencode_refs_in_resolved_drains(resolved);

    // Validate each opencode/* agent
    for agent_name in all_agents {
        if let Some((provider, model)) = parse_opencode_ref(&agent_name) {
            if let Err(e) = opencode_resolver.validate(&provider, &model) {
                let msg = opencode_resolver.format_error(&e, &agent_name);
                errors.push(msg);
            }
        }
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("\n\n"))
    }
}

/// Parse an `opencode/provider/model` reference into `(provider, model)`.
///
/// Returns `None` if the reference doesn't match the expected pattern.
fn parse_opencode_ref(agent_name: &str) -> Option<(String, String)> {
    if !agent_name.starts_with("opencode/") {
        return None;
    }

    let parts: Vec<&str> = agent_name.split('/').collect();
    if parts.len() != 3 {
        return None;
    }

    let provider = parts[1].to_string();
    let model = parts[2].to_string();

    Some((provider, model))
}

/// Get all `OpenCode` agent references from resolved drain bindings.
#[must_use]
pub fn get_opencode_refs(resolved: &ResolvedDrainConfig) -> Vec<String> {
    get_opencode_refs_in_resolved_drains(resolved)
}

/// Get all `OpenCode` agent references from the legacy fallback configuration.
#[must_use]
pub fn get_opencode_refs_legacy(fallback: &FallbackConfig) -> Vec<String> {
    get_opencode_refs_in_resolved_drains(&fallback.resolve_drains())
}

/// Get all `OpenCode` agent references from resolved drain bindings.
#[must_use]
pub fn get_opencode_refs_in_resolved_drains(resolved: &ResolvedDrainConfig) -> Vec<String> {
    let unique = AgentDrain::all()
        .into_iter()
        .filter_map(|drain| resolved.binding(drain))
        .flat_map(|binding| binding.agents.iter().cloned())
        .filter(|name| name.starts_with("opencode/"))
        .collect::<BTreeSet<_>>();

    unique.into_iter().collect()
}

/// Count the number of `OpenCode` agent references in the resolved drain bindings.
#[cfg(test)]
fn count_opencode_refs(resolved: &ResolvedDrainConfig) -> usize {
    AgentDrain::all()
        .into_iter()
        .filter_map(|drain| resolved.binding(drain))
        .flat_map(|binding| binding.agents.iter())
        .filter(|name| name.starts_with("opencode/"))
        .cloned()
        .collect::<BTreeSet<_>>()
        .len()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::opencode_api::{Model, Provider};
    use std::collections::HashMap;

    fn mock_catalog() -> ApiCatalog {
        let mut providers = HashMap::new();
        providers.insert(
            "anthropic".to_string(),
            Provider {
                id: "anthropic".to_string(),
                name: "Anthropic".to_string(),
                description: "Anthropic Claude models".to_string(),
            },
        );

        let mut models = HashMap::new();
        models.insert(
            "anthropic".to_string(),
            vec![Model {
                id: "claude-sonnet-4-5".to_string(),
                name: "Claude Sonnet 4.5".to_string(),
                description: "Latest Claude Sonnet".to_string(),
                context_length: Some(200_000),
            }],
        );

        ApiCatalog {
            providers,
            models,
            cached_at: Some(chrono::Utc::now()),
            ttl_seconds: 86400,
        }
    }

    fn create_fallback_with_refs(refs: &[&str]) -> FallbackConfig {
        FallbackConfig {
            developer: refs.iter().map(|s| (*s).to_string()).collect(),
            ..FallbackConfig::default()
        }
    }

    #[test]
    fn test_parse_opencode_ref_valid() {
        let result = parse_opencode_ref("opencode/anthropic/claude-sonnet-4-5");
        assert_eq!(
            result,
            Some(("anthropic".to_string(), "claude-sonnet-4-5".to_string()))
        );
    }

    #[test]
    fn test_parse_opencode_ref_invalid() {
        assert_eq!(parse_opencode_ref("claude"), None);
        assert_eq!(parse_opencode_ref("opencode"), None);
        assert_eq!(parse_opencode_ref("opencode/anthropic"), None);
        assert_eq!(parse_opencode_ref("ccs/glm"), None);
    }

    #[test]
    fn test_validate_opencode_agents_valid() {
        let catalog = mock_catalog();
        let fallback = create_fallback_with_refs(&["opencode/anthropic/claude-sonnet-4-5"]);
        let resolved = fallback.resolve_drains();

        let result = validate_opencode_agents(&resolved, &catalog);
        assert!(result.is_ok());
    }

    #[test]
    fn test_validate_opencode_agents_invalid_provider() {
        let catalog = mock_catalog();
        let fallback = create_fallback_with_refs(&["opencode/unknown/claude-sonnet-4-5"]);
        let resolved = fallback.resolve_drains();

        let result = validate_opencode_agents(&resolved, &catalog);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("unknown"));
    }

    #[test]
    fn test_validate_opencode_agents_invalid_model() {
        let catalog = mock_catalog();
        let fallback = create_fallback_with_refs(&["opencode/anthropic/unknown-model"]);
        let resolved = fallback.resolve_drains();

        let result = validate_opencode_agents(&resolved, &catalog);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("unknown-model"));
    }

    #[test]
    fn test_count_opencode_refs() {
        let fallback = create_fallback_with_refs(&[
            "opencode/anthropic/claude-sonnet-4-5",
            "claude",
            "opencode/openai/gpt-4",
        ]);
        let resolved = fallback.resolve_drains();

        let count = count_opencode_refs(&resolved);
        assert_eq!(count, 2);
    }

    #[test]
    fn test_get_opencode_refs() {
        let fallback = create_fallback_with_refs(&[
            "opencode/anthropic/claude-sonnet-4-5",
            "claude",
            "opencode/openai/gpt-4",
        ]);
        let resolved = fallback.resolve_drains();

        let refs = get_opencode_refs(&resolved);
        assert_eq!(refs.len(), 2);
        assert!(refs.contains(&"opencode/anthropic/claude-sonnet-4-5".to_string()));
        assert!(refs.contains(&"opencode/openai/gpt-4".to_string()));
    }
}
