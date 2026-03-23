// Tests for the Workspace trait implementations.
//
// This file contains all unit tests for WorkspaceFs and MemoryWorkspace.

use std::io;

// =========================================================================
// WorkspaceFs write_atomic interrupt-skipping tests
// =========================================================================

/// Verify that `write_atomic` succeeds (writes content correctly) even when
/// `user_interrupted_occurred()` returns true.
///
/// During interrupt-triggered shutdown, `write_atomic` skips the expensive
/// `sync_all()` call to avoid hanging indefinitely in `F_FULLFSYNC` on macOS.
/// The file must still be written correctly despite the skipped sync.
#[test]
fn write_atomic_succeeds_when_user_interrupted_occurred() {
    use crate::interrupt::{
        request_user_interrupt, reset_user_interrupted_occurred, take_user_interrupt_request,
    };
    use tempfile::TempDir;

    // The interrupt flags are process-global; coordinate all test access so
    // parallel tests can't steal each other's pending interrupt requests.
    let _lock = crate::interrupt::interrupt_test_lock();

    // Guarantee clean state
    take_user_interrupt_request();
    reset_user_interrupted_occurred();

    let tmp = TempDir::new().expect("create temp dir");
    let ws = WorkspaceFs::new(tmp.path().to_path_buf());

    // Signal interrupt BEFORE calling write_atomic
    request_user_interrupt();

    let result = ws.write_atomic(Path::new("checkpoint.json"), r#"{"test": true}"#);

    // Clean up interrupt flags
    take_user_interrupt_request();
    reset_user_interrupted_occurred();

    // write_atomic must still succeed and produce readable content
    assert!(
        result.is_ok(),
        "write_atomic must succeed even when interrupted: {result:?}"
    );
    assert_eq!(
        ws.read(Path::new("checkpoint.json"))
            .expect("file must be readable"),
        r#"{"test": true}"#
    );
}

// =========================================================================
// WorkspaceFs path resolution tests (no filesystem access needed)
// =========================================================================

#[test]
fn test_workspace_fs_root() {
    let ws = WorkspaceFs::new(PathBuf::from("/test/repo"));
    assert_eq!(ws.root(), Path::new("/test/repo"));
}

#[test]
fn test_workspace_fs_agent_paths() {
    let ws = WorkspaceFs::new(PathBuf::from("/test/repo"));

    assert_eq!(ws.agent_dir(), PathBuf::from("/test/repo/.agent"));
    assert_eq!(ws.agent_tmp(), PathBuf::from("/test/repo/.agent/tmp"));
    assert_eq!(ws.plan_md(), PathBuf::from("/test/repo/.agent/PLAN.md"));
    assert_eq!(ws.issues_md(), PathBuf::from("/test/repo/.agent/ISSUES.md"));
    assert_eq!(
        ws.commit_message(),
        PathBuf::from("/test/repo/.agent/commit-message.txt")
    );
    assert_eq!(
        ws.checkpoint(),
        PathBuf::from("/test/repo/.agent/checkpoint.json")
    );
    assert_eq!(
        ws.start_commit(),
        PathBuf::from("/test/repo/.agent/start_commit")
    );
    assert_eq!(ws.prompt_md(), PathBuf::from("/test/repo/PROMPT.md"));
}

#[test]
fn test_workspace_fs_dynamic_paths() {
    let ws = WorkspaceFs::new(PathBuf::from("/test/repo"));

    assert_eq!(
        ws.xsd_path("plan"),
        PathBuf::from("/test/repo/.agent/tmp/plan.xsd")
    );
    assert_eq!(
        ws.xml_path("issues"),
        PathBuf::from("/test/repo/.agent/tmp/issues.xml")
    );
    assert_eq!(
        ws.log_path("agent.log"),
        PathBuf::from("/test/repo/.agent/logs/agent.log")
    );
}

#[test]
fn test_workspace_fs_absolute() {
    let ws = WorkspaceFs::new(PathBuf::from("/test/repo"));

    let abs = ws.absolute(Path::new(".agent/tmp/plan.xml"));
    assert_eq!(abs, PathBuf::from("/test/repo/.agent/tmp/plan.xml"));

    let abs_str = ws.absolute_str(".agent/tmp/plan.xml");
    assert_eq!(abs_str, "/test/repo/.agent/tmp/plan.xml");
}

// =========================================================================
// MemoryWorkspace tests
// =========================================================================

#[test]
fn test_memory_workspace_read_write() {
    let ws = MemoryWorkspace::new_test();

    ws.write(Path::new(".agent/test.txt"), "hello").unwrap();
    assert_eq!(ws.read(Path::new(".agent/test.txt")).unwrap(), "hello");
    assert!(ws.was_written(".agent/test.txt"));
}

#[test]
fn test_memory_workspace_with_file() {
    let ws = MemoryWorkspace::new_test().with_file("existing.txt", "pre-existing content");

    assert_eq!(
        ws.read(Path::new("existing.txt")).unwrap(),
        "pre-existing content"
    );
}

#[test]
fn test_memory_workspace_exists() {
    let ws = MemoryWorkspace::new_test().with_file("exists.txt", "content");

    assert!(ws.exists(Path::new("exists.txt")));
    assert!(!ws.exists(Path::new("not_exists.txt")));
}

#[test]
fn test_memory_workspace_remove() {
    let ws = MemoryWorkspace::new_test().with_file("to_delete.txt", "content");

    assert!(ws.exists(Path::new("to_delete.txt")));
    ws.remove(Path::new("to_delete.txt")).unwrap();
    assert!(!ws.exists(Path::new("to_delete.txt")));
}

#[test]
fn test_memory_workspace_read_nonexistent_fails() {
    let ws = MemoryWorkspace::new_test();

    let result = ws.read(Path::new("nonexistent.txt"));
    assert!(result.is_err());
    assert_eq!(result.unwrap_err().kind(), io::ErrorKind::NotFound);
}

#[test]
fn test_memory_workspace_written_files() {
    let ws = MemoryWorkspace::new_test();

    ws.write(Path::new("file1.txt"), "content1").unwrap();
    ws.write(Path::new("file2.txt"), "content2").unwrap();

    let files = ws.written_files();
    // Verifying both expected files exist and have correct content
    // (unwrap would panic if keys are missing from HashMap)
    assert_eq!(
        String::from_utf8_lossy(files.get(&PathBuf::from("file1.txt")).unwrap()),
        "content1"
    );
    assert_eq!(
        String::from_utf8_lossy(files.get(&PathBuf::from("file2.txt")).unwrap()),
        "content2"
    );
}

#[test]
fn test_memory_workspace_get_file() {
    let ws = MemoryWorkspace::new_test();

    ws.write(Path::new("test.txt"), "test content").unwrap();
    assert_eq!(ws.get_file("test.txt"), Some("test content".to_string()));
    assert_eq!(ws.get_file("nonexistent.txt"), None);
}

#[test]
fn test_memory_workspace_clear() {
    let ws = MemoryWorkspace::new_test().with_file("file.txt", "content");

    assert!(ws.exists(Path::new("file.txt")));
    ws.clear();
    assert!(!ws.exists(Path::new("file.txt")));
}

#[test]
fn test_memory_workspace_absolute_str() {
    let ws = MemoryWorkspace::new_test();

    assert_eq!(
        ws.absolute_str(".agent/tmp/commit_message.xml"),
        "/test/repo/.agent/tmp/commit_message.xml"
    );
}

#[test]
fn test_memory_workspace_creates_parent_dirs() {
    let ws = MemoryWorkspace::new_test();

    ws.write(Path::new("a/b/c/file.txt"), "content").unwrap();

    // Parent directories should be tracked
    assert!(ws.is_dir(Path::new("a")));
    assert!(ws.is_dir(Path::new("a/b")));
    assert!(ws.is_dir(Path::new("a/b/c")));
    assert!(ws.is_file(Path::new("a/b/c/file.txt")));
}

#[test]
fn test_memory_workspace_rename() {
    let ws = MemoryWorkspace::new_test().with_file("old.txt", "content");

    ws.rename(Path::new("old.txt"), Path::new("new.txt"))
        .unwrap();

    assert!(!ws.exists(Path::new("old.txt")));
    assert!(ws.exists(Path::new("new.txt")));
    assert_eq!(ws.read(Path::new("new.txt")).unwrap(), "content");
}

#[test]
fn test_memory_workspace_rename_creates_parent_dirs() {
    let ws = MemoryWorkspace::new_test().with_file("old.txt", "content");

    ws.rename(Path::new("old.txt"), Path::new("a/b/new.txt"))
        .unwrap();

    assert!(!ws.exists(Path::new("old.txt")));
    assert!(ws.is_dir(Path::new("a")));
    assert!(ws.is_dir(Path::new("a/b")));
    assert!(ws.exists(Path::new("a/b/new.txt")));
}

#[test]
fn test_memory_workspace_rename_nonexistent_fails() {
    let ws = MemoryWorkspace::new_test();

    let result = ws.rename(Path::new("nonexistent.txt"), Path::new("new.txt"));
    assert!(result.is_err());
    assert_eq!(result.unwrap_err().kind(), io::ErrorKind::NotFound);
}

#[test]
fn test_memory_workspace_set_readonly_noop() {
    // In-memory workspace doesn't track permissions, but should succeed
    let ws = MemoryWorkspace::new_test().with_file("test.txt", "content");

    // Should succeed (no-op)
    ws.set_readonly(Path::new("test.txt")).unwrap();
    ws.set_writable(Path::new("test.txt")).unwrap();

    // File should still be readable
    assert_eq!(ws.read(Path::new("test.txt")).unwrap(), "content");
}

#[test]
fn test_memory_workspace_write_atomic() {
    let ws = MemoryWorkspace::new_test();

    ws.write_atomic(Path::new("atomic.txt"), "atomic content")
        .unwrap();

    assert_eq!(ws.read(Path::new("atomic.txt")).unwrap(), "atomic content");
}

#[test]
fn test_memory_workspace_write_atomic_creates_parent_dirs() {
    let ws = MemoryWorkspace::new_test();

    ws.write_atomic(Path::new("a/b/c/atomic.txt"), "nested atomic")
        .unwrap();

    assert!(ws.is_dir(Path::new("a")));
    assert!(ws.is_dir(Path::new("a/b")));
    assert!(ws.is_dir(Path::new("a/b/c")));
    assert_eq!(
        ws.read(Path::new("a/b/c/atomic.txt")).unwrap(),
        "nested atomic"
    );
}

#[test]
fn test_memory_workspace_write_atomic_overwrites() {
    let ws = MemoryWorkspace::new_test().with_file("existing.txt", "old content");

    ws.write_atomic(Path::new("existing.txt"), "new content")
        .unwrap();

    assert_eq!(ws.read(Path::new("existing.txt")).unwrap(), "new content");
}
