//! Tests for `invoke_prompt` handler functionality
//!
//! This module contains tests for agent prompt invocation, covering:
//! - Error handling when prompts are missing or unreadable
//! - Prompt selection logic (retry prompts, continuation prompts, session management)
//! - Agent-specific invocation behavior for each role
//! - Rate limit continuation prompt handling
//!
//! ## Test Organization
//!
//! Tests are organized by concern:
//! - `error_handling` - Missing prompts and workspace read errors
//! - `prompt_selection` - Retry prompt priorities and session ID management
//! - `agent_roles` - Agent-specific tests for planning, development, review, fix, commit
//! - `continuation` - Rate limit continuation prompt behavior

mod agent_roles;
mod continuation;
mod error_handling;
mod prompt_selection;

use std::io;
use std::path::Path;
use std::path::PathBuf;

/// Test helper: Workspace that fails reads for a specific path
///
/// Used to test error handling when prompt files cannot be read due to
/// permission errors or other non-NotFound I/O errors.
#[derive(Debug, Clone)]
pub(super) struct ReadFailingWorkspace {
    inner: crate::workspace::MemoryWorkspace,
    forbidden_read_path: PathBuf,
    error_kind: io::ErrorKind,
}

impl ReadFailingWorkspace {
    pub(super) fn new(
        inner: crate::workspace::MemoryWorkspace,
        forbidden_read_path: PathBuf,
        error_kind: io::ErrorKind,
    ) -> Self {
        Self {
            inner,
            forbidden_read_path,
            error_kind,
        }
    }
}

impl crate::workspace::Workspace for ReadFailingWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
        if relative == self.forbidden_read_path.as_path() {
            return Err(io::Error::new(self.error_kind, "read forbidden (test)"));
        }
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
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

    fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        self.inner.rename(from, to)
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
