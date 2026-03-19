use super::common::TestFixture;
use crate::reducer::event::LifecycleEvent;
use crate::reducer::handler::MainEffectHandler;
use crate::workspace::{MemoryWorkspace, Workspace};

#[test]
fn test_ensure_gitignore_creates_file_when_missing() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let result = MainEffectHandler::ensure_gitignore_entries(&ctx);

    match result.event {
        crate::reducer::event::PipelineEvent::Lifecycle(
            LifecycleEvent::GitignoreEntriesEnsured {
                added,
                existing,
                created,
            },
        ) => {
            assert_eq!(added.len(), 2);
            assert!(added.contains(&"/PROMPT*".to_string()));
            assert!(added.contains(&".agent/".to_string()));
            assert!(existing.is_empty());
            assert!(created);
        }
        _ => panic!("Expected GitignoreEntriesEnsured event"),
    }

    assert!(fixture.workspace.exists(std::path::Path::new(".gitignore")));
    let content = fixture
        .workspace
        .read(std::path::Path::new(".gitignore"))
        .unwrap();
    assert!(content.contains("/PROMPT*"));
    assert!(content.contains(".agent/"));
    assert!(content.contains("# Ralph-workflow artifacts"));
}

#[test]
fn test_ensure_gitignore_appends_when_file_exists() {
    let workspace = MemoryWorkspace::new_test().with_file(".gitignore", "node_modules/\n*.log\n");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let result = MainEffectHandler::ensure_gitignore_entries(&ctx);

    match result.event {
        crate::reducer::event::PipelineEvent::Lifecycle(
            LifecycleEvent::GitignoreEntriesEnsured {
                added,
                existing,
                created,
            },
        ) => {
            assert_eq!(added.len(), 2);
            assert!(existing.is_empty());
            assert!(!created);
        }
        _ => panic!("Expected GitignoreEntriesEnsured event"),
    }

    let content = fixture
        .workspace
        .read(std::path::Path::new(".gitignore"))
        .unwrap();
    assert!(content.contains("node_modules/"));
    assert!(content.contains("*.log"));
    assert!(content.contains("/PROMPT*"));
    assert!(content.contains(".agent/"));
}

#[test]
fn test_ensure_gitignore_idempotent_when_entries_exist() {
    let existing = "# Ralph-workflow artifacts (auto-generated)\n/PROMPT*\n.agent/\n";
    let workspace = MemoryWorkspace::new_test().with_file(".gitignore", existing);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let result = MainEffectHandler::ensure_gitignore_entries(&ctx);

    match result.event {
        crate::reducer::event::PipelineEvent::Lifecycle(
            LifecycleEvent::GitignoreEntriesEnsured {
                added,
                existing,
                created,
            },
        ) => {
            assert!(added.is_empty());
            assert_eq!(existing.len(), 2);
            assert!(!created);
        }
        _ => panic!("Expected GitignoreEntriesEnsured event"),
    }

    let content = fixture
        .workspace
        .read(std::path::Path::new(".gitignore"))
        .unwrap();
    assert_eq!(
        content,
        "# Ralph-workflow artifacts (auto-generated)\n/PROMPT*\n.agent/\n"
    );
}

#[test]
fn test_ensure_gitignore_partial_entries() {
    let workspace = MemoryWorkspace::new_test().with_file(".gitignore", "/PROMPT*\n");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let result = MainEffectHandler::ensure_gitignore_entries(&ctx);

    match result.event {
        crate::reducer::event::PipelineEvent::Lifecycle(
            LifecycleEvent::GitignoreEntriesEnsured {
                added,
                existing,
                created,
            },
        ) => {
            assert_eq!(added.len(), 1);
            assert!(added.contains(&".agent/".to_string()));
            assert_eq!(existing.len(), 1);
            assert!(existing.contains(&"/PROMPT*".to_string()));
            assert!(!created);
        }
        _ => panic!("Expected GitignoreEntriesEnsured event"),
    }
}

/// Workspace wrapper that simulates write failures for testing error handling.
#[derive(Debug, Clone)]
struct FailingWriteWorkspace {
    inner: MemoryWorkspace,
}

impl FailingWriteWorkspace {
    fn new(inner: MemoryWorkspace) -> Self {
        Self { inner }
    }
}

impl Workspace for FailingWriteWorkspace {
    fn root(&self) -> &std::path::Path {
        self.inner.root()
    }

    fn read(&self, relative: &std::path::Path) -> std::io::Result<String> {
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &std::path::Path) -> std::io::Result<Vec<u8>> {
        self.inner.read_bytes(relative)
    }

    fn write(&self, _relative: &std::path::Path, _content: &str) -> std::io::Result<()> {
        Err(std::io::Error::new(
            std::io::ErrorKind::PermissionDenied,
            "simulated permission denied",
        ))
    }

    fn write_bytes(&self, _relative: &std::path::Path, _content: &[u8]) -> std::io::Result<()> {
        Err(std::io::Error::new(
            std::io::ErrorKind::PermissionDenied,
            "simulated permission denied",
        ))
    }

    fn append_bytes(&self, relative: &std::path::Path, content: &[u8]) -> std::io::Result<()> {
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

    fn remove(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(
        &self,
        relative: &std::path::Path,
    ) -> std::io::Result<Vec<crate::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &std::path::Path, to: &std::path::Path) -> std::io::Result<()> {
        self.inner.rename(from, to)
    }

    fn set_readonly(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &std::path::Path) -> std::io::Result<()> {
        self.inner.set_writable(relative)
    }

    fn write_atomic(&self, relative: &std::path::Path, content: &str) -> std::io::Result<()> {
        self.inner.write_atomic(relative, content)
    }
}

#[test]
fn test_ensure_gitignore_handles_write_failure_gracefully() {
    let workspace = MemoryWorkspace::new_test().with_file(".gitignore", "node_modules/\n*.log\n");
    let failing_workspace = FailingWriteWorkspace::new(workspace.clone());

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx_with_workspace(&failing_workspace);

    let result = MainEffectHandler::ensure_gitignore_entries(&ctx);

    match result.event {
        crate::reducer::event::PipelineEvent::Lifecycle(
            LifecycleEvent::GitignoreEntriesEnsured {
                added,
                existing,
                created,
            },
        ) => {
            assert!(
                added.is_empty(),
                "entries_added should be empty when write fails"
            );
            assert!(existing.is_empty());
            assert!(!created);
        }
        _ => panic!("Expected GitignoreEntriesEnsured event"),
    }

    let content = fixture
        .workspace
        .read(std::path::Path::new(".gitignore"))
        .unwrap();
    assert_eq!(content, "node_modules/\n*.log\n");
    assert!(!content.contains("/PROMPT*"));
    assert!(!content.contains(".agent/"));
}

#[test]
fn test_ensure_gitignore_handles_write_failure_on_missing_file() {
    let workspace = MemoryWorkspace::new_test();
    let failing_workspace = FailingWriteWorkspace::new(workspace.clone());

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx_with_workspace(&failing_workspace);

    let result = MainEffectHandler::ensure_gitignore_entries(&ctx);

    match result.event {
        crate::reducer::event::PipelineEvent::Lifecycle(
            LifecycleEvent::GitignoreEntriesEnsured {
                added,
                existing,
                created,
            },
        ) => {
            assert!(
                added.is_empty(),
                "entries_added should be empty when write fails"
            );
            assert!(existing.is_empty());
            assert!(created);
        }
        _ => panic!("Expected GitignoreEntriesEnsured event"),
    }

    assert!(!fixture.workspace.exists(std::path::Path::new(".gitignore")));
}
