use std::io;
use std::path::{Path, PathBuf};

pub trait CacheEnvironment: Send + Sync {
    fn cache_dir(&self) -> Option<PathBuf>;

    fn read_file(&self, path: &Path) -> io::Result<String>;

    fn write_file(&self, path: &Path, content: &str) -> io::Result<()>;

    fn create_dir_all(&self, path: &Path) -> io::Result<()>;
}

#[derive(Debug, Default, Clone, Copy)]
pub struct RealCacheEnvironment;

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
