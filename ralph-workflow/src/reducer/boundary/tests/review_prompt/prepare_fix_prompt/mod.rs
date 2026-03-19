//! Review fix prompt preparation tests.
//!
//! Tests the handler that prepares prompts for the fix phase of review,
//! including retry handling, error recovery, and context building.
//!
//! ## Test Organization
//!
//! - `retry_handling`: Same-agent retry prompt reuse and retry note management
//! - `error_handling`: Workspace I/O error masking and non-fatal write failures
//! - `context_building`: Placeholder handling and missing file sentinel embedding

mod context_building;
mod error_handling;
mod retry_handling;
use crate::workspace::MemoryWorkspace;
use crate::workspace::Workspace;
use std::io;
use std::path::{Path, PathBuf};

/// Test helper: Workspace that fails reads for a specific path with a configurable error kind.
///
/// Used to verify that non-NotFound errors are properly surfaced rather than masked.
#[derive(Debug, Clone)]
pub(super) struct ReadFailingWorkspace {
    inner: MemoryWorkspace,
    forbidden_read_path: PathBuf,
    kind: io::ErrorKind,
}

impl ReadFailingWorkspace {
    pub(super) fn new(
        inner: MemoryWorkspace,
        forbidden_read_path: PathBuf,
        kind: io::ErrorKind,
    ) -> Self {
        Self {
            inner,
            forbidden_read_path,
            kind,
        }
    }
}

impl Workspace for ReadFailingWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
        if relative == self.forbidden_read_path.as_path() {
            return Err(io::Error::new(
                self.kind,
                format!("read forbidden for {}", self.forbidden_read_path.display()),
            ));
        }
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
        if relative == self.forbidden_read_path.as_path() {
            return Err(io::Error::new(
                self.kind,
                format!("read forbidden for {}", self.forbidden_read_path.display()),
            ));
        }
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn remove(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        self.inner.rename(from, to)
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_writable(relative)
    }
}
