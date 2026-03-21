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

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OpenCodeValidationError {
    InvalidReferences { messages: Vec<String> },
}

impl std::fmt::Display for OpenCodeValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidReferences { messages } => write!(f, "{}", messages.join("\n\n")),
        }
    }
}

impl std::error::Error for OpenCodeValidationError {}

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
) -> Result<(), OpenCodeValidationError> {
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
) -> Result<(), OpenCodeValidationError> {
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
) -> Result<(), OpenCodeValidationError> {
    let opencode_resolver = OpenCodeResolver::new(catalog.clone());

    let all_agents = get_opencode_refs_in_resolved_drains(resolved);

    // Validate each opencode/* agent - collect errors using iterator
    let errors: Vec<_> = all_agents
        .iter()
        .filter_map(|agent_name| {
            parse_opencode_ref(agent_name).and_then(|(provider, model)| {
                opencode_resolver
                    .validate(&provider, &model)
                    .err()
                    .map(|e| opencode_resolver.format_error(&e, agent_name))
            })
        })
        .collect();

    if errors.is_empty() {
        Ok(())
    } else {
        Err(OpenCodeValidationError::InvalidReferences { messages: errors })
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

    let provider = parts.get(1)?.to_string();
    let model = parts.get(2)?.to_string();

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
        let providers = HashMap::from([(
            "anthropic".to_string(),
            Provider {
                id: "anthropic".to_string(),
                name: "Anthropic".to_string(),
                description: "Anthropic Claude models".to_string(),
            },
        )]);

        let models = HashMap::from([(
            "anthropic".to_string(),
            vec![Model {
                id: "claude-sonnet-4-5".to_string(),
                name: "Claude Sonnet 4.5".to_string(),
                description: "Latest Claude Sonnet".to_string(),
                context_length: Some(200_000),
            }],
        )]);

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
        assert!(result
            .expect_err("expected invalid provider error")
            .to_string()
            .contains("unknown"));
    }

    #[test]
    fn test_validate_opencode_agents_returns_typed_error() {
        let catalog = mock_catalog();
        let fallback = create_fallback_with_refs(&["opencode/unknown/claude-sonnet-4-5"]);
        let resolved = fallback.resolve_drains();

        let error = validate_opencode_agents(&resolved, &catalog)
            .expect_err("invalid provider should return a typed validation error");

        assert!(matches!(
            error,
            OpenCodeValidationError::InvalidReferences { .. }
        ));
    }

    #[test]
    fn test_validate_opencode_agents_invalid_model() {
        let catalog = mock_catalog();
        let fallback = create_fallback_with_refs(&["opencode/anthropic/unknown-model"]);
        let resolved = fallback.resolve_drains();

        let result = validate_opencode_agents(&resolved, &catalog);
        assert!(result.is_err());
        assert!(result
            .expect_err("expected invalid model error")
            .to_string()
            .contains("unknown-model"));
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
