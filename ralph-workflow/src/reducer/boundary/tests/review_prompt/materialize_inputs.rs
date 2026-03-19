use super::super::common::TestFixture;
use super::AtomicWriteEnforcingWorkspace;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::PipelineState;
use crate::workspace::MemoryWorkspace;
use crate::workspace::Workspace;
use std::io;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
struct ReadFailingWorkspace {
    inner: MemoryWorkspace,
    forbidden_read_path: PathBuf,
    kind: io::ErrorKind,
}

/// Workspace wrapper that enforces "parent directory must exist" semantics on write.
///
/// This models workspace implementations that do not implicitly create parent
/// directories, ensuring we don't rely on `Workspace::write` doing so.
#[derive(Debug, Clone)]
struct ParentDirRequiredWorkspace {
    inner: MemoryWorkspace,
}

impl ParentDirRequiredWorkspace {
    fn new(inner: MemoryWorkspace) -> Self {
        Self { inner }
    }

    fn ensure_parent_dir_exists(&self, relative: &Path) -> io::Result<()> {
        if let Some(parent) = relative.parent() {
            if !parent.as_os_str().is_empty() && !self.inner.is_dir(parent) {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("parent directory does not exist for {}", relative.display()),
                ));
            }
        }
        Ok(())
    }
}

impl Workspace for ParentDirRequiredWorkspace {
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
        self.ensure_parent_dir_exists(relative)?;
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.ensure_parent_dir_exists(relative)?;
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.ensure_parent_dir_exists(relative)?;
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
        self.ensure_parent_dir_exists(relative)?;
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_writable(relative)
    }
}

impl ReadFailingWorkspace {
    fn new(inner: MemoryWorkspace, forbidden_read_path: PathBuf, kind: io::ErrorKind) -> Self {
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

#[test]
fn test_materialize_review_inputs_uses_sentinel_plan_when_missing() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.config.isolation_mode = false;
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed with sentinel PLAN");

    assert!(
        matches!(
            result.event,
            PipelineEvent::PromptInput(
                crate::reducer::event::PromptInputEvent::ReviewInputsMaterialized { .. }
            )
        ),
        "Expected ReviewInputsMaterialized event with sentinel PLAN, got {:?}",
        result.event
    );

    // Verify the PLAN file was created with sentinel content (no isolation mode context)
    let plan_content = fixture
        .workspace
        .read(std::path::Path::new(".agent/PLAN.md"))
        .expect("PLAN.md should exist after materialization");
    assert_eq!(
        plan_content, "No PLAN provided",
        "Sentinel PLAN content should not include isolation mode context when isolation_mode=false"
    );
}

#[test]
fn test_materialize_review_inputs_creates_agent_dir_before_writing_sentinel_plan() {
    // Intentionally do not create `.agent/` up-front. Some workspace implementations
    // do not auto-create parent directories on write.
    let inner = MemoryWorkspace::new_test();
    let workspace = ParentDirRequiredWorkspace::new(inner);

    let mut fixture = TestFixture::new();
    fixture.config.isolation_mode = false;
    let ctx = fixture.ctx_with_workspace(&workspace);

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should create .agent/ and write sentinel PLAN");

    let plan_content = workspace
        .read(Path::new(".agent/PLAN.md"))
        .expect("PLAN.md should exist after materialization");
    assert_eq!(plan_content, "No PLAN provided");
}

#[test]
fn test_materialize_review_inputs_does_not_mask_non_not_found_plan_read_errors() {
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/PLAN.md"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    fixture.config.isolation_mode = false;
    let ctx = fixture.ctx_with_workspace(&workspace);

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let err = handler
        .materialize_review_inputs(&ctx, 0)
        .expect_err("materialize_review_inputs should surface non-NotFound PLAN read failures");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/PLAN.md"
        ),
        "expected WorkspaceReadFailed for PLAN read, got: {error_event:?}"
    );
}

#[test]
fn test_materialize_review_inputs_does_not_mask_non_not_found_diff_backup_read_errors() {
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/DIFF.backup"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    fixture.config.isolation_mode = false;
    let ctx = fixture.ctx_with_workspace(&workspace);

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let err = handler
        .materialize_review_inputs(&ctx, 0)
        .expect_err("materialize_review_inputs should surface non-NotFound DIFF read failures");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/DIFF.backup"
        ),
        "expected WorkspaceReadFailed for DIFF backup read, got: {error_event:?}"
    );
}

#[test]
fn test_materialize_review_inputs_does_not_mask_non_not_found_diff_baseline_read_errors() {
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");
    let workspace = ReadFailingWorkspace::new(
        inner,
        PathBuf::from(".agent/DIFF.base"),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let err = handler
        .materialize_review_inputs(&ctx, 0)
        .expect_err("materialize_review_inputs should surface non-NotFound baseline read failures");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == ".agent/DIFF.base"
        ),
        "expected WorkspaceReadFailed for DIFF baseline read, got: {error_event:?}"
    );
}

#[test]
fn test_materialize_review_inputs_uses_sentinel_plan_with_isolation_mode_context() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.config.isolation_mode = true;
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed with sentinel PLAN in isolation mode");

    assert!(
        matches!(
            result.event,
            PipelineEvent::PromptInput(
                crate::reducer::event::PromptInputEvent::ReviewInputsMaterialized { .. }
            )
        ),
        "Expected ReviewInputsMaterialized event with sentinel PLAN, got {:?}",
        result.event
    );

    // Verify the PLAN file was created with sentinel content including isolation mode context
    let plan_content = fixture
        .workspace
        .read(std::path::Path::new(".agent/PLAN.md"))
        .expect("PLAN.md should exist after materialization");
    assert_eq!(
        plan_content, "No PLAN provided (normal in isolation mode)",
        "Sentinel PLAN content should include isolation mode context when isolation_mode=true"
    );
}

#[test]
fn test_materialize_review_inputs_uses_fallback_diff_instructions_when_missing() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed with fallback DIFF instructions");

    assert!(
        matches!(
            result.event,
            PipelineEvent::PromptInput(
                crate::reducer::event::PromptInputEvent::ReviewInputsMaterialized { .. }
            )
        ),
        "Expected ReviewInputsMaterialized event with fallback DIFF, got {:?}",
        result.event
    );
}

#[test]
fn test_materialize_review_inputs_writes_oversize_diff_with_atomic_write() {
    let large_diff = "d".repeat(crate::prompts::MAX_INLINE_CONTENT_SIZE + 1);
    let inner = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/DIFF.backup", &large_diff)
        .with_dir(".agent/tmp");
    let workspace =
        AtomicWriteEnforcingWorkspace::new(inner, std::path::PathBuf::from(".agent/tmp/diff.txt"));

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&workspace);

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should return an EffectResult");

    assert!(
        matches!(
            result.event,
            PipelineEvent::PromptInput(
                crate::reducer::event::PromptInputEvent::ReviewInputsMaterialized { .. }
            )
        ),
        "Expected ReviewInputsMaterialized event, got {:?}",
        result.event
    );

    let written = workspace
        .read(std::path::Path::new(".agent/tmp/diff.txt"))
        .expect("materialized diff file should be written");
    assert_eq!(written, large_diff);
}
