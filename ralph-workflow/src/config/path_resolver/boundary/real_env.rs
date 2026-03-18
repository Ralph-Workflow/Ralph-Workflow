use std::io;
use std::path::{Path, PathBuf};

use crate::config::ConfigEnvironment;

/// Production implementation of [`ConfigEnvironment`].
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
        // Try worktree root first, fall back to default behavior
        self.worktree_root()
            .map(|root| root.join(".agent/ralph-workflow.toml"))
            .or_else(|| Some(PathBuf::from(".agent/ralph-workflow.toml")))
    }
}
