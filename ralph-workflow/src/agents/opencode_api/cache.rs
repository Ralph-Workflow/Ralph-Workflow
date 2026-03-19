//! `OpenCode` API catalog caching.
//!
//! This module handles file-based caching of the `OpenCode` model catalog
//! with TTL-based expiration.
//!
//! # Dependency Injection
//!
//! The [`CacheEnvironment`] trait abstracts filesystem operations for caching,
//! enabling pure unit tests without real filesystem access. Production code
//! uses [`RealCacheEnvironment`], tests use [`MemoryCacheEnvironment`].

use crate::agents::opencode_api::fetch::fetch_api_catalog;
use crate::agents::opencode_api::types::ApiCatalog;
use crate::agents::opencode_api::DEFAULT_CACHE_TTL_SECONDS;
use crate::agents::{CacheEnvironment, RealCacheEnvironment};
use std::path::{Path, PathBuf};
use thiserror::Error;

/// Errors that can occur when loading the API catalog.
#[derive(Debug, Error)]
pub enum CacheError {
    #[error("Failed to read cache file: {0}")]
    ReadError(#[from] std::io::Error),

    #[error("Failed to parse cache JSON: {0}")]
    ParseError(#[from] serde_json::Error),

    #[error("Failed to fetch API catalog: {0}")]
    FetchError(String),

    #[error("Cache directory not found")]
    CacheDirNotFound,
}

/// Get the cache file path using a custom environment.
fn cache_file_path_with_env(env: &dyn CacheEnvironment) -> Result<PathBuf, CacheError> {
    let cache_dir = env.cache_dir().ok_or(CacheError::CacheDirNotFound)?;

    env.create_dir_all(&cache_dir)?;

    Ok(cache_dir.join("opencode-api-cache.json"))
}

/// Load the API catalog from cache or fetch if expired.
///
/// This function:
/// 1. Checks if a cached catalog exists
/// 2. If cached and not expired, returns the cached version
/// 3. If expired or missing, fetches a fresh catalog from the API
/// 4. Saves the fetched catalog to disk for future use
///
/// Gracefully degrades on network errors: if fetching fails but a stale
/// cache exists (< 7 days old), it will be used with a warning.
///
/// # Returns
///
/// Returns the catalog along with any warnings encountered during loading.
/// Warnings should be emitted by the caller at the I/O boundary.
pub fn load_api_catalog() -> Result<(ApiCatalog, Vec<CacheWarning>), CacheError> {
    load_api_catalog_with_ttl(DEFAULT_CACHE_TTL_SECONDS)
}

/// Load the API catalog with a custom TTL.
///
/// This is the boundary entry point that accepts TTL as a parameter
/// (obtained from environment at the call site).
///
/// # Returns
///
/// Returns the catalog along with any warnings encountered during loading.
pub fn load_api_catalog_with_ttl(
    ttl_seconds: u64,
) -> Result<(ApiCatalog, Vec<CacheWarning>), CacheError> {
    load_api_catalog_with_env(&RealCacheEnvironment, ttl_seconds)
}

/// Load the API catalog using a custom environment.
fn load_api_catalog_with_env(
    env: &dyn CacheEnvironment,
    ttl_seconds: u64,
) -> Result<(ApiCatalog, Vec<CacheWarning>), CacheError> {
    let cache_path = cache_file_path_with_env(env)?;

    match load_cached_catalog_with_env(env, &cache_path, ttl_seconds) {
        Ok(result) => Ok((result.catalog, result.warnings)),
        Err(_) => {
            let (catalog, warnings) = fetch_api_catalog()?;
            Ok((catalog, warnings))
        }
    }
}

/// Warnings that can occur during catalog loading.
#[derive(Debug, Clone)]
pub enum CacheWarning {
    /// Used stale cache because fresh fetch failed.
    StaleCacheUsed { stale_days: i64, error: String },
    /// Catalog was fetched but could not be saved to cache.
    CacheSaveFailed { error: String },
}

/// Result of loading catalog with associated warnings.
#[derive(Debug, Clone)]
pub struct LoadCatalogResult {
    pub catalog: ApiCatalog,
    pub warnings: Vec<CacheWarning>,
}

/// Pure function to check if stale cache should be used and compute warning.
fn compute_stale_cache_warning(catalog: &ApiCatalog, fetch_error: String) -> Option<CacheWarning> {
    let cached_at = catalog.cached_at?;
    let now = chrono::Utc::now();
    let stale_days = (now.signed_duration_since(cached_at).num_seconds() / 86400).abs();
    (stale_days < 7).then_some(CacheWarning::StaleCacheUsed {
        stale_days,
        error: fetch_error,
    })
}

/// Load a cached catalog from disk.
fn load_cached_catalog_with_env(
    env: &dyn CacheEnvironment,
    path: &Path,
    ttl_seconds: u64,
) -> Result<LoadCatalogResult, CacheError> {
    let content = env.read_file(path)?;

    let catalog: ApiCatalog =
        serde_json::from_str::<ApiCatalog>(&content).map(|c| ApiCatalog { ttl_seconds, ..c })?;

    if catalog.is_expired() {
        match fetch_api_catalog() {
            Ok((fresh, fetch_warnings)) => {
                if let Some(warning) = fetch_warnings.into_iter().last() {
                    return Ok(LoadCatalogResult {
                        catalog: fresh,
                        warnings: vec![warning],
                    });
                }
                return Ok(LoadCatalogResult {
                    catalog: fresh,
                    warnings: vec![],
                });
            }
            Err(e) => {
                let error_str = e.to_string();
                if let Some(warning) = compute_stale_cache_warning(&catalog, error_str.clone()) {
                    return Ok(LoadCatalogResult {
                        catalog,
                        warnings: vec![warning],
                    });
                }
                return Err(CacheError::FetchError(error_str));
            }
        }
    }

    Ok(LoadCatalogResult {
        catalog,
        warnings: vec![],
    })
}

/// Save the API catalog to disk.
///
/// Note: Only serializes the providers and models data from the API.
/// The `cached_at` timestamp and `ttl_seconds` are not persisted.
pub fn save_catalog(catalog: &ApiCatalog) -> Result<(), CacheError> {
    save_catalog_with_env(catalog, &RealCacheEnvironment)
}

/// Save the API catalog using a custom environment.
fn save_catalog_with_env(
    catalog: &ApiCatalog,
    env: &dyn CacheEnvironment,
) -> Result<(), CacheError> {
    #[derive(serde::Serialize)]
    struct SerializableCatalog<'a> {
        providers: &'a std::collections::HashMap<String, crate::agents::opencode_api::Provider>,
        models: &'a std::collections::HashMap<String, Vec<crate::agents::opencode_api::Model>>,
    }

    let cache_path = cache_file_path_with_env(env)?;
    let serializable = SerializableCatalog {
        providers: &catalog.providers,
        models: &catalog.models,
    };
    let content = serde_json::to_string_pretty(&serializable)?;
    env.write_file(&cache_path, &content)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::opencode_api::types::{Model, Provider};
    use std::collections::HashMap;
    use std::io;
    use std::sync::{Arc, RwLock};

    /// In-memory implementation of [`CacheEnvironment`] for testing.
    #[derive(Debug, Clone, Default)]
    struct MemoryCacheEnvironment {
        cache_dir: Option<PathBuf>,
        files: Arc<RwLock<HashMap<PathBuf, String>>>,
        dirs: Arc<RwLock<std::collections::HashSet<PathBuf>>>,
    }

    impl MemoryCacheEnvironment {
        fn new() -> Self {
            Self::default()
        }

        #[must_use]
        fn with_cache_dir<P: Into<PathBuf>>(mut self, path: P) -> Self {
            self.cache_dir = Some(path.into());
            self
        }

        #[must_use]
        fn with_file<P: Into<PathBuf>, S: Into<String>>(self, path: P, content: S) -> Self {
            let path = path.into();
            self.files
                .write()
                .expect("RwLock poisoned")
                .insert(path, content.into());
            self
        }

        fn get_file(&self, path: &Path) -> Option<String> {
            self.files
                .read()
                .expect("RwLock poisoned")
                .get(path)
                .cloned()
        }

        fn was_written(&self, path: &Path) -> bool {
            self.files
                .read()
                .expect("RwLock poisoned")
                .contains_key(path)
        }
    }

    impl CacheEnvironment for MemoryCacheEnvironment {
        fn cache_dir(&self) -> Option<PathBuf> {
            self.cache_dir.clone()
        }

        fn read_file(&self, path: &Path) -> io::Result<String> {
            self.files
                .read()
                .expect("RwLock poisoned")
                .get(path)
                .cloned()
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::NotFound,
                        format!("File not found: {}", path.display()),
                    )
                })
        }

        fn write_file(&self, path: &Path, content: &str) -> io::Result<()> {
            self.files
                .write()
                .expect("RwLock poisoned")
                .insert(path.to_path_buf(), content.to_string());
            Ok(())
        }

        fn create_dir_all(&self, path: &Path) -> io::Result<()> {
            self.dirs
                .write()
                .expect("RwLock poisoned")
                .insert(path.to_path_buf());
            Ok(())
        }
    }

    fn create_test_catalog() -> ApiCatalog {
        let providers = HashMap::from([(
            "test".to_string(),
            Provider {
                id: "test".to_string(),
                name: "Test Provider".to_string(),
                description: "Test".to_string(),
            },
        )]);

        let models = HashMap::from([(
            "test".to_string(),
            vec![Model {
                id: "test-model".to_string(),
                name: "Test Model".to_string(),
                description: "Test".to_string(),
                context_length: None,
            }],
        )]);

        ApiCatalog {
            providers,
            models,
            cached_at: Some(chrono::Utc::now()),
            ttl_seconds: DEFAULT_CACHE_TTL_SECONDS,
        }
    }

    #[test]
    fn test_memory_environment_file_operations() {
        let env = MemoryCacheEnvironment::new().with_cache_dir("/test/cache");

        let path = Path::new("/test/file.txt");

        env.write_file(path, "test content").unwrap();

        assert_eq!(env.read_file(path).unwrap(), "test content");
        assert!(env.was_written(path));
    }

    #[test]
    fn test_memory_environment_with_prepopulated_file() {
        let env = MemoryCacheEnvironment::new()
            .with_cache_dir("/test/cache")
            .with_file("/test/existing.txt", "existing content");

        assert_eq!(
            env.read_file(Path::new("/test/existing.txt")).unwrap(),
            "existing content"
        );
    }

    #[test]
    fn test_cache_file_path_with_memory_env() {
        let env = MemoryCacheEnvironment::new().with_cache_dir("/test/cache");

        let path = cache_file_path_with_env(&env).unwrap();
        assert_eq!(path, PathBuf::from("/test/cache/opencode-api-cache.json"));
    }

    #[test]
    fn test_cache_file_path_without_cache_dir() {
        let env = MemoryCacheEnvironment::new();

        let result = cache_file_path_with_env(&env);
        assert!(matches!(result, Err(CacheError::CacheDirNotFound)));
    }

    #[test]
    fn test_save_and_load_catalog_with_memory_env() {
        let env = MemoryCacheEnvironment::new().with_cache_dir("/test/cache");

        let catalog = create_test_catalog();

        save_catalog_with_env(&catalog, &env).unwrap();

        let cache_path = Path::new("/test/cache/opencode-api-cache.json");
        assert!(env.was_written(cache_path));

        let content = env.get_file(cache_path).unwrap();
        let loaded: ApiCatalog = serde_json::from_str(&content).unwrap();

        assert_eq!(loaded.providers.len(), catalog.providers.len());
        assert!(loaded.has_provider("test"));
        assert!(loaded.has_model("test", "test-model"));
    }

    #[test]
    fn test_catalog_serialization() {
        #[derive(serde::Serialize)]
        struct SerializableCatalog<'a> {
            providers: &'a std::collections::HashMap<String, crate::agents::opencode_api::Provider>,
            models: &'a std::collections::HashMap<String, Vec<crate::agents::opencode_api::Model>>,
        }

        let catalog = create_test_catalog();

        let serializable = SerializableCatalog {
            providers: &catalog.providers,
            models: &catalog.models,
        };
        let json = serde_json::to_string(&serializable).unwrap();
        let deserialized: ApiCatalog = serde_json::from_str(&json).unwrap();

        assert_eq!(deserialized.providers.len(), catalog.providers.len());
        assert_eq!(deserialized.models.len(), catalog.models.len());
    }

    #[test]
    fn test_expired_catalog_detection() {
        let catalog = create_test_catalog();

        assert!(!catalog.is_expired());

        catalog.cached_at = Some(
            chrono::Utc::now()
                - chrono::Duration::seconds(DEFAULT_CACHE_TTL_SECONDS.cast_signed() + 1),
        );
        assert!(catalog.is_expired());
    }

    #[test]
    fn test_real_environment_returns_path() {
        let env = RealCacheEnvironment;
        let cache_dir = env.cache_dir();

        if let Some(dir) = cache_dir {
            assert!(dir.to_string_lossy().contains("ralph-workflow"));
        }
    }

    #[test]
    fn test_production_cache_file_path_returns_correct_filename() {
        let env = RealCacheEnvironment;
        let path = cache_file_path_with_env(&env).unwrap();
        assert!(
            path.ends_with("opencode-api-cache.json"),
            "cache file should end with opencode-api-cache.json"
        );
    }
}
