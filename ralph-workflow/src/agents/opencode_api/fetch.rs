//! `OpenCode` API catalog fetching.
//!
//! This module handles HTTP requests to fetch the `OpenCode` model catalog
//! from <https://models.dev/api.json>.

use crate::agents::opencode_api::cache::{save_catalog, CacheError, CacheWarning};
use crate::agents::opencode_api::types::ApiCatalog;
use crate::agents::opencode_api::API_URL;
use std::fmt;
use std::sync::Arc;

/// HTTP capability abstraction for catalog fetching.
///
/// Allows domain code to request HTTP bodies without importing the boundary module.
pub trait HttpFetcher: Send + Sync {
    /// Fetch the body of the given URL.
    fn fetch(&self, url: &str) -> Result<String, HttpFetchError>;
}

/// Errors produced while fetching HTTP resources.
#[derive(Debug)]
pub enum HttpFetchError {
    /// Underlying HTTP capability failure described by the provider.
    RequestFailed(String),
}

impl fmt::Display for HttpFetchError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            HttpFetchError::RequestFailed(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for HttpFetchError {}

/// Trait for fetching the `OpenCode` API catalog.
///
/// This trait enables dependency injection for catalog fetching,
/// allowing tests to provide mock implementations that don't make network calls.
pub trait CatalogHttpClient: Send + Sync {
    /// Fetch the API catalog JSON and parse it.
    fn fetch_api_catalog(
        &self,
        ttl_seconds: u64,
    ) -> Result<(ApiCatalog, Vec<CacheWarning>), CacheError>;
}

/// Production implementation of [`CatalogHttpClient`] that fetches from the network.
#[derive(Clone)]
pub struct RealCatalogFetcher {
    fetcher: Arc<dyn HttpFetcher>,
    persist_catalog: Arc<CatalogPersistFn>,
}

type CatalogPersistFn = dyn Fn(&ApiCatalog) -> Result<(), CacheError> + Send + Sync;

impl std::fmt::Debug for RealCatalogFetcher {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RealCatalogFetcher").finish()
    }
}

impl RealCatalogFetcher {
    /// Build a catalog fetcher backed by the given HTTP capability.
    #[must_use]
    pub fn with_fetcher(fetcher: Arc<dyn HttpFetcher>) -> Self {
        Self {
            fetcher,
            persist_catalog: Arc::new(save_catalog),
        }
    }

    /// Build a catalog fetcher from any type that implements the fetcher trait.
    #[must_use]
    pub fn with_http_fetcher<F>(fetcher: F) -> Self
    where
        F: HttpFetcher + 'static,
    {
        Self::with_fetcher(Arc::new(fetcher))
    }

    #[cfg(test)]
    fn with_fetcher_and_persist<F, P>(fetcher: F, persist_catalog: P) -> Self
    where
        F: HttpFetcher + 'static,
        P: Fn(&ApiCatalog) -> Result<(), CacheError> + Send + Sync + 'static,
    {
        Self {
            fetcher: Arc::new(fetcher),
            persist_catalog: Arc::new(persist_catalog),
        }
    }
}

impl CatalogHttpClient for RealCatalogFetcher {
    fn fetch_api_catalog(
        &self,
        ttl_seconds: u64,
    ) -> Result<(ApiCatalog, Vec<CacheWarning>), CacheError> {
        let json = self
            .fetcher
            .fetch(API_URL)
            .map_err(|err| CacheError::FetchError(err.to_string()))?;

        let catalog: ApiCatalog = serde_json::from_str(&json).map_err(CacheError::ParseError)?;

        let catalog = ApiCatalog {
            ttl_seconds,
            cached_at: Some(chrono::Utc::now()),
            ..catalog
        };

        let warnings: Vec<CacheWarning> = (self.persist_catalog)(&catalog)
            .err()
            .map(|e| CacheWarning::CacheSaveFailed {
                error: e.to_string(),
            })
            .into_iter()
            .collect();

        Ok((catalog, warnings))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::opencode_api::types::{Model, Provider};
    use crate::agents::opencode_api::DEFAULT_CACHE_TTL_SECONDS;
    use std::collections::HashMap;
    /// Create a mock API catalog for testing.
    fn mock_api_catalog() -> ApiCatalog {
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

    #[test]
    fn test_real_catalog_fetcher_uses_injected_http_fetcher() {
        struct StubFetcher {
            payload: &'static str,
        }

        impl HttpFetcher for StubFetcher {
            fn fetch(&self, _url: &str) -> Result<String, HttpFetchError> {
                Ok(self.payload.to_string())
            }
        }

        let stub_catalog = r#"{
            "test-provider": {
                "id": "test-provider",
                "name": "Test Provider",
                "doc": "used for fixture",
                "models": {
                    "test-model": {
                        "id": "test-model",
                        "name": "Test Model",
                        "family": "Lorem",
                        "limit": { "context": 4096 }
                    }
                }
            }
        }"#;

        let fetcher = RealCatalogFetcher::with_fetcher_and_persist(
            StubFetcher {
                payload: stub_catalog,
            },
            |_catalog| Ok(()),
        );

        let (catalog, warnings) = fetcher.fetch_api_catalog(1234).unwrap();
        assert!(warnings.is_empty());
        assert_eq!(catalog.ttl_seconds, 1234);
        assert!(catalog.has_provider("test-provider"));
        assert!(catalog.has_model("test-provider", "test-model"));
    }
}
