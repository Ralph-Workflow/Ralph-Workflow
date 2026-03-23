//! In-memory implementation of [`ConfigEnvironment`] for testing.
//!
//! This is a **boundary module** because it uses interior mutability (RwLock)
//! to simulate file system operations for test isolation.
//!
//! Provides complete isolation from the real environment:
//! - Injected paths instead of environment variables
//! - In-memory file storage instead of real filesystem
//! - Injectable environment variables via `with_env_var()` (default: no vars set)

use std::collections::HashMap;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

use crate::config::ConfigEnvironment;

/// In-memory implementation of [`ConfigEnvironment`] for testing.
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
    files: Arc<RwLock<HashMap<PathBuf, String>>>,
    dirs: Arc<RwLock<std::collections::HashSet<PathBuf>>>,
    env_vars: HashMap<String, String>,
}

impl MemoryConfigEnvironment {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn with_unified_config_path<P: Into<PathBuf>>(mut self, path: P) -> Self {
        self.unified_config_path = Some(path.into());
        self
    }

    #[must_use]
    pub fn with_local_config_path<P: Into<PathBuf>>(mut self, path: P) -> Self {
        self.local_config_path = Some(path.into());
        self
    }

    #[must_use]
    pub fn with_prompt_path<P: Into<PathBuf>>(mut self, path: P) -> Self {
        self.prompt_path = Some(path.into());
        self
    }

    #[must_use]
    pub fn with_file<P: Into<PathBuf>, S: Into<String>>(self, path: P, content: S) -> Self {
        let path = path.into();
        self.files.write()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment files lock")
            .insert(path, content.into());
        self
    }

    #[must_use]
    pub fn with_worktree_root<P: Into<PathBuf>>(mut self, path: P) -> Self {
        self.worktree_root = Some(path.into());
        self
    }

    #[must_use]
    pub fn with_env_var(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env_vars.insert(key.into(), value.into());
        self
    }

    #[must_use]
    pub fn get_file(&self, path: &Path) -> Option<String> {
        self.files.read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryConfigEnvironment files lock")
            .get(path).cloned()
    }

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

    fn get_env_var(&self, key: &str) -> Option<String> {
        self.env_vars.get(key).cloned()
    }

    fn local_config_path(&self) -> Option<PathBuf> {
        if let Some(ref path) = self.local_config_path {
            return Some(path.clone());
        }

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

///
/// Uses real environment variables and filesystem operations:
/// - Reads `XDG_CONFIG_HOME` for config path resolution
/// - Uses `std::fs` for all file operations
#[derive(Debug, Default, Clone, Copy)]
pub struct RealConfigEnvironment;

fn compute_canonical_repo_root(gitdir: &Path) -> Option<PathBuf> {
    let parent = gitdir.parent()?;
    if parent.file_name().and_then(|n| n.to_str()) == Some("worktrees") {
        parent.parent().and_then(|p| p.parent()).map(PathBuf::from)
    } else {
        None
    }
}

impl ConfigEnvironment for RealConfigEnvironment {
    fn unified_config_path(&self) -> Option<PathBuf> {
        crate::config::unified::unified_config_path()
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

        compute_canonical_repo_root(gitdir).or_else(|| repo.workdir().map(PathBuf::from))
    }

    fn local_config_path(&self) -> Option<PathBuf> {
        self.worktree_root()
            .map(|root| root.join(".agent/ralph-workflow.toml"))
            .or_else(|| Some(PathBuf::from(".agent/ralph-workflow.toml")))
    }
}
