// agents/cache_environment/io.rs — boundary module for cache environment operations.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

use std::path::{Path, PathBuf};

pub trait CacheEnvironment: Send + Sync {
    fn cache_dir(&self) -> Option<PathBuf>;

    fn read_file(&self, path: &Path) -> Result<String, std::io::Error>;

    fn write_file(&self, path: &Path, content: &str) -> Result<(), std::io::Error>;

    fn create_dir_all(&self, path: &Path) -> Result<(), std::io::Error>;
}

#[derive(Debug, Default, Clone, Copy)]
pub struct RealCacheEnvironment;

impl CacheEnvironment for RealCacheEnvironment {
    fn cache_dir(&self) -> Option<PathBuf> {
        dirs::cache_dir().map(|d| d.join("ralph-workflow"))
    }

    fn read_file(&self, path: &Path) -> Result<String, std::io::Error> {
        std::fs::read_to_string(path)
    }

    fn write_file(&self, path: &Path, content: &str) -> Result<(), std::io::Error> {
        std::fs::write(path, content)
    }

    fn create_dir_all(&self, path: &Path) -> Result<(), std::io::Error> {
        std::fs::create_dir_all(path)
    }
}
