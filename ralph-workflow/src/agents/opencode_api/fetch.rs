//! `OpenCode` API catalog fetching.
//!
//! This module handles HTTP requests to fetch the `OpenCode` model catalog
//! from <https://models.dev/api.json>.

use crate::agents::opencode_api::cache::{save_catalog, CacheError, CacheWarning};
use crate::agents::opencode_api::types::ApiCatalog;
use crate::agents::opencode_api::API_URL;

// fetch_url lives in boundary module io/ - this import is the architectural seam
// where non-boundary code uses boundary capability
use crate::io::http_fetch::fetch_url;

/// Fetch the API catalog JSON from the provided HTTP client and parse it.
pub fn fetch_api_catalog(ttl_seconds: u64) -> Result<(ApiCatalog, Vec<CacheWarning>), CacheError> {
    let json = fetch_url(API_URL).map_err(CacheError::FetchError)?;

    let catalog: ApiCatalog = serde_json::from_str(&json).map_err(CacheError::ParseError)?;

    let catalog = ApiCatalog {
        ttl_seconds,
        cached_at: Some(chrono::Utc::now()),
        ..catalog
    };

    let warnings: Vec<CacheWarning> = save_catalog(&catalog)
        .err()
        .map(|e| CacheWarning::CacheSaveFailed {
            error: e.to_string(),
        })
        .into_iter()
        .collect();

    Ok((catalog, warnings))
}

/// Fetch the API catalog with cache (deprecated - use `load_api_catalog` from cache module).
/// Fetch the API catalog with cache (deprecated - use `load_api_catalog` from cache module).
#[allow(dead_code)]
pub fn fetch_api_catalog_with_cache(
    ttl_seconds: u64,
) -> Result<(ApiCatalog, Vec<CacheWarning>), CacheError> {
    fetch_api_catalog(ttl_seconds)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::opencode_api::types::{Model, Provider};
    use crate::agents::opencode_api::DEFAULT_CACHE_TTL_SECONDS;
    use std::collections::HashMap;

    /// Create a mock API catalog for testing.
    pub fn mock_api_catalog() -> ApiCatalog {
        let providers = HashMap::from([
            (
                "opencode".to_string(),
                Provider {
                    id: "opencode".to_string(),
                    name: "OpenCode".to_string(),
                    description: "Open source AI coding tool".to_string(),
                },
            ),
            (
                "anthropic".to_string(),
                Provider {
                    id: "anthropic".to_string(),
                    name: "Anthropic".to_string(),
                    description: "Anthropic Claude models".to_string(),
                },
            ),
            (
                "openai".to_string(),
                Provider {
                    id: "openai".to_string(),
                    name: "OpenAI".to_string(),
                    description: "OpenAI GPT models".to_string(),
                },
            ),
        ]);

        let models = HashMap::from([
            (
                "opencode".to_string(),
                vec![Model {
                    id: "glm-4.7-free".to_string(),
                    name: "GLM-4.7 Free".to_string(),
                    description: "Open source GLM model".to_string(),
                    context_length: Some(128_000),
                }],
            ),
            (
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
            ),
            (
                "openai".to_string(),
                vec![Model {
                    id: "gpt-4".to_string(),
                    name: "GPT-4".to_string(),
                    description: "OpenAI's GPT-4".to_string(),
                    context_length: Some(8192),
                }],
            ),
        ]);

        ApiCatalog {
            providers,
            models,
            cached_at: Some(chrono::Utc::now()),
            ttl_seconds: DEFAULT_CACHE_TTL_SECONDS,
        }
    }

    #[test]
    fn test_mock_api_catalog_structure() {
        let catalog = mock_api_catalog();

        assert_eq!(catalog.providers.len(), 3);
        assert!(catalog.has_provider("opencode"));
        assert!(catalog.has_provider("anthropic"));
        assert!(catalog.has_provider("openai"));

        assert!(catalog.has_model("opencode", "glm-4.7-free"));
        assert!(catalog.has_model("anthropic", "claude-sonnet-4-5"));
        assert!(catalog.has_model("anthropic", "claude-opus-4"));
        assert!(catalog.has_model("openai", "gpt-4"));

        let model = catalog.get_model("anthropic", "claude-sonnet-4-5").unwrap();
        assert_eq!(model.id, "claude-sonnet-4-5");
        assert_eq!(model.context_length, Some(200_000));
    }

    #[test]
    fn test_catalog_ttl_default() {
        let catalog = mock_api_catalog();
        assert_eq!(catalog.ttl_seconds, DEFAULT_CACHE_TTL_SECONDS);
    }

    #[test]
    fn test_api_url_constant() {
        assert_eq!(API_URL, "https://models.dev/api.json");
    }
}
