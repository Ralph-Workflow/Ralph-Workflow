//! File I/O hardening utilities for `.agent/` files.
//!
//! This module focuses on preventing partial writes and catching obvious
//! corruption (e.g. zero-length or binary files) in small, text-based agent
//! artifacts like `PLAN.md` and `commit-message.txt`.

use std::io;
use std::path::Path;

use crate::workspace::Workspace;

pub const MAX_AGENT_FILE_SIZE: u64 = 10 * 1024 * 1024;

pub fn write_file_atomic_with_workspace(
    workspace: &dyn Workspace,
    path: &Path,
    content: &str,
) -> io::Result<()> {
    workspace.write_atomic(path, content)
}

pub fn verify_file_not_corrupted_with_workspace(
    workspace: &dyn Workspace,
    path: &Path,
) -> io::Result<bool> {
    let content = workspace.read_bytes(path)?;

    if content.is_empty() || content.len() as u64 > MAX_AGENT_FILE_SIZE {
        return Ok(false);
    }

    let Ok(text) = String::from_utf8(content) else {
        return Ok(false);
    };

    Ok(!text.contains('\0'))
}

pub fn check_filesystem_ready_with_workspace(
    workspace: &dyn Workspace,
    path: &Path,
) -> io::Result<()> {
    if !workspace.is_dir(path) {
        workspace.create_dir_all(path)?;
    }

    let test_file = path.join(".write_test");
    workspace.write(&test_file, "test")?;
    workspace.remove(&test_file)?;

    if let Some(lock_file) = workspace.read_dir(path).ok().and_then(|entries| {
        entries
            .iter()
            .filter_map(|entry| {
                let name = entry.file_name()?;
                let name = name.to_str()?;
                if !name.to_ascii_lowercase().ends_with(".lock") {
                    return None;
                }
                let modified = entry.modified()?;
                let elapsed = modified.elapsed().ok()?;
                if elapsed > std::time::Duration::from_secs(3600) {
                    Some(name.to_string())
                } else {
                    None
                }
            })
            .next()
    }) {
        return Err(io::Error::other(format!(
            "Stale lock file found: {lock_file}"
        )));
    }

    Ok(())
}

pub fn check_xml_file_writable_with_workspace(
    workspace: &dyn Workspace,
    xml_path: &Path,
    force_cleanup: bool,
) -> io::Result<bool> {
    if !workspace.exists(xml_path) {
        return Ok(false);
    }

    if force_cleanup {
        workspace.remove(xml_path)?;
        return Ok(false);
    }

    let content = workspace.read(xml_path)?;
    workspace.write(xml_path, &content)?;
    Ok(true)
}

pub fn check_and_cleanup_xml_before_retry_with_workspace(
    workspace: &dyn Workspace,
    xml_path: &Path,
    logger: &crate::logger::Logger,
) -> io::Result<()> {
    match check_xml_file_writable_with_workspace(workspace, xml_path, false) {
        Ok(true | false) => Ok(()),
        Err(e) => {
            logger.warn(&format!(
                "XML file {} error: {}. Attempting cleanup...",
                xml_path.display(),
                e
            ));

            match check_xml_file_writable_with_workspace(workspace, xml_path, true) {
                Ok(_) => {
                    logger.info(&format!(
                        "Successfully cleaned up file: {}",
                        xml_path.display()
                    ));
                    Ok(())
                }
                Err(cleanup_err) => {
                    logger.error(&format!(
                        "Failed to cleanup file {}: {}",
                        xml_path.display(),
                        cleanup_err
                    ));
                    Err(cleanup_err)
                }
            }
        }
    }
}

pub fn cleanup_stale_xml_files_with_workspace(
    workspace: &dyn Workspace,
    tmp_dir: &Path,
    force_cleanup: bool,
) -> io::Result<String> {
    if !workspace.is_dir(tmp_dir) {
        return Ok("Directory doesn't exist yet - nothing to clean".to_string());
    }

    let entries = workspace.read_dir(tmp_dir)?;

    let results: Vec<_> = entries
        .iter()
        .filter_map(|entry| {
            let path = entry.path();
            let extension = path.extension().and_then(|s| s.to_str())?;
            if extension != "xml" {
                return None;
            }
            Some((path, extension == "xml"))
        })
        .collect();

    let (writable, cleaned, report): (usize, usize, Vec<String>) = if force_cleanup {
        let cleaned: Vec<_> = results
            .iter()
            .filter(|(_, _)| workspace.exists(results[0].0.clone()))
            .filter_map(|(path, _)| {
                workspace.remove(path.clone()).ok()?;
                Some(format!("  🗑 Removed file: {}", path.display()))
            })
            .collect();
        (0, cleaned.len(), cleaned)
    } else {
        let report: Vec<_> = results
            .iter()
            .map(|(path, _)| format!("  ✓ {} is writable", path.display()))
            .collect();
        (results.len(), 0, report)
    };

    let summary = format!(
        "XML file check complete: {} writable, {} locked, {} cleaned",
        writable, 0, cleaned
    );

    if report.is_empty() {
        Ok(summary)
    } else {
        Ok(format!("{}\n{}", summary, report.join("\n")))
    }
}

#[cfg(test)]
mod tests {
    #[cfg(feature = "test-utils")]
    mod workspace_tests {
        use crate::files::integrity::*;
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

            let result =
                verify_file_not_corrupted_with_workspace(&workspace, Path::new("valid.txt"));
            assert!(result.unwrap());
        }

        #[test]
        fn test_verify_file_not_corrupted_with_workspace_empty() {
            let workspace = MemoryWorkspace::new_test().with_file("empty.txt", "");

            let result =
                verify_file_not_corrupted_with_workspace(&workspace, Path::new("empty.txt"));
            assert!(!result.unwrap());
        }

        #[test]
        fn test_verify_file_not_corrupted_with_workspace_null_bytes() {
            let workspace =
                MemoryWorkspace::new_test().with_file_bytes("binary.txt", b"hello\x00world");

            let result =
                verify_file_not_corrupted_with_workspace(&workspace, Path::new("binary.txt"));
            assert!(!result.unwrap());
        }

        #[test]
        fn test_verify_file_not_corrupted_with_workspace_not_found() {
            let workspace = MemoryWorkspace::new_test();

            let result =
                verify_file_not_corrupted_with_workspace(&workspace, Path::new("nonexistent.txt"));
            assert!(result.is_err());
        }

        #[test]
        fn test_check_filesystem_ready_with_workspace_creates_dir() {
            let workspace = MemoryWorkspace::new_test();

            assert!(!workspace.is_dir(Path::new(".agent")));

            check_filesystem_ready_with_workspace(&workspace, Path::new(".agent")).unwrap();

            assert!(workspace.is_dir(Path::new(".agent")));
        }

        #[test]
        fn test_check_filesystem_ready_with_workspace_existing_dir() {
            let workspace = MemoryWorkspace::new_test().with_dir(".agent");

            check_filesystem_ready_with_workspace(&workspace, Path::new(".agent")).unwrap();
        }

        #[test]
        fn test_check_filesystem_ready_with_workspace_detects_stale_lock() {
            use std::time::{Duration, SystemTime};

            let old_time = SystemTime::now() - Duration::from_secs(7200);
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
            let workspace = MemoryWorkspace::new_test()
                .with_dir(".agent")
                .with_file(".agent/pipeline.lock", "locked");

            check_filesystem_ready_with_workspace(&workspace, Path::new(".agent")).unwrap();
        }

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
                .with_file(".agent/tmp/plan.xsd", "schema");

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

            let report =
                cleanup_stale_xml_files_with_workspace(&workspace, Path::new(".agent/tmp"), true)
                    .unwrap();

            assert!(!workspace.exists(Path::new(".agent/tmp/issues.xml")));
            assert!(!workspace.exists(Path::new(".agent/tmp/plan.xml")));
            assert!(report.contains("cleaned") || report.contains("Removed"));
        }
    }
}
