//! Seam tests for `write_timeout_context` boundary function.
//!
//! Contract assertions:
//! 1. Workspace read capability is called on the logfile path.
//! 2. Workspace write capability is called on the context path with logfile content.
//! 3. Capability errors map to typed `WorkspaceReadFailed` / `WorkspaceWriteFailed`.
//! 4. Success emits `AgentTimeoutContextWritten` event.

use super::common::TestFixture;
use crate::agents::AgentRole;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::workspace::{DirEntry, MemoryWorkspace, Workspace};
use std::io;
use std::path::{Path, PathBuf};

// ---------------------------------------------------------------------------
// Helper: workspace that fails writes to a specific path.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct WriteFailingAtPathWorkspace {
    inner: MemoryWorkspace,
    forbidden_write_path: PathBuf,
    kind: io::ErrorKind,
}

impl WriteFailingAtPathWorkspace {
    fn new(inner: MemoryWorkspace, path: impl Into<PathBuf>, kind: io::ErrorKind) -> Self {
        Self {
            inner,
            forbidden_write_path: path.into(),
            kind,
        }
    }
}

impl Workspace for WriteFailingAtPathWorkspace {
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
        if relative == self.forbidden_write_path.as_path() {
            return Err(io::Error::new(
                self.kind,
                format!(
                    "write forbidden for {}",
                    self.forbidden_write_path.display()
                ),
            ));
        }
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

// ---------------------------------------------------------------------------
// Contract 1 + 3: success path — workspace read then write, correct event.
// ---------------------------------------------------------------------------

#[test]
fn write_timeout_context_reads_logfile_and_writes_to_context_path() {
    // Arrange: logfile exists with known content.
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/logs-run")
        .with_file(".agent/logs-run/agent.log", "AGENT LOG CONTENT\n");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    // Act
    let result = MainEffectHandler::write_timeout_context(
        &ctx,
        AgentRole::Developer,
        ".agent/logs-run/agent.log",
        ".agent/tmp/timeout-context-dev_1.md",
    )
    .expect("write_timeout_context should succeed when logfile exists");

    // Contract 1: context path written with logfile content.
    let context_path = Path::new(".agent/tmp/timeout-context-dev_1.md");
    assert!(
        fixture.workspace.exists(context_path),
        "context file must be written to the workspace"
    );
    let written = fixture
        .workspace
        .read(context_path)
        .expect("context file must be readable");
    assert_eq!(
        written, "AGENT LOG CONTENT\n",
        "context file content must match logfile content"
    );

    // Contract 3: correct typed event on success.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::TimeoutContextWritten {
                role,
                ref logfile_path,
                ref context_path,
            }) if role == AgentRole::Developer
                && logfile_path == ".agent/logs-run/agent.log"
                && context_path == ".agent/tmp/timeout-context-dev_1.md"
        ),
        "expected AgentTimeoutContextWritten event with correct role and paths, got: {:?}",
        result.event
    );
}

// ---------------------------------------------------------------------------
// Contract 2a: missing logfile → WorkspaceReadFailed typed error.
// ---------------------------------------------------------------------------

#[test]
fn write_timeout_context_maps_missing_logfile_to_workspace_read_failed() {
    // Arrange: logfile does NOT exist.
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    // Act
    let err = MainEffectHandler::write_timeout_context(
        &ctx,
        AgentRole::Developer,
        ".agent/logs-run/nonexistent.log",
        ".agent/tmp/timeout-context-dev_1.md",
    )
    .expect_err("should fail when logfile does not exist");

    // Contract 2a: typed WorkspaceReadFailed error preserves path for event-loop recovery.
    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error must downcast to ErrorEvent for event-loop handling");

    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::NotFound,
            } if path == ".agent/logs-run/nonexistent.log"
        ),
        "expected WorkspaceReadFailed(NotFound) for missing logfile, got: {error_event:?}"
    );
}

// ---------------------------------------------------------------------------
// Contract 2b: context write failure → WorkspaceWriteFailed typed error.
// ---------------------------------------------------------------------------

#[test]
fn write_timeout_context_maps_context_write_failure_to_workspace_write_failed() {
    // Arrange: logfile exists, but writing to the context path is forbidden.
    let base_ws = MemoryWorkspace::new_test()
        .with_dir(".agent/logs-run")
        .with_file(".agent/logs-run/agent.log", "log content");

    let failing_ws = WriteFailingAtPathWorkspace::new(
        base_ws,
        ".agent/tmp/timeout-context-dev_1.md",
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&failing_ws);

    // Act
    let err = MainEffectHandler::write_timeout_context(
        &ctx,
        AgentRole::Developer,
        ".agent/logs-run/agent.log",
        ".agent/tmp/timeout-context-dev_1.md",
    )
    .expect_err("should fail when context path write is forbidden");

    // Contract 2b: typed WorkspaceWriteFailed error preserves path.
    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error must downcast to ErrorEvent");

    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceWriteFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied,
            } if path == ".agent/tmp/timeout-context-dev_1.md"
        ),
        "expected WorkspaceWriteFailed(PermissionDenied) for context path, got: {error_event:?}"
    );
}
