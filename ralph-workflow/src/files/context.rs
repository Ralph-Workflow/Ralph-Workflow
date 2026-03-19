//! Context file management for Ralph's agent files.
//!
//! This module handles operations on context files (STATUS.md, NOTES.md, ISSUES.md)
//! in the `.agent/` directory, including cleanup for isolation mode and fresh eyes
//! for the reviewer phase.
use crate::logger::Logger;
use crate::workspace::Workspace;
use std::io;
use std::path::Path;

// Vague status line constants (for isolation mode)
pub const VAGUE_STATUS_LINE: &str = "In progress.";
pub const VAGUE_NOTES_LINE: &str = "Notes.";
pub const VAGUE_ISSUES_LINE: &str = "No issues recorded.";

/// Delete ISSUES.md after the final fix iteration completes using workspace.
///
/// This version uses the [`Workspace`] trait for file operations,
/// allowing tests to use an in-memory workspace instead of the real filesystem.
///
/// # Arguments
///
/// * `workspace` - The workspace for file operations
/// * `logger` - Logger for output
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn delete_issues_file_for_isolation_with_workspace(
    workspace: &dyn Workspace,
    logger: &Logger,
) -> io::Result<()> {
    let issues_path = Path::new(".agent/ISSUES.md");

    if workspace.exists(issues_path) {
        workspace.remove(issues_path)?;
        logger.info("Isolation mode: deleted .agent/ISSUES.md after final fix");
    }

    Ok(())
}

/// Overwrite a file with a single-line content using workspace.
///
/// Enforces "1 sentence, 1 line" semantics by taking only the first line.
/// Uses atomic write to ensure file integrity.
fn overwrite_one_liner_with_workspace(
    workspace: &dyn Workspace,
    path: &Path,
    line: &str,
) -> io::Result<()> {
    let first_line = line.lines().next().unwrap_or_default().trim();
    let content = if first_line.is_empty() {
        "\n".to_string()
    } else {
        format!("{first_line}\n")
    };
    workspace.write_atomic(path, &content)
}

/// Clean context before reviewer phase using workspace.
///
/// This version uses the [`Workspace`] trait for file operations,
/// allowing tests to use an in-memory workspace instead of the real filesystem.
///
/// When `isolation_mode` is true (the default), this function does nothing
/// since STATUS.md, NOTES.md and ISSUES.md should not exist in isolation mode.
///
/// In non-isolation mode, this overwrites the context files with vague
/// one-liners to give the reviewer "fresh eyes" without context from
/// the development phase.
///
/// # Arguments
///
/// * `workspace` - The workspace for file operations
/// * `logger` - Logger for output
/// * `isolation_mode` - If true, skip cleanup since files don't exist
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn clean_context_for_reviewer_with_workspace(
    workspace: &dyn Workspace,
    logger: &Logger,
    isolation_mode: bool,
) -> io::Result<()> {
    if isolation_mode {
        // In isolation mode, these files don't exist, so nothing to clean
        logger.info("Isolation mode: skipping context cleanup (files don't exist)");
        return Ok(());
    }

    logger.info("Cleaning context for reviewer (fresh eyes)...");

    // Remove any archived context; preserving it defeats the "fresh eyes" intent.
    let archive_dir = Path::new(".agent/archive");
    // Best-effort: if this fails, proceed with overwriting the live files.
    let _ = workspace.remove_dir_all_if_exists(archive_dir);

    // Overwrite live context files with intentionally vague one-liners.
    overwrite_one_liner_with_workspace(
        workspace,
        Path::new(".agent/STATUS.md"),
        VAGUE_STATUS_LINE,
    )?;
    overwrite_one_liner_with_workspace(workspace, Path::new(".agent/NOTES.md"), VAGUE_NOTES_LINE)?;
    overwrite_one_liner_with_workspace(
        workspace,
        Path::new(".agent/ISSUES.md"),
        VAGUE_ISSUES_LINE,
    )?;

    logger.success("Context cleaned for reviewer");
    Ok(())
}

/// Update the status file with minimal, vague content using workspace.
///
/// This version uses the [`Workspace`] trait for file operations,
/// allowing tests to use an in-memory workspace instead of the real filesystem.
///
/// Status is intentionally kept to 1 sentence to prevent context contamination.
/// When `isolation_mode` is true (the default), this function does nothing
/// since STATUS.md should not exist in isolation mode.
///
/// # Arguments
///
/// * `workspace` - The workspace for file operations
/// * `_status` - Status string (unused, always writes vague status)
/// * `isolation_mode` - If true, do nothing since STATUS.md should not exist
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn update_status_with_workspace(
    workspace: &dyn Workspace,
    _status: &str,
    isolation_mode: bool,
) -> io::Result<()> {
    if isolation_mode {
        // In isolation mode, STATUS.md should not exist
        return Ok(());
    }
    overwrite_one_liner_with_workspace(workspace, Path::new(".agent/STATUS.md"), VAGUE_STATUS_LINE)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::logger::Colors;
    use crate::workspace::{MemoryWorkspace, Workspace};

    // =========================================================================
    // Workspace-based tests (for testability without real filesystem)
    // =========================================================================
    //
    // Note: Old tests using with_temp_cwd have been removed since production
    // code now uses workspace-based functions. The non-workspace functions
    // have been removed; these workspace-based tests cover current behavior.

    #[test]
    fn test_delete_issues_file_for_isolation_with_workspace() {
        let workspace = MemoryWorkspace::new_test()
            .with_dir(".agent")
            .with_file(".agent/ISSUES.md", "some issues");

        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);

        delete_issues_file_for_isolation_with_workspace(&workspace, &logger).unwrap();

        assert!(
            !workspace.exists(Path::new(".agent/ISSUES.md")),
            "ISSUES.md should be deleted via workspace"
        );
    }

    #[test]
    fn test_delete_issues_file_for_isolation_with_workspace_nonexistent() {
        // File doesn't exist - should succeed silently
        let workspace = MemoryWorkspace::new_test().with_dir(".agent");

        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);

        let result = delete_issues_file_for_isolation_with_workspace(&workspace, &logger);
        assert!(result.is_ok(), "Should succeed when file doesn't exist");
    }

    #[test]
    fn test_clean_context_for_reviewer_with_workspace_non_isolation() {
        let workspace = MemoryWorkspace::new_test()
            .with_dir(".agent")
            .with_file(".agent/STATUS.md", "old status")
            .with_file(".agent/NOTES.md", "old notes")
            .with_file(".agent/ISSUES.md", "old issues")
            .with_dir(".agent/archive")
            .with_file(".agent/archive/old.txt", "archived");

        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);

        // Non-isolation mode should overwrite context files with vague content
        clean_context_for_reviewer_with_workspace(&workspace, &logger, false).unwrap();

        // Files should be overwritten with vague one-liners
        assert_eq!(
            workspace.read(Path::new(".agent/STATUS.md")).unwrap(),
            "In progress.\n"
        );
        assert_eq!(
            workspace.read(Path::new(".agent/NOTES.md")).unwrap(),
            "Notes.\n"
        );
        assert_eq!(
            workspace.read(Path::new(".agent/ISSUES.md")).unwrap(),
            "No issues recorded.\n"
        );
        // Archive directory should be removed
        assert!(
            !workspace.exists(Path::new(".agent/archive")),
            "Archive should be removed"
        );
    }

    #[test]
    fn test_clean_context_for_reviewer_with_workspace_isolation_mode() {
        let workspace = MemoryWorkspace::new_test().with_dir(".agent");

        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);

        // Isolation mode should do nothing
        clean_context_for_reviewer_with_workspace(&workspace, &logger, true).unwrap();

        // No files should be created
        assert!(
            !workspace.exists(Path::new(".agent/STATUS.md")),
            "STATUS.md should not be created in isolation mode"
        );
    }

    #[test]
    fn test_update_status_with_workspace_non_isolation() {
        let workspace = MemoryWorkspace::new_test().with_dir(".agent");

        // Non-isolation mode should write vague status
        update_status_with_workspace(&workspace, "In progress.", false).unwrap();

        let content = workspace.read(Path::new(".agent/STATUS.md")).unwrap();
        assert_eq!(content, "In progress.\n");
    }

    #[test]
    fn test_update_status_with_workspace_isolation_mode() {
        let workspace = MemoryWorkspace::new_test().with_dir(".agent");

        // Isolation mode should do nothing
        update_status_with_workspace(&workspace, "In progress.", true).unwrap();

        // STATUS.md should NOT be created
        assert!(
            !workspace.exists(Path::new(".agent/STATUS.md")),
            "STATUS.md should not be created in isolation mode"
        );
    }
}
