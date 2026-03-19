use super::common::TestFixture;
use crate::reducer::event::{ErrorEvent, WorkspaceIoErrorKind};
use crate::reducer::handler::MainEffectHandler;
use crate::workspace::{DirEntry, MemoryWorkspace, Workspace};
use std::io;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
struct RemoveFailingWorkspace {
    inner: MemoryWorkspace,
    forbidden_remove_path: PathBuf,
    kind: io::ErrorKind,
}

impl RemoveFailingWorkspace {
    fn new(inner: MemoryWorkspace, forbidden_remove_path: PathBuf, kind: io::ErrorKind) -> Self {
        Self {
            inner,
            forbidden_remove_path,
            kind,
        }
    }
}

impl Workspace for RemoveFailingWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
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
        if relative == self.forbidden_remove_path.as_path() {
            return Err(io::Error::new(
                self.kind,
                format!(
                    "remove forbidden for {}",
                    self.forbidden_remove_path.display()
                ),
            ));
        }
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
        if relative == self.forbidden_remove_path.as_path() {
            return Err(io::Error::new(
                self.kind,
                format!(
                    "remove_if_exists forbidden for {}",
                    self.forbidden_remove_path.display()
                ),
            ));
        }
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

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<DirEntry>> {
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

#[derive(Debug, Clone)]
struct ReadDirFailingWorkspace {
    inner: MemoryWorkspace,
    forbidden_read_dir_path: PathBuf,
    kind: io::ErrorKind,
}

impl ReadDirFailingWorkspace {
    fn new(inner: MemoryWorkspace, forbidden_read_dir_path: PathBuf, kind: io::ErrorKind) -> Self {
        Self {
            inner,
            forbidden_read_dir_path,
            kind,
        }
    }
}

impl Workspace for ReadDirFailingWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
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

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<DirEntry>> {
        if relative == self.forbidden_read_dir_path.as_path() {
            return Err(io::Error::new(
                self.kind,
                format!(
                    "read_dir forbidden for {}",
                    self.forbidden_read_dir_path.display()
                ),
            ));
        }
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

#[test]
fn test_cleanup_context_surfaces_remove_failures_as_error_event() {
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "# Issues\n")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/a.xml", "<a/>");
    let workspace = RemoveFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/PLAN.md"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let err = MainEffectHandler::cleanup_context(&ctx)
        .expect_err("cleanup_context should surface remove failures as typed error event");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceRemoveFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/PLAN.md"
        ),
        "expected WorkspaceRemoveFailed for PLAN.md removal, got: {error_event:?}"
    );
}

#[test]
fn test_cleanup_context_surfaces_read_dir_failures_as_error_event() {
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/a.xml", "<a/>");
    let workspace = ReadDirFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/tmp"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let err = MainEffectHandler::cleanup_context(&ctx)
        .expect_err("cleanup_context should surface read_dir failures as typed error event");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/tmp"
        ),
        "expected WorkspaceReadFailed for .agent/tmp read_dir, got: {error_event:?}"
    );
}
