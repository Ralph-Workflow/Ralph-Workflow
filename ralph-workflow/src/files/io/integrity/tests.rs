// =========================================================================
// Workspace-based tests (for testability without real filesystem)
// =========================================================================

#[cfg(feature = "test-utils")]
mod workspace_tests {
    use super::super::*;
    use crate::workspace::{MemoryWorkspace, Workspace};
    use std::path::Path;

    #[test]
    fn test_write_file_atomic_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        write_file_atomic_with_workspace(&workspace, Path::new("test.txt"), "content").unwrap();

        assert_eq!(workspace.read(Path::new("test.txt")).unwrap(), "content");
    }

    #[test]
    fn test_write_file_atomic_with_workspace_creates_parent_dirs() {
        let workspace = MemoryWorkspace::new_test();

        write_file_atomic_with_workspace(
            &workspace,
            Path::new(".agent/tmp/output.txt"),
            "nested content",
        )
        .unwrap();

        assert!(workspace.exists(Path::new(".agent/tmp/output.txt")));
        assert_eq!(
            workspace.read(Path::new(".agent/tmp/output.txt")).unwrap(),
            "nested content"
        );
    }

    #[test]
    fn test_verify_file_not_corrupted_with_workspace_valid() {
        let workspace =
            MemoryWorkspace::new_test().with_file("valid.txt", "valid content\nwith lines");

        let result = verify_file_not_corrupted_with_workspace(&workspace, Path::new("valid.txt"));
        assert!(result.unwrap());
    }

    #[test]
    fn test_verify_file_not_corrupted_with_workspace_empty() {
        let workspace = MemoryWorkspace::new_test().with_file("empty.txt", "");

        let result = verify_file_not_corrupted_with_workspace(&workspace, Path::new("empty.txt"));
        assert!(!result.unwrap());
    }

    #[test]
    fn test_verify_file_not_corrupted_with_workspace_null_bytes() {
        let workspace =
            MemoryWorkspace::new_test().with_file_bytes("binary.txt", b"hello\x00world");

        let result = verify_file_not_corrupted_with_workspace(&workspace, Path::new("binary.txt"));
        assert!(!result.unwrap());
    }

    #[test]
    fn test_verify_file_not_corrupted_with_workspace_not_found() {
        let workspace = MemoryWorkspace::new_test();

        let result =
            verify_file_not_corrupted_with_workspace(&workspace, Path::new("nonexistent.txt"));
        assert!(result.is_err());
    }

    // =====================================================================
    // Tests for check_filesystem_ready_with_workspace
    // =====================================================================

    #[test]
    fn test_check_filesystem_ready_with_workspace_creates_dir() {
        let workspace = MemoryWorkspace::new_test();

        // Directory doesn't exist yet
        assert!(!workspace.is_dir(Path::new(".agent")));

        // Should create the directory and succeed
        check_filesystem_ready_with_workspace(&workspace, Path::new(".agent")).unwrap();

        assert!(workspace.is_dir(Path::new(".agent")));
    }

    #[test]
    fn test_check_filesystem_ready_with_workspace_existing_dir() {
        let workspace = MemoryWorkspace::new_test().with_dir(".agent");

        // Should succeed on existing directory
        check_filesystem_ready_with_workspace(&workspace, Path::new(".agent")).unwrap();
    }

    #[test]
    fn test_check_filesystem_ready_with_workspace_detects_stale_lock() {
        use std::time::{Duration, SystemTime};

        // Create a workspace with a lock file that has an old modification time
        let old_time = SystemTime::now() - Duration::from_secs(7200); // 2 hours ago
        let workspace = MemoryWorkspace::new_test()
            .with_dir(".agent")
            .with_file_at_time(".agent/pipeline.lock", "locked", old_time);

        let result = check_filesystem_ready_with_workspace(&workspace, Path::new(".agent"));
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(err_msg.contains("Stale lock file"));
    }

    #[test]
    fn test_check_filesystem_ready_with_workspace_ignores_fresh_lock() {
        // Create a workspace with a fresh lock file
        let workspace = MemoryWorkspace::new_test()
            .with_dir(".agent")
            .with_file(".agent/pipeline.lock", "locked");

        // Fresh lock files should not cause an error
        check_filesystem_ready_with_workspace(&workspace, Path::new(".agent")).unwrap();
    }

    // =====================================================================
    // Tests for cleanup_stale_xml_files_with_workspace
    // =====================================================================

    #[test]
    fn test_cleanup_stale_xml_files_with_workspace_nonexistent() {
        let workspace = MemoryWorkspace::new_test();

        let report =
            cleanup_stale_xml_files_with_workspace(&workspace, Path::new(".agent/tmp"), false)
                .unwrap();
        assert!(report.contains("doesn't exist"));
    }

    #[test]
    fn test_cleanup_stale_xml_files_with_workspace_empty_dir() {
        let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");

        let report =
            cleanup_stale_xml_files_with_workspace(&workspace, Path::new(".agent/tmp"), false)
                .unwrap();
        assert!(report.contains("0 writable"));
    }

    #[test]
    fn test_cleanup_stale_xml_files_with_workspace_finds_xml() {
        let workspace = MemoryWorkspace::new_test()
            .with_file(".agent/tmp/issues.xml", "<issues/>")
            .with_file(".agent/tmp/plan.xml", "<plan/>")
            .with_file(".agent/tmp/plan.xsd", "schema"); // XSD should be ignored

        let report =
            cleanup_stale_xml_files_with_workspace(&workspace, Path::new(".agent/tmp"), false)
                .unwrap();
        assert!(
            report.contains("2 writable"),
            "Should find 2 XML files, got: {report}"
        );
    }

    #[test]
    fn test_cleanup_stale_xml_files_with_workspace_force_cleanup() {
        let workspace = MemoryWorkspace::new_test()
            .with_file(".agent/tmp/issues.xml", "<issues/>")
            .with_file(".agent/tmp/plan.xml", "<plan/>");

        // With force_cleanup=true, files should be removed
        let report =
            cleanup_stale_xml_files_with_workspace(&workspace, Path::new(".agent/tmp"), true)
                .unwrap();

        // Files should be removed
        assert!(!workspace.exists(Path::new(".agent/tmp/issues.xml")));
        assert!(!workspace.exists(Path::new(".agent/tmp/plan.xml")));
        assert!(report.contains("cleaned") || report.contains("Removed"));
    }
}
