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
use crate::agents::opencode_api::{CACHE_TTL_ENV_VAR, DEFAULT_CACHE_TTL_SECONDS};
use std::io;
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

/// Trait for cache environment access.
///
/// This trait abstracts filesystem operations needed for caching:
/// - Cache directory resolution
/// - File reading and writing
/// - Directory creation
///
/// By injecting this trait, cache code becomes pure and testable.
trait CacheEnvironment: Send + Sync {
    /// Get the cache directory for ralph-workflow.
    ///
    /// In production, returns `~/.cache/ralph-workflow` or equivalent.
    /// Returns `None` if the cache directory cannot be determined.
    fn cache_dir(&self) -> Option<PathBuf>;

    /// Read the contents of a file.
    fn read_file(&self, path: &Path) -> io::Result<String>;

    /// Write content to a file.
    fn write_file(&self, path: &Path, content: &str) -> io::Result<()>;

    /// Create directories recursively.
    fn create_dir_all(&self, path: &Path) -> io::Result<()>;
}

/// Production implementation of [`CacheEnvironment`].
///
/// Uses the `dirs` crate for cache directory resolution and `std::fs` for
/// all file operations.
#[derive(Debug, Default, Clone, Copy)]
struct RealCacheEnvironment;

impl CacheEnvironment for RealCacheEnvironment {
    fn cache_dir(&self) -> Option<PathBuf> {
        dirs::cache_dir().map(|d| d.join("ralph-workflow"))
    }

    fn read_file(&self, path: &Path) -> io::Result<String> {
        std::fs::read_to_string(path)
    }

    fn write_file(&self, path: &Path, content: &str) -> io::Result<()> {
        std::fs::write(path, content)
    }

    fn create_dir_all(&self, path: &Path) -> io::Result<()> {
        std::fs::create_dir_all(path)
    }
}

/// Get the cache file path using a custom environment.
fn cache_file_path_with_env(env: &dyn CacheEnvironment) -> Result<PathBuf, CacheError> {
    let cache_dir = env.cache_dir().ok_or(CacheError::CacheDirNotFound)?;

    // Ensure cache directory exists
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
/// # Errors
///
/// Returns error if the operation fails.
pub fn load_api_catalog() -> Result<ApiCatalog, CacheError> {
    load_api_catalog_with_env(&RealCacheEnvironment)
}

/// Load the API catalog using a custom environment.
fn load_api_catalog_with_env(env: &dyn CacheEnvironment) -> Result<ApiCatalog, CacheError> {
    let ttl_seconds = std::env::var(CACHE_TTL_ENV_VAR)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_CACHE_TTL_SECONDS);

    let cache_path = cache_file_path_with_env(env)?;

    // Try to load from cache - pure function returns warnings as data
    match load_cached_catalog_with_env(env, &cache_path, ttl_seconds) {
        Ok(result) => {
            // Boundary: emit warnings as side effect
            // Note: This function does I/O (console output) so it's appropriate for boundary code
            emit_cache_warnings(&result.warnings);
            Ok(result.catalog)
        }
        Err(_) => {
            // Cache miss or expired, fetch from API
            fetch_api_catalog()
        }
    }
}

/// Emit cache warnings to stderr.
/// This is a boundary function that handles I/O (console output).
fn emit_cache_warnings(warnings: &[CacheWarning]) {
    use std::io::Write;
    let mut stderr = std::io::stderr();
    warnings.iter().for_each(|warning| {
        match warning {
            CacheWarning::StaleCacheUsed { stale_days, error } => {
                let _ = writeln!(
                    stderr,
                    "Warning: Failed to fetch fresh OpenCode API catalog ({error}), using stale cache from {stale_days} days ago"
                );
            }
        }
    });
}

/// Warnings that can occur during catalog loading.
#[derive(Debug, Clone)]
pub enum CacheWarning {
    /// Used stale cache because fresh fetch failed.
    StaleCacheUsed { stale_days: i64, error: String },
}

/// Result of loading catalog with associated warnings.
#[derive(Debug, Clone)]
pub struct LoadCatalogResult {
    /// The loaded catalog.
    pub catalog: ApiCatalog,
    /// Warnings that occurred during loading.
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
///
/// Returns an error if the cache file doesn't exist, is invalid, or is expired.
fn load_cached_catalog_with_env(
    env: &dyn CacheEnvironment,
    path: &Path,
    ttl_seconds: u64,
) -> Result<LoadCatalogResult, CacheError> {
    let content = env.read_file(path)?;

    let mut catalog: ApiCatalog = serde_json::from_str(&content)?;

    // Set the TTL for expiration checking
    catalog.ttl_seconds = ttl_seconds;

    // Check if expired
    if catalog.is_expired() {
        // Try to fetch fresh catalog, but use stale cache if fetch fails
        match fetch_api_catalog() {
            Ok(fresh) => {
                return Ok(LoadCatalogResult {
                    catalog: fresh,
                    warnings: vec![],
                })
            }
            Err(e) => {
                let error_str = e.to_string();
                // Use stale cache if it's less than 7 days old
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
    use std::sync::{Arc, RwLock};

    /// In-memory implementation of [`CacheEnvironment`] for testing.
    ///
    /// Provides complete isolation from the real filesystem:
    /// - Configurable cache directory path
    /// - In-memory file storage
    #[derive(Debug, Clone, Default)]
    struct MemoryCacheEnvironment {
        cache_dir: Option<PathBuf>,
        /// In-memory file storage.
        files: Arc<RwLock<HashMap<PathBuf, String>>>,
        /// Directories that have been created.
        dirs: Arc<RwLock<std::collections::HashSet<PathBuf>>>,
    }

    impl MemoryCacheEnvironment {
        /// Create a new memory environment with no paths configured.
        fn new() -> Self {
            Self::default()
        }

        /// Set the cache directory path.
        #[must_use]
        fn with_cache_dir<P: Into<PathBuf>>(mut self, path: P) -> Self {
            self.cache_dir = Some(path.into());
            self
        }

        /// Pre-populate a file in memory.
        #[must_use]
        fn with_file<P: Into<PathBuf>, S: Into<String>>(self, path: P, content: S) -> Self {
            let path = path.into();
            self.files.write()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryCacheEnvironment files lock")
                .insert(path, content.into());
            self
        }

        /// Get the contents of a file (for test assertions).
        fn get_file(&self, path: &Path) -> Option<String> {
            self.files.read()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryCacheEnvironment files lock")
                .get(path).cloned()
        }

        /// Check if a file was written (for test assertions).
        fn was_written(&self, path: &Path) -> bool {
            self.files.read()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryCacheEnvironment files lock")
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
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryCacheEnvironment files lock")
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
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryCacheEnvironment files lock")
                .insert(path.to_path_buf(), content.to_string());
            Ok(())
        }

        fn create_dir_all(&self, path: &Path) -> io::Result<()> {
            self.dirs.write()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryCacheEnvironment dirs lock")
                .insert(path.to_path_buf());
            Ok(())
        }
    }

    fn create_test_catalog() -> ApiCatalog {
        let mut providers = HashMap::new();
        providers.insert(
            "test".to_string(),
            Provider {
                id: "test".to_string(),
                name: "Test Provider".to_string(),
                description: "Test".to_string(),
            },
        );

        let mut models = HashMap::new();
        models.insert(
            "test".to_string(),
            vec![Model {
                id: "test-model".to_string(),
                name: "Test Model".to_string(),
                description: "Test".to_string(),
                context_length: None,
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
    fn test_memory_environment_file_operations() {
        // Test that MemoryCacheEnvironment correctly implements file operations
        let env = MemoryCacheEnvironment::new().with_cache_dir("/test/cache");

        let path = Path::new("/test/file.txt");

        // Write file
        env.write_file(path, "test content").unwrap();

        // File can be read
        assert_eq!(env.read_file(path).unwrap(), "test content");
        assert!(env.was_written(path));
    }

    #[test]
    fn test_memory_environment_with_prepopulated_file() {
        // Test that files can be prepopulated for testing
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
        // Test that cache_file_path_with_env returns correct path
        let env = MemoryCacheEnvironment::new().with_cache_dir("/test/cache");

        let path = cache_file_path_with_env(&env).unwrap();
        assert_eq!(path, PathBuf::from("/test/cache/opencode-api-cache.json"));
    }

    #[test]
    fn test_cache_file_path_without_cache_dir() {
        // Test that cache_file_path_with_env returns error without cache dir
        let env = MemoryCacheEnvironment::new(); // No cache dir set

        let result = cache_file_path_with_env(&env);
        assert!(matches!(result, Err(CacheError::CacheDirNotFound)));
    }

    #[test]
    fn test_save_and_load_catalog_with_memory_env() {
        // Test save and load using MemoryCacheEnvironment
        let env = MemoryCacheEnvironment::new().with_cache_dir("/test/cache");

        let catalog = create_test_catalog();

        // Save catalog
        save_catalog_with_env(&catalog, &env).unwrap();

        // Verify file was written
        let cache_path = Path::new("/test/cache/opencode-api-cache.json");
        assert!(env.was_written(cache_path));

        // Verify content is valid JSON that can be parsed
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

        // Test that catalog serialization produces valid JSON
        let catalog = create_test_catalog();

        // Serialize using the same method as save_catalog
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
        // Test that expiration detection works correctly
        let mut catalog = create_test_catalog();

        // Fresh catalog should not be expired
        assert!(!catalog.is_expired());

        // Old catalog should be expired
        catalog.cached_at = Some(
            chrono::Utc::now()
                - chrono::Duration::seconds(DEFAULT_CACHE_TTL_SECONDS.cast_signed() + 1),
        );
        assert!(catalog.is_expired());
    }

    #[test]
    fn test_real_environment_returns_path() {
        // Test that RealCacheEnvironment returns a valid path
        let env = RealCacheEnvironment;
        let cache_dir = env.cache_dir();

        // Should return Some path (unless running in weird environment)
        if let Some(dir) = cache_dir {
            assert!(dir.to_string_lossy().contains("ralph-workflow"));
        }
    }

    #[test]
    fn test_production_cache_file_path_returns_correct_filename() {
        // Test that the production cache_file_path returns a path ending in the expected filename
        let env = RealCacheEnvironment;
        let path = cache_file_path_with_env(&env).unwrap();
        assert!(
            path.ends_with("opencode-api-cache.json"),
            "cache file should end with opencode-api-cache.json"
        );
    }
}
