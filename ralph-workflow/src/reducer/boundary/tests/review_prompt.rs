use crate::workspace::{MemoryWorkspace, Workspace};
use std::io;

mod materialize_inputs;
mod prepare_fix_prompt;
mod prepare_review_prompt;
mod xsd_retry;

#[derive(Debug, Clone)]
struct AtomicWriteEnforcingWorkspace {
    inner: MemoryWorkspace,
    forbidden_write_path: std::path::PathBuf,
}

impl AtomicWriteEnforcingWorkspace {
    fn new(inner: MemoryWorkspace, forbidden_write_path: std::path::PathBuf) -> Self {
        Self {
            inner,
            forbidden_write_path,
        }
    }
}

impl Workspace for AtomicWriteEnforcingWorkspace {
    fn root(&self) -> &std::path::Path {
        self.inner.root()
    }

    fn read(&self, relative: &std::path::Path) -> io::Result<String> {
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &std::path::Path) -> io::Result<Vec<u8>> {
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &std::path::Path, content: &str) -> io::Result<()> {
        if relative == self.forbidden_write_path.as_path() {
            return Err(io::Error::other(format!(
                "non-atomic write forbidden for {}",
                self.forbidden_write_path.display()
            )));
        }
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &std::path::Path, content: &[u8]) -> io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &std::path::Path, content: &[u8]) -> io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &std::path::Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &std::path::Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &std::path::Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn remove(&self, relative: &std::path::Path) -> io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &std::path::Path) -> io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &std::path::Path) -> io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &std::path::Path) -> io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &std::path::Path) -> io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &std::path::Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &std::path::Path, to: &std::path::Path) -> io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &std::path::Path, content: &str) -> io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &std::path::Path) -> io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &std::path::Path) -> io::Result<()> {
        self.inner.set_writable(relative)
    }
}
