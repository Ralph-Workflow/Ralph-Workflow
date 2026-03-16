use std::collections::HashMap;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

use crate::config::ConfigEnvironment;

/// In-memory implementation of [`ConfigEnvironment`] for testing.
///
/// Provides complete isolation from the real environment:
/// - Injected paths instead of environment variables
/// - In-memory file storage instead of real filesystem
/// - Injectable environment variables via `with_env_var()` (default: no vars set)
///
/// # Example
///
/// ```ignore
/// use crate::config::MemoryConfigEnvironment;
///
/// let env = MemoryConfigEnvironment::new()
///     .with_unified_config_path("/test/config/ralph-workflow.toml")
///     .with_prompt_path("/test/repo/PROMPT.md")
///     .with_file("/test/repo/existing.txt", "content")
///     .with_env_var("RALPH_DEVELOPER_ITERS", "10");
///
/// // Write a file
/// env.write_file(Path::new("/test/new.txt"), "new content")?;
///
/// // Verify it was written
/// assert!(env.was_written(Path::new("/test/new.txt")));
/// assert_eq!(env.get_file(Path::new("/test/new.txt")), Some("new content".to_string()));
/// ```
#[derive(Debug, Clone, Default)]
pub struct MemoryConfigEnvironment {
    unified_config_path: Option<PathBuf>,
    prompt_path: Option<PathBuf>,
    local_config_path: Option<PathBuf>,
    worktree_root: Option<PathBuf>,
    /// In-memory file storage.
    files: Arc<RwLock<HashMap<PathBuf, String>>>,
    /// Directories that have been created.
    dirs: Arc<RwLock<std::collections::HashSet<PathBuf>>>,
    /// Injectable environment variables for testing.
    ///
    /// By default (empty map), `get_env_var()` returns `None` for all keys,
    /// providing complete isolation from the real process environment.
    env_vars: HashMap<String, String>,
}

impl MemoryConfigEnvironment {
    /// Create a new memory environment with no paths configured.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Set the unified config path.
    #[must_use]
    pub fn with_unified_config_path<P: Into<PathBuf>>(mut self, path: P) -> Self {
        self.unified_config_path = Some(path.into());
        self
    }

    /// Set the local config path.
    #[must_use]
    pub fn with_local_config_path<P: Into<PathBuf>>(mut self, path: P) -> Self {
        self.local_config_path = Some(path.into());
        self
    }

    /// Set the PROMPT.md path.
    #[must_use]
    pub fn with_prompt_path<P: Into<PathBuf>>(mut self, path: P) -> Self {
        self.prompt_path = Some(path.into());
        self
    }

    /// Pre-populate a file in memory.
    ///
    /// # Panics
    ///
    /// Panics if the `RwLock` is poisoned.
    #[must_use]
    pub fn with_file<P: Into<PathBuf>, S: Into<String>>(self, path: P, content: S) -> Self {
        let path = path.into();
        self.files.write()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment files lock")
            .insert(path, content.into());
        self
    }

    /// Set the worktree root path for testing git worktree scenarios.
    #[must_use]
    pub fn with_worktree_root<P: Into<PathBuf>>(mut self, path: P) -> Self {
        self.worktree_root = Some(path.into());
        self
    }

    /// Inject an environment variable for testing.
    ///
    /// Provides per-test env isolation without mutating the real process environment.
    /// Use this instead of `std::env::set_var` to avoid `#[serial]` requirements.
    ///
    /// # Example
    ///
    /// ```ignore
    /// let env = MemoryConfigEnvironment::new()
    ///     .with_env_var("RALPH_DEVELOPER_ITERS", "42")
    ///     .with_env_var("RALPH_ISOLATION_MODE", "false");
    /// ```
    #[must_use]
    pub fn with_env_var(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env_vars.insert(key.into(), value.into());
        self
    }

    /// Get the contents of a file (for test assertions).
    ///
    /// # Panics
    ///
    /// Panics if the `RwLock` is poisoned.
    #[must_use]
    pub fn get_file(&self, path: &Path) -> Option<String> {
        self.files.read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment files lock")
            .get(path).cloned()
    }

    /// Check if a file was written (for test assertions).
    ///
    /// # Panics
    ///
    /// Panics if the `RwLock` is poisoned.
    #[must_use]
    pub fn was_written(&self, path: &Path) -> bool {
        self.files.read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment files lock")
            .contains_key(path)
    }
}

impl ConfigEnvironment for MemoryConfigEnvironment {
    fn unified_config_path(&self) -> Option<PathBuf> {
        self.unified_config_path.clone()
    }

    /// Returns an injected env var from the in-memory map.
    ///
    /// Returns `None` for any key not explicitly set via `with_env_var()`,
    /// providing complete isolation from the real process environment.
    fn get_env_var(&self, key: &str) -> Option<String> {
        self.env_vars.get(key).cloned()
    }

    fn local_config_path(&self) -> Option<PathBuf> {
        // If explicit local_config_path was set, use it (for legacy tests)
        if let Some(ref path) = self.local_config_path {
            return Some(path.clone());
        }

        // Otherwise, use worktree root if available
        self.worktree_root()
            .map(|root| root.join(".agent/ralph-workflow.toml"))
            .or_else(|| Some(PathBuf::from(".agent/ralph-workflow.toml")))
    }

    fn prompt_path(&self) -> PathBuf {
        self.prompt_path
            .clone()
            .unwrap_or_else(|| PathBuf::from("PROMPT.md"))
    }

    fn file_exists(&self, path: &Path) -> bool {
        self.files.read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment files lock")
            .contains_key(path)
    }

    fn read_file(&self, path: &Path) -> io::Result<String> {
        self.files
            .read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment files lock")
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
        // Simulate creating parent directories
        if let Some(parent) = path.parent() {
            self.dirs.write()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment dirs lock")
                .insert(parent.to_path_buf());
        }
        self.files
            .write()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment files lock")
            .insert(path.to_path_buf(), content.to_string());
        Ok(())
    }

    fn create_dir_all(&self, path: &Path) -> io::Result<()> {
        self.dirs.write()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment dirs lock")
            .insert(path.to_path_buf());
        Ok(())
    }

    fn worktree_root(&self) -> Option<PathBuf> {
        self.worktree_root.clone()
    }
}
