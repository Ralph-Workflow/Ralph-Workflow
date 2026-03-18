//! `OpenCode` API catalog module.
//!
//! This module handles fetching, caching, and querying the `OpenCode` model catalog
//! from <https://models.dev/api.json>. The catalog contains available providers and models
//! that `OpenCode` supports, enabling dynamic agent configuration.
//!
//! # Module Structure
//!
//! - `types` - API catalog data structures
//! - `cache` - File-based caching with TTL
//! - `fetch` - HTTP fetching logic
//!
//! # Dependency Injection
//!
//! The [`CatalogLoader`] trait enables dependency injection for testing.
//! Production code uses [`RealCatalogLoader`] which fetches from the network,
//! while tests can provide mock implementations.

mod cache;
mod fetch;
mod types;

pub use cache::{load_api_catalog, CacheError, CacheWarning};
pub use types::{ApiCatalog, Model, Provider};

/// `OpenCode` API endpoint for model catalog.
pub const API_URL: &str = "https://models.dev/api.json";

/// Default cache TTL in seconds (24 hours).
pub const DEFAULT_CACHE_TTL_SECONDS: u64 = 24 * 60 * 60;

/// Environment variable for customizing cache TTL.
pub const CACHE_TTL_ENV_VAR: &str = "RALPH_OPENCODE_CACHE_TTL_SECONDS";

/// Trait for loading the `OpenCode` API catalog.
///
/// This trait enables dependency injection for catalog loading, allowing
/// tests to provide mock implementations that don't make network calls.
pub trait CatalogLoader: Send + Sync {
    /// Load the API catalog.
    ///
    /// Returns the catalog or an error if loading fails.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn load(&self) -> Result<ApiCatalog, CacheError>;
}

/// Production implementation of [`CatalogLoader`] that fetches from the network.
///
/// This loader uses the standard caching mechanism:
/// 1. Check for a valid cached catalog
/// 2. If cache is missing or expired, fetch from the API
/// 3. Cache the fetched result for future use
#[derive(Debug, Default, Clone)]
pub struct RealCatalogLoader;

impl CatalogLoader for RealCatalogLoader {
    fn load(&self) -> Result<ApiCatalog, CacheError> {
        let (catalog, warnings) = load_api_catalog()?;
        warnings.into_iter().for_each(|warning| {
            match warning {
                CacheWarning::StaleCacheUsed { stale_days, error } => {
                    eprintln!("Warning: Failed to fetch fresh OpenCode API catalog ({error}), using stale cache from {stale_days} days ago");
                }
                CacheWarning::CacheSaveFailed { error } => {
                    eprintln!("Warning: Failed to cache OpenCode API catalog: {error}");
                }
            }
        });
        Ok(catalog)
    }
}
