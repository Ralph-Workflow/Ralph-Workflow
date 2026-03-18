//! `OpenCode` API catalog data structures.
//!
//! Parses and represents the `OpenCode` model catalog (see <https://models.dev/api.json>), and a
//! simplified cache format used for local storage.

use serde::{Deserialize, Deserializer, Serialize};
use std::collections::HashMap;

use crate::agents::opencode_api::DEFAULT_CACHE_TTL_SECONDS;

/// `OpenCode` API catalog containing all available providers and models.
#[derive(Debug, Clone)]
pub struct ApiCatalog {
    /// All providers supported by `OpenCode`.
    pub providers: HashMap<String, Provider>,
    /// All models supported by `OpenCode`, indexed by provider name.
    pub models: HashMap<String, Vec<Model>>,
    /// When this catalog was cached (for TTL tracking).
    pub cached_at: Option<chrono::DateTime<chrono::Utc>>,
    /// Time-to-live in seconds for this catalog.
    pub ttl_seconds: u64,
}

impl<'de> Deserialize<'de> for ApiCatalog {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        // First, deserialize to a generic JSON value
        let value = serde_json::Value::deserialize(deserializer)?;

        // Check if this is the cache format (has "providers" and "models" keys)
        if value.get("providers").is_some() && value.get("models").is_some() {
            // Cache format
            let cache_format: CacheFormat =
                serde_json::from_value(value).map_err(serde::de::Error::custom)?;
            Ok(Self {
                providers: cache_format.providers,
                models: cache_format.models,
                cached_at: None,
                ttl_seconds: DEFAULT_CACHE_TTL_SECONDS,
            })
        } else {
            // API format - parse as provider entries
            let api_format: HashMap<String, RawProviderEntry> =
                serde_json::from_value(value).map_err(serde::de::Error::custom)?;
            Ok(Self::from(api_format))
        }
    }
}

/// Cache format for local storage.
#[derive(Debug, Clone, Deserialize)]
struct CacheFormat {
    providers: HashMap<String, Provider>,
    models: HashMap<String, Vec<Model>>,
}

/// Raw provider entry as returned by the API.
/// Each provider has an embedded `models` object with model IDs as keys.
#[derive(Debug, Clone, Deserialize)]
struct RawProviderEntry {
    id: String,
    name: String,
    #[serde(default)]
    doc: Option<String>,
    #[serde(default)]
    models: HashMap<String, RawModel>,
}

/// Raw model entry as returned by the API.
#[derive(Debug, Clone, Deserialize)]
struct RawModel {
    id: String,
    name: String,
    #[serde(default)]
    family: Option<String>,
    #[serde(default)]
    limit: Option<RawModelLimit>,
}

/// Raw model limit structure from API.
#[derive(Debug, Clone, Deserialize)]
struct RawModelLimit {
    #[serde(default)]
    context: Option<usize>,
}

impl From<HashMap<String, RawProviderEntry>> for ApiCatalog {
    fn from(raw: HashMap<String, RawProviderEntry>) -> Self {
        let entries: Vec<_> = raw
            .into_iter()
            .map(|(provider_id, entry)| {
                let provider = Provider {
                    id: entry.id.clone(),
                    name: entry.name.clone(),
                    description: entry.doc.unwrap_or_default(),
                };

                let model_list: Vec<Model> = entry
                    .models
                    .into_values()
                    .map(|m| Model {
                        id: m.id,
                        name: m.name,
                        description: m.family.unwrap_or_default(),
                        context_length: m.limit.and_then(|l| l.context),
                    })
                    .collect();

                (provider_id, provider, model_list)
            })
            .collect();

        let providers: HashMap<_, _> = entries
            .iter()
            .map(|(id, provider, _)| (id.clone(), provider.clone()))
            .collect();

        let models: HashMap<_, _> = entries
            .iter()
            .filter_map(|(id, _, model_list)| {
                if model_list.is_empty() {
                    None
                } else {
                    Some((id.clone(), model_list.clone()))
                }
            })
            .collect();

        Self {
            providers,
            models,
            cached_at: None,
            ttl_seconds: DEFAULT_CACHE_TTL_SECONDS,
        }
    }
}

impl ApiCatalog {
    /// Check if the catalog is expired based on its `cached_at` timestamp and TTL.
    #[must_use]
    pub fn is_expired(&self) -> bool {
        self.cached_at.is_none_or(|cached_at| {
            let now = chrono::Utc::now();
            let elapsed = now.signed_duration_since(cached_at);
            elapsed.num_seconds().cast_unsigned() > self.ttl_seconds
        })
    }

    /// Check if a provider exists in the catalog.
    #[must_use]
    pub fn has_provider(&self, provider: &str) -> bool {
        self.providers.contains_key(provider)
    }

    /// Check if a specific model exists for a provider.
    #[must_use]
    pub fn has_model(&self, provider: &str, model: &str) -> bool {
        self.models
            .get(provider)
            .is_some_and(|models| models.iter().any(|m| m.id == model))
    }

    /// Get all model IDs for a provider.
    #[must_use]
    pub fn get_model_ids(&self, provider: &str) -> Vec<String> {
        self.models
            .get(provider)
            .map(|models| models.iter().map(|m| m.id.clone()).collect())
            .unwrap_or_default()
    }

    /// Get all provider names.
    #[must_use]
    pub fn provider_names(&self) -> Vec<String> {
        self.providers.keys().cloned().collect()
    }
}

/// Helper methods for `ApiCatalog` (test-only).
#[cfg(test)]
impl ApiCatalog {
    /// Get a model by provider and model ID.
    #[must_use]
    pub fn get_model(&self, provider: &str, model_id: &str) -> Option<&Model> {
        self.models
            .get(provider)
            .and_then(|models| models.iter().find(|m| m.id == model_id))
    }

    /// Find providers that start with the given prefix.
    #[must_use]
    pub fn find_providers_by_prefix(&self, prefix: &str) -> Vec<String> {
        let prefix_lower = prefix.to_lowercase();
        self.provider_names()
            .into_iter()
            .filter(|p| p.to_lowercase().starts_with(&prefix_lower))
            .collect()
    }

    /// Find models for a provider that start with the given prefix.
    #[must_use]
    pub fn find_models_by_prefix(&self, provider: &str, prefix: &str) -> Vec<String> {
        let prefix_lower = prefix.to_lowercase();
        self.get_model_ids(provider)
            .into_iter()
            .filter(|m| m.to_lowercase().starts_with(&prefix_lower))
            .collect()
    }
}

/// A provider supported by `OpenCode`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Provider {
    /// Unique provider identifier.
    pub id: String,
    /// Display name for the provider.
    pub name: String,
    /// Optional description of the provider.
    #[serde(default)]
    pub description: String,
}

/// A model available from a provider.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Model {
    /// Unique model identifier (e.g., "claude-sonnet-4-5").
    pub id: String,
    /// Display name for the model.
    pub name: String,
    /// Optional description of the model.
    #[serde(default)]
    pub description: String,
    /// Optional context window size.
    #[serde(default)]
    pub context_length: Option<usize>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create_test_catalog() -> ApiCatalog {
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
            ttl_seconds: DEFAULT_CACHE_TTL_SECONDS,
        }
    }

    #[test]
    fn test_catalog_not_expired_when_fresh() {
        let catalog = create_test_catalog();
        assert!(!catalog.is_expired());
    }

    #[test]
    fn test_catalog_expired_when_old() {
        let mut catalog = create_test_catalog();
        catalog.cached_at = Some(
            chrono::Utc::now()
                - chrono::Duration::seconds(DEFAULT_CACHE_TTL_SECONDS.cast_signed() + 1),
        );
        assert!(catalog.is_expired());
    }

    #[test]
    fn test_catalog_expired_without_timestamp() {
        let mut catalog = create_test_catalog();
        catalog.cached_at = None;
        assert!(catalog.is_expired());
    }

    #[test]
    fn test_has_provider() {
        let catalog = create_test_catalog();
        assert!(catalog.has_provider("anthropic"));
        assert!(catalog.has_provider("openai"));
        assert!(!catalog.has_provider("nonexistent"));
    }

    #[test]
    fn test_has_model() {
        let catalog = create_test_catalog();
        assert!(catalog.has_model("anthropic", "claude-sonnet-4-5"));
        assert!(catalog.has_model("anthropic", "claude-opus-4"));
        assert!(catalog.has_model("openai", "gpt-4"));
        assert!(!catalog.has_model("anthropic", "gpt-4"));
        assert!(!catalog.has_model("nonexistent", "any-model"));
    }

    #[test]
    fn test_get_model_ids() {
        let catalog = create_test_catalog();
        let anthropic_models = catalog.get_model_ids("anthropic");
        assert_eq!(anthropic_models.len(), 2);
        assert!(anthropic_models.contains(&"claude-sonnet-4-5".to_string()));
        assert!(anthropic_models.contains(&"claude-opus-4".to_string()));

        let openai_models = catalog.get_model_ids("openai");
        assert_eq!(openai_models.len(), 1);
        assert!(openai_models.contains(&"gpt-4".to_string()));

        let nonexistent_models = catalog.get_model_ids("nonexistent");
        assert!(nonexistent_models.is_empty());
    }

    #[test]
    fn test_get_model() {
        let catalog = create_test_catalog();
        let model = catalog.get_model("anthropic", "claude-sonnet-4-5");
        assert!(model.is_some());
        assert_eq!(model.unwrap().id, "claude-sonnet-4-5");

        assert!(catalog.get_model("nonexistent", "any").is_none());
        assert!(catalog.get_model("anthropic", "nonexistent").is_none());
    }

    #[test]
    fn test_provider_names() {
        let catalog = create_test_catalog();
        let names = catalog.provider_names();
        assert_eq!(names.len(), 2);
        assert!(names.contains(&"anthropic".to_string()));
        assert!(names.contains(&"openai".to_string()));
    }

    #[test]
    fn test_find_providers_by_prefix() {
        let catalog = create_test_catalog();
        let results = catalog.find_providers_by_prefix("anth");
        assert_eq!(results, vec!["anthropic".to_string()]);

        let results = catalog.find_providers_by_prefix("a");
        assert_eq!(results, vec!["anthropic".to_string()]);

        let results = catalog.find_providers_by_prefix("nonexistent");
        assert!(results.is_empty());
    }

    #[test]
    fn test_find_models_by_prefix() {
        let catalog = create_test_catalog();
        let results = catalog.find_models_by_prefix("anthropic", "claude-son");
        assert_eq!(results, vec!["claude-sonnet-4-5".to_string()]);

        let results = catalog.find_models_by_prefix("anthropic", "claude");
        assert_eq!(results.len(), 2);

        let results = catalog.find_models_by_prefix("nonexistent", "any");
        assert!(results.is_empty());
    }

    #[test]
    fn test_deserialize_real_api_format() {
        // This is a simplified version of the actual models.dev/api.json format.
        let json = r#"{"opencode":{"id":"opencode","name":"OpenCode Zen","doc":"https://opencode.ai/docs/zen","models":{"glm-4.7-free":{"id":"glm-4.7-free","name":"GLM-4.7","family":"glm-free","limit":{"context":204800,"output":131072}},"claude-sonnet-4-5":{"id":"claude-sonnet-4-5","name":"Claude Sonnet 4.5","family":"claude-sonnet","limit":{"context":1000000,"output":64000}}}},"anthropic":{"id":"anthropic","name":"Anthropic","doc":"https://docs.anthropic.com","models":{"claude-opus-4":{"id":"claude-opus-4","name":"Claude Opus 4","family":"claude-opus","limit":{"context":200000,"output":32000}}}}}"#;

        let catalog: ApiCatalog = serde_json::from_str(json).expect("Failed to parse API format");

        // Check providers were parsed correctly
        assert!(catalog.has_provider("opencode"));
        assert!(catalog.has_provider("anthropic"));
        assert_eq!(catalog.providers.len(), 2);

        // Check opencode provider details
        let opencode = catalog.providers.get("opencode").unwrap();
        assert_eq!(opencode.id, "opencode");
        assert_eq!(opencode.name, "OpenCode Zen");

        // Check models were parsed correctly
        assert!(catalog.has_model("opencode", "glm-4.7-free"));
        assert!(catalog.has_model("opencode", "claude-sonnet-4-5"));
        assert!(catalog.has_model("anthropic", "claude-opus-4"));

        // Check model details
        let opencode_models = catalog.get_model_ids("opencode");
        assert_eq!(opencode_models.len(), 2);
        assert!(opencode_models.contains(&"glm-4.7-free".to_string()));
        assert!(opencode_models.contains(&"claude-sonnet-4-5".to_string()));
    }

    #[test]
    fn test_deserialize_cache_format() {
        // This is the format used for local caching.
        let json = r#"{"providers":{"opencode":{"id":"opencode","name":"OpenCode Zen","description":"OpenCode API"}},"models":{"opencode":[{"id":"glm-4.7-free","name":"GLM-4.7","description":"Free GLM model","context_length":204800}]}}"#;

        let catalog: ApiCatalog = serde_json::from_str(json).expect("Failed to parse cache format");

        assert!(catalog.has_provider("opencode"));
        assert!(catalog.has_model("opencode", "glm-4.7-free"));
    }
}
