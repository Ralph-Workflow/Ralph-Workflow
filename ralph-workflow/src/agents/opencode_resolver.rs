//! `OpenCode` agent resolver for dynamic provider/model configuration.
//!
//! This module provides dynamic agent resolution for `OpenCode` using the syntax
//! `opencode/provider/model` (e.g., `opencode/anthropic/claude-sonnet-4-5`).
//!
//! The resolver validates provider/model combinations against the `OpenCode` API
//! catalog and generates `AgentConfig` instances on-the-fly with the appropriate
//! command-line flags.
//!
//! # Usage
//!
//! ```ignore
//! // In agent chain configuration:
//! [agent_chains]
//! developer = ["opencode/anthropic/claude-sonnet-4-5", "claude"]
//!
//! [agent_drains]
//! planning = "developer"
//! development = "developer"
//! ```
//!
//! # Supported Patterns
//!
//! - `opencode/provider/model` - Dynamic provider/model from API catalog
//! - `opencode` - Base `OpenCode` agent (uses default or user-specified provider/model)

use crate::agents::config::AgentConfig;
use crate::agents::opencode_api::ApiCatalog;
use crate::agents::parser::JsonParserType;
use strsim::levenshtein;

/// Maximum Levenshtein distance for typo suggestions.
///
/// Strings with edit distance <= this value are considered potential typos.
const MAX_TYPO_DISTANCE: usize = 3;

/// `OpenCode` agent resolver for dynamic provider/model configuration.
///
/// Validates provider/model combinations against the `OpenCode` API catalog
/// and generates `AgentConfig` instances with the appropriate command-line flags.
pub struct OpenCodeResolver {
    /// `OpenCode` API catalog with available providers and models.
    catalog: ApiCatalog,
}

impl OpenCodeResolver {
    /// Create a new `OpenCode` resolver with the given API catalog.
    pub const fn new(catalog: ApiCatalog) -> Self {
        Self { catalog }
    }

    /// Try to resolve an agent name to an `AgentConfig`.
    ///
    /// Supports the following patterns:
    /// - `opencode` - Plain `OpenCode` agent (uses `OpenCode`'s default model)
    /// - `opencode/provider/model` - Dynamic provider/model from API catalog
    ///
    /// Returns `None` if the name doesn't match the `OpenCode` pattern or if
    /// the provider/model combination is not found in the catalog.
    pub fn try_resolve(&self, name: &str) -> Option<AgentConfig> {
        // Handle plain "opencode" - use default (no model flag)
        if name == "opencode" {
            return Some(Self::build_default_config());
        }

        // Check if it starts with "opencode/"
        if !name.starts_with("opencode/") {
            return None;
        }

        // Parse the pattern: "opencode/provider/model"
        let parts: Vec<&str> = name.split('/').collect();
        if parts.len() != 3 {
            return None;
        }

        let provider = parts[1];
        let model = parts[2];

        // Validate provider and model exist in catalog
        if !self.catalog.has_provider(provider) {
            return None;
        }
        if !self.catalog.has_model(provider, model) {
            return None;
        }

        // Build the agent config
        Some(Self::build_config(provider, model))
    }

    /// Build an `AgentConfig` for the given provider/model.
    fn build_config(provider: &str, model: &str) -> AgentConfig {
        // OpenCode command syntax: opencode run -m provider/model
        // The model_flag is the "-m provider/model" part
        let model_flag = format!("-m {provider}/{model}");

        // Set OPENCODE_PERMISSION to allow all tool actions without prompting
        // This is required for non-interactive/headless execution
        // The value must be a JSON object where keys are permission types and values are actions
        // Using {"*": "allow"} grants all permissions for all patterns
        let mut env_vars = std::collections::HashMap::new();
        env_vars.insert(
            "OPENCODE_PERMISSION".to_string(),
            r#"{"*": "allow"}"#.to_string(),
        );

        AgentConfig {
            cmd: "opencode run".to_string(),
            output_flag: "--format json".to_string(),
            // OpenCode doesn't have a CLI yolo flag - permissions are controlled
            // via OPENCODE_PERMISSION environment variable (set above)
            yolo_flag: String::new(),
            verbose_flag: "--log-level DEBUG --print-logs".to_string(),
            can_commit: true,
            json_parser: JsonParserType::OpenCode,
            model_flag: Some(model_flag),
            print_flag: String::new(),
            streaming_flag: String::new(),
            // Session continuation: -s <session_id> (from `opencode run --help`)
            // Allows XSD retries to continue the same conversation so AI retains memory
            session_flag: "-s {}".to_string(),
            env_vars,
            display_name: Some(format!("OpenCode ({provider})")),
        }
    }

    /// Build an `AgentConfig` for plain "opencode" (no provider/model specified).
    /// `OpenCode` will use its default model configuration.
    fn build_default_config() -> AgentConfig {
        let mut env_vars = std::collections::HashMap::new();
        env_vars.insert(
            "OPENCODE_PERMISSION".to_string(),
            r#"{"*": "allow"}"#.to_string(),
        );

        AgentConfig {
            cmd: "opencode run".to_string(),
            output_flag: "--format json".to_string(),
            // OpenCode doesn't have a CLI yolo flag - permissions are controlled
            // via OPENCODE_PERMISSION environment variable (set above)
            yolo_flag: String::new(),
            verbose_flag: "--log-level DEBUG --print-logs".to_string(),
            can_commit: true,
            json_parser: JsonParserType::OpenCode,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: String::new(),
            // Session continuation: -s <session_id> (from `opencode run --help`)
            // Allows XSD retries to continue the same conversation so AI retains memory
            session_flag: "-s {}".to_string(),
            env_vars,
            display_name: Some("OpenCode".to_string()),
        }
    }

    /// Validate a provider/model combination.
    ///
    /// Returns an error if the provider or model doesn't exist in the catalog.
    pub fn validate(&self, provider: &str, model: &str) -> Result<(), ValidationError> {
        if !self.catalog.has_provider(provider) {
            return Err(ValidationError::ProviderNotFound {
                provider: provider.to_string(),
                suggestions: self.suggest_providers(provider),
            });
        }

        if !self.catalog.has_model(provider, model) {
            return Err(ValidationError::ModelNotFound {
                provider: provider.to_string(),
                model: model.to_string(),
                suggestions: self.suggest_models(provider, model),
            });
        }

        Ok(())
    }

    /// Suggest similar provider names for a typo.
    fn suggest_providers(&self, provider: &str) -> Vec<String> {
        let mut suggestions: Vec<_> = self
            .catalog
            .provider_names()
            .into_iter()
            .map(|p| {
                let distance = levenshtein(provider, &p);
                (p, distance)
            })
            .filter(|(_, d)| *d <= MAX_TYPO_DISTANCE)
            .collect();

        suggestions.sort_by_key(|(_, d)| *d);
        suggestions
            .into_iter()
            .map(|(p, _)| p)
            .take(MAX_TYPO_DISTANCE)
            .collect()
    }

    /// Suggest similar model names for a typo.
    fn suggest_models(&self, provider: &str, model: &str) -> Vec<String> {
        let mut suggestions: Vec<_> = self
            .catalog
            .get_model_ids(provider)
            .into_iter()
            .map(|m| {
                let distance = levenshtein(model, &m);
                (m, distance)
            })
            .filter(|(_, d)| *d <= MAX_TYPO_DISTANCE)
            .collect();

        suggestions.sort_by_key(|(_, d)| *d);
        suggestions
            .into_iter()
            .map(|(m, _)| m)
            .take(MAX_TYPO_DISTANCE)
            .collect()
    }

    /// Get a user-friendly error message for a validation error.
    pub fn format_error(&self, error: &ValidationError, agent_name: &str) -> String {
        match error {
            ValidationError::ProviderNotFound {
                provider,
                suggestions,
            } => {
                use std::fmt::Write;
                let mut msg =
                    format!("Error: OpenCode provider '{provider}' not found in API catalog.\n");
                if let Some(closest) = suggestions.first() {
                    writeln!(msg, "Did you mean: {closest}?").unwrap();
                }
                writeln!(msg, "Agent reference: {agent_name}").unwrap();
                msg.push_str("Available providers: ");
                msg.push_str(&self.catalog.provider_names().join(", "));
                msg.push_str("\n\nPlease update your agent configuration.");
                msg
            }
            ValidationError::ModelNotFound {
                provider,
                model,
                suggestions,
            } => {
                use std::fmt::Write;
                let mut msg = format!(
                    "Error: OpenCode model '{provider}/{model}' not found in API catalog.\n"
                );
                if let Some(closest) = suggestions.first() {
                    writeln!(msg, "Did you mean: {provider}/{closest}?").unwrap();
                }
                writeln!(msg, "Agent reference: {agent_name}").unwrap();
                write!(msg, "Available models for '{provider}': ").unwrap();
                msg.push_str(&self.catalog.get_model_ids(provider).join(", "));
                msg.push_str("\n\nPlease update your agent configuration.");
                msg
            }
        }
    }
}

/// Errors that can occur during `OpenCode` agent validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ValidationError {
    /// Provider not found in the API catalog.
    ProviderNotFound {
        provider: String,
        suggestions: Vec<String>,
    },
    /// Model not found for the given provider.
    ModelNotFound {
        provider: String,
        model: String,
        suggestions: Vec<String>,
    },
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::opencode_api::{Model, Provider};
    use std::collections::HashMap;

    fn mock_api_catalog() -> ApiCatalog {
        let mut providers = HashMap::new();
        providers.insert(
            "anthropic".to_string(),
            Provider {
                id: "anthropic".to_string(),
                name: "Anthropic".to_string(),
                description: "Anthropic Claude models".to_string(),
            },
        );
        providers.insert(
            "openai".to_string(),
            Provider {
                id: "openai".to_string(),
                name: "OpenAI".to_string(),
                description: "OpenAI GPT models".to_string(),
            },
        );

        let mut models = HashMap::new();
        models.insert(
            "anthropic".to_string(),
            vec![
                Model {
                    id: "claude-sonnet-4-5".to_string(),
                    name: "Claude Sonnet 4.5".to_string(),
                    description: "Latest Claude Sonnet".to_string(),
                    context_length: Some(200_000),
                },
                Model {
                    id: "claude-opus-4".to_string(),
                    name: "Claude Opus 4".to_string(),
                    description: "Most capable Claude".to_string(),
                    context_length: Some(200_000),
                },
            ],
        );
        models.insert(
            "openai".to_string(),
            vec![Model {
                id: "gpt-4".to_string(),
                name: "GPT-4".to_string(),
                description: "OpenAI's GPT-4".to_string(),
                context_length: Some(8192),
            }],
        );

        ApiCatalog {
            providers,
            models,
            cached_at: Some(chrono::Utc::now()),
            ttl_seconds: 86400,
        }
    }

    #[test]
    fn test_try_resolve_valid_pattern() {
        let catalog = mock_api_catalog();
        let resolver = OpenCodeResolver::new(catalog);

        let config = resolver.try_resolve("opencode/anthropic/claude-sonnet-4-5");
        assert!(config.is_some());

        let config = config.unwrap();
        assert_eq!(config.cmd, "opencode run");
        assert_eq!(
            config.model_flag,
            Some("-m anthropic/claude-sonnet-4-5".to_string())
        );
        assert_eq!(config.json_parser, JsonParserType::OpenCode);
    }

    #[test]
    fn test_try_resolve_plain_opencode() {
        let catalog = mock_api_catalog();
        let resolver = OpenCodeResolver::new(catalog);

        let config = resolver.try_resolve("opencode");
        assert!(config.is_some());

        let config = config.unwrap();
        assert_eq!(config.cmd, "opencode run");
        assert_eq!(config.model_flag, None); // No model flag for default
        assert_eq!(config.json_parser, JsonParserType::OpenCode);
        assert_eq!(
            config.env_vars.get("OPENCODE_PERMISSION"),
            Some(&r#"{"*": "allow"}"#.to_string())
        );
        assert_eq!(config.display_name, Some("OpenCode".to_string()));
    }

    #[test]
    fn test_try_resolve_invalid_pattern() {
        let catalog = mock_api_catalog();
        let resolver = OpenCodeResolver::new(catalog);

        // Not an opencode pattern
        assert!(resolver.try_resolve("claude").is_none());
        assert!(resolver.try_resolve("ccs/glm").is_none());

        // Malformed opencode pattern (missing model)
        assert!(resolver.try_resolve("opencode/anthropic").is_none());

        // Unknown provider
        assert!(resolver.try_resolve("opencode/unknown/model").is_none());

        // Unknown model
        assert!(resolver
            .try_resolve("opencode/anthropic/unknown-model")
            .is_none());
    }

    #[test]
    fn test_validate_valid_provider_model() {
        let catalog = mock_api_catalog();
        let resolver = OpenCodeResolver::new(catalog);

        assert!(resolver.validate("anthropic", "claude-sonnet-4-5").is_ok());
        assert!(resolver.validate("openai", "gpt-4").is_ok());
    }

    #[test]
    fn test_validate_invalid_provider() {
        let catalog = mock_api_catalog();
        let resolver = OpenCodeResolver::new(catalog);

        let result = resolver.validate("unknown", "model");
        assert!(result.is_err());

        if let Err(ValidationError::ProviderNotFound { provider, .. }) = result {
            assert_eq!(provider, "unknown");
        } else {
            panic!("Expected ProviderNotFound error");
        }
    }

    #[test]
    fn test_validate_invalid_model() {
        let catalog = mock_api_catalog();
        let resolver = OpenCodeResolver::new(catalog);

        let result = resolver.validate("anthropic", "unknown-model");
        assert!(result.is_err());

        if let Err(ValidationError::ModelNotFound { model, .. }) = result {
            assert_eq!(model, "unknown-model");
        } else {
            panic!("Expected ModelNotFound error");
        }
    }

    #[test]
    fn test_build_config() {
        let catalog = mock_api_catalog();
        let _resolver = OpenCodeResolver::new(catalog);

        let config = OpenCodeResolver::build_config("anthropic", "claude-sonnet-4-5");

        assert_eq!(config.cmd, "opencode run");
        assert_eq!(
            config.model_flag,
            Some("-m anthropic/claude-sonnet-4-5".to_string())
        );
        assert_eq!(config.output_flag, "--format json");
        // OpenCode doesn't have a CLI yolo flag - permissions via OPENCODE_PERMISSION env var
        assert_eq!(config.yolo_flag, "");
        assert_eq!(config.json_parser, JsonParserType::OpenCode);
        assert!(config.can_commit);
        // Verify OPENCODE_PERMISSION is set for non-interactive mode
        // The value is a JSON object that grants all permissions
        assert_eq!(
            config.env_vars.get("OPENCODE_PERMISSION"),
            Some(&r#"{"*": "allow"}"#.to_string())
        );
    }

    #[test]
    fn test_format_error_provider_not_found() {
        let catalog = mock_api_catalog();
        let resolver = OpenCodeResolver::new(catalog);

        let error = ValidationError::ProviderNotFound {
            provider: "antrhopic".to_string(),
            suggestions: vec!["anthropic".to_string()],
        };

        let msg = resolver.format_error(&error, "opencode/antrhopic/claude-sonnet-4-5");

        assert!(msg.contains("antrhopic"));
        assert!(msg.contains("anthropic"));
        assert!(msg.contains("opencode/antrhopic/claude-sonnet-4-5"));
        assert!(msg.contains("Available providers"));
    }

    #[test]
    fn test_format_error_model_not_found() {
        let catalog = mock_api_catalog();
        let resolver = OpenCodeResolver::new(catalog);

        let error = ValidationError::ModelNotFound {
            provider: "anthropic".to_string(),
            model: "claude-sonnet-4".to_string(),
            suggestions: vec!["claude-sonnet-4-5".to_string()],
        };

        let msg = resolver.format_error(&error, "opencode/anthropic/claude-sonnet-4");

        assert!(msg.contains("anthropic/claude-sonnet-4"));
        assert!(msg.contains("claude-sonnet-4-5"));
        assert!(msg.contains("Available models"));
    }
}
