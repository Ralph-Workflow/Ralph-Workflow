//! Configuration environment abstraction.
//!
//! This module provides the [`ConfigEnvironment`] trait that abstracts all
//! external side effects needed for configuration operations:
//! - Environment variable access (for path resolution)
//! - Filesystem operations (for reading/writing config files)
//!
//! # Design Philosophy
//!
//! Configuration types like `UnifiedConfig` should be pure data structures.
//! All side effects (env vars, file I/O) are injected through this trait,
//! making the code testable without mocking globals.
//!
//! # Dependency Injection
//!
//! Production code uses [`RealConfigEnvironment`] which reads from actual
//! environment variables and performs real filesystem operations. Tests use
//! [`MemoryConfigEnvironment`] with in-memory storage for both.
//!
//! # Example
//!
//! ```ignore
//! use crate::config::{ConfigEnvironment, RealConfigEnvironment, MemoryConfigEnvironment};
//!
//! // Production: uses real env vars and filesystem
//! let env = RealConfigEnvironment;
//! let config_path = env.unified_config_path();
//!
//! // Testing: uses in-memory storage
//! let env = MemoryConfigEnvironment::new()
//!     .with_unified_config_path("/test/config/ralph-workflow.toml")
//!     .with_prompt_path("/test/repo/PROMPT.md")
//!     .with_file("/test/repo/PROMPT.md", "# Goal\nTest");
//! ```

use std::io;
use std::path::{Path, PathBuf};

mod boundary;

pub use boundary::memory_env::MemoryConfigEnvironment;

/// Trait for configuration environment access.
///
/// This trait abstracts all external side effects needed for configuration:
/// - Path resolution (which may depend on environment variables)
/// - File existence checks
/// - File reading and writing
/// - Directory creation
///
/// By injecting this trait, configuration code becomes pure and testable.
pub trait ConfigEnvironment: Send + Sync {
    /// Get the path to the unified config file.
    ///
    /// In production, this returns `~/.config/ralph-workflow.toml` or
    /// `$XDG_CONFIG_HOME/ralph-workflow.toml` if the env var is set.
    ///
    /// Returns `None` if the path cannot be determined (e.g., no home directory).
    fn unified_config_path(&self) -> Option<PathBuf>;

    /// Get the path to the local config file.
    ///
    /// In production, this returns `.agent/ralph-workflow.toml` relative to CWD.
    /// Tests may override this to use a different path.
    ///
    /// Returns `None` if local config is not supported or path cannot be determined.
    fn local_config_path(&self) -> Option<PathBuf> {
        Some(PathBuf::from(".agent/ralph-workflow.toml"))
    }

    /// Get the path to the PROMPT.md file.
    ///
    /// In production, this returns `./PROMPT.md` (relative to current directory).
    /// Tests may override this to use a different path.
    fn prompt_path(&self) -> PathBuf {
        PathBuf::from("PROMPT.md")
    }

    /// Check if a file exists at the given path.
    fn file_exists(&self, path: &Path) -> bool;

    /// Read the contents of a file.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn read_file(&self, path: &Path) -> io::Result<String>;

    /// Write content to a file, creating parent directories if needed.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn write_file(&self, path: &Path, content: &str) -> io::Result<()>;

    /// Create directories recursively.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn create_dir_all(&self, path: &Path) -> io::Result<()>;

    /// Get the canonical root of the git repository, even from a worktree.
    ///
    /// When running inside a git worktree, this returns the **main repository root**
    /// (not the ephemeral worktree working directory). This ensures local config
    /// paths remain valid after worktree deletion.
    ///
    /// Returns `None` if not in a git repository or in a bare repository.
    fn worktree_root(&self) -> Option<PathBuf> {
        None // Default implementation for backwards compatibility
    }

    /// Get a single environment variable by name.
    ///
    /// In production (`RealConfigEnvironment`), reads from the real process environment.
    /// In tests (`MemoryConfigEnvironment`), returns `None` by default (no env vars set),
    /// providing complete isolation from the real process environment.
    ///
    /// Used to thread env-var access through config loading so production code is
    /// fully testable without `#[serial]` or global env mutation.
    fn get_env_var(&self, _key: &str) -> Option<String> {
        None
    }
}

/// Production implementation of [`ConfigEnvironment`].
///
/// Uses real environment variables and filesystem operations:
/// - Reads `XDG_CONFIG_HOME` for config path resolution
/// - Uses `std::fs` for all file operations
#[derive(Debug, Default, Clone, Copy)]
pub struct RealConfigEnvironment;

impl ConfigEnvironment for RealConfigEnvironment {
    fn unified_config_path(&self) -> Option<PathBuf> {
        super::unified::unified_config_path()
    }

    fn get_env_var(&self, key: &str) -> Option<String> {
        std::env::var(key).ok()
    }

    fn file_exists(&self, path: &Path) -> bool {
        path.exists()
    }

    fn read_file(&self, path: &Path) -> io::Result<String> {
        std::fs::read_to_string(path)
    }

    fn write_file(&self, path: &Path, content: &str) -> io::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(path, content)
    }

    fn create_dir_all(&self, path: &Path) -> io::Result<()> {
        std::fs::create_dir_all(path)
    }

    fn worktree_root(&self) -> Option<PathBuf> {
        let repo = git2::Repository::discover(".").ok()?;
        let gitdir = repo.path();

        // Detect worktrees: in a git worktree, repo.path() returns something like
        // <main-repo>/.git/worktrees/<worktree-name>/
        // We detect this by checking if the gitdir is inside a "worktrees" subdirectory
        // of the main repo's .git directory.
        if let Some(parent) = gitdir.parent() {
            if parent.file_name().and_then(|n| n.to_str()) == Some("worktrees") {
                // parent is <main-repo>/.git/worktrees
                // parent.parent() is <main-repo>/.git
                // parent.parent().parent() is <main-repo> (the canonical root)
                return parent.parent().and_then(|p| p.parent()).map(PathBuf::from);
            }
        }

        repo.workdir().map(PathBuf::from)
    }

    fn local_config_path(&self) -> Option<PathBuf> {
        // Try worktree root first, fall back to default behavior
        self.worktree_root()
            .map(|root| root.join(".agent/ralph-workflow.toml"))
            .or_else(|| Some(PathBuf::from(".agent/ralph-workflow.toml")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_real_environment_returns_path() {
        let env = RealConfigEnvironment;
        // Should return Some path (unless running in weird environment without home dir)
        let path = env.unified_config_path();
        if let Some(p) = path {
            assert!(p.to_string_lossy().contains("ralph-workflow.toml"));
        }
    }

    #[test]
    fn test_memory_environment_with_custom_paths() {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/custom/config.toml")
            .with_prompt_path("/custom/PROMPT.md");

        assert_eq!(
            env.unified_config_path(),
            Some(PathBuf::from("/custom/config.toml"))
        );
        assert_eq!(env.prompt_path(), PathBuf::from("/custom/PROMPT.md"));
    }

    #[test]
    fn test_memory_environment_default_prompt_path() {
        let env = MemoryConfigEnvironment::new();
        assert_eq!(env.prompt_path(), PathBuf::from("PROMPT.md"));
    }

    #[test]
    fn test_memory_environment_no_unified_config() {
        let env = MemoryConfigEnvironment::new();
        assert_eq!(env.unified_config_path(), None);
    }

    #[test]
    fn test_memory_environment_file_operations() {
        let env = MemoryConfigEnvironment::new();
        let path = Path::new("/test/file.txt");

        // File doesn't exist initially
        assert!(!env.file_exists(path));

        // Write file
        env.write_file(path, "test content").unwrap();

        // File now exists
        assert!(env.file_exists(path));
        assert_eq!(env.read_file(path).unwrap(), "test content");
        assert!(env.was_written(path));
    }

    #[test]
    fn test_memory_environment_with_prepopulated_file() {
        let env =
            MemoryConfigEnvironment::new().with_file("/test/existing.txt", "existing content");

        assert!(env.file_exists(Path::new("/test/existing.txt")));
        assert_eq!(
            env.read_file(Path::new("/test/existing.txt")).unwrap(),
            "existing content"
        );
    }

    #[test]
    fn test_memory_environment_read_nonexistent_file() {
        let env = MemoryConfigEnvironment::new();
        let result = env.read_file(Path::new("/nonexistent"));
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::NotFound);
    }

    #[test]
    fn test_memory_environment_with_worktree_root() {
        let env = MemoryConfigEnvironment::new().with_worktree_root("/test/worktree");

        assert_eq!(env.worktree_root(), Some(PathBuf::from("/test/worktree")));
        assert_eq!(
            env.local_config_path(),
            Some(PathBuf::from("/test/worktree/.agent/ralph-workflow.toml"))
        );
    }

    #[test]
    fn test_memory_environment_without_worktree_root() {
        let env = MemoryConfigEnvironment::new();

        assert_eq!(env.worktree_root(), None);
        assert_eq!(
            env.local_config_path(),
            Some(PathBuf::from(".agent/ralph-workflow.toml"))
        );
    }

    #[test]
    fn test_memory_environment_explicit_local_path_overrides_worktree() {
        let env = MemoryConfigEnvironment::new()
            .with_worktree_root("/test/worktree")
            .with_local_config_path("/custom/path/config.toml");

        // Explicit local_config_path should take precedence
        assert_eq!(
            env.local_config_path(),
            Some(PathBuf::from("/custom/path/config.toml"))
        );
    }

    #[test]
    fn test_canonical_repo_root_used_for_local_config_path() {
        // Simulate a worktree scenario where the canonical repo root
        // differs from the worktree working directory. The worktree_root
        // should point to the canonical repo root (not the ephemeral
        // worktree path) so local config persists after worktree deletion.
        let canonical_root = "/home/user/my-repo";
        let env = MemoryConfigEnvironment::new().with_worktree_root(canonical_root);

        assert_eq!(env.worktree_root(), Some(PathBuf::from(canonical_root)));
        assert_eq!(
            env.local_config_path(),
            Some(PathBuf::from(
                "/home/user/my-repo/.agent/ralph-workflow.toml"
            ))
        );
    }

    #[test]
    fn test_worktree_root_and_local_config_path_consistency() {
        // Both --init-local-config and runtime loading must resolve
        // the same canonical path. Verify that local_config_path()
        // is always derived from worktree_root() when set.
        let env = MemoryConfigEnvironment::new().with_worktree_root("/repos/main-repo");

        let root = env.worktree_root().unwrap();
        let local_path = env.local_config_path().unwrap();

        assert_eq!(local_path, root.join(".agent/ralph-workflow.toml"));
    }
}
