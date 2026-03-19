use std::io;
use std::path::{Path, PathBuf};

pub trait CacheEnvironment: Send + Sync {
    fn cache_dir(&self) -> Option<PathBuf>;

    fn read_file(&self, path: &Path) -> io::Result<String>;

    fn write_file(&self, path: &Path, content: &str) -> io::Result<()>;

    fn create_dir_all(&self, path: &Path) -> io::Result<()>;
}
