//! Backup management for PROMPT.md.
//!
//! This module handles creation and rotation of PROMPT.md backups to protect
//! against accidental deletion or modification.

use std::path::Path;

use crate::workspace::Workspace;

// ============================================================================
// Workspace-based backup functions
// ============================================================================

/// Create a backup of PROMPT.md using the Workspace abstraction.
///
/// This function mirrors `create_prompt_backup_at` but uses the `Workspace` trait
/// for all file operations, allowing tests to use `MemoryWorkspace` instead of
/// real filesystem access.
///
/// With backup rotation enabled, this maintains up to 3 backup versions:
/// `.agent/PROMPT.md.backup`, `.agent/PROMPT.md.backup.1`, and `.agent/PROMPT.md.backup.2`.
///
/// # Behavior
///
/// - If PROMPT.md doesn't exist, returns `Ok(None)` (nothing to backup)
/// - Creates the `.agent` directory if it doesn't exist
/// - Rotates existing backups: backup.2 → deleted, backup.1 → backup.2, backup → backup.1
/// - Sets all backup files to read-only (best-effort; failures don't error)
/// - Returns a warning string in the Ok variant if read-only setting fails
///
/// # Returns
///
/// Returns `io::Result<Option<String>>` where:
/// - `Ok(None)` - backup created and read-only set successfully
/// - `Ok(Some(warning))` - backup created but read-only couldn't be set
/// - `Err(e)` - if the backup cannot be created
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn create_prompt_backup_with_workspace(
    workspace: &dyn Workspace,
) -> std::io::Result<Option<String>> {
    let prompt_path = Path::new("PROMPT.md");

    // If PROMPT.md doesn't exist, that's fine - nothing to backup
    if !workspace.exists(prompt_path) {
        return Ok(None);
    }

    // Ensure .agent directory exists
    let agent_dir = Path::new(".agent");
    let backup_base = Path::new(".agent/PROMPT.md.backup");
    let backup_1 = Path::new(".agent/PROMPT.md.backup.1");
    let backup_2 = Path::new(".agent/PROMPT.md.backup.2");

    workspace.create_dir_all(agent_dir)?;

    // Read PROMPT.md content
    let content = workspace.read(prompt_path).map_err(|e| {
        std::io::Error::new(
            e.kind(),
            format!("Failed to read PROMPT.md for backup: {e}"),
        )
    })?;

    // Backup rotation: .2 → deleted, .1 → .2, .backup → .1
    // Delete oldest backup if it exists
    let _ = workspace.remove_if_exists(backup_2);

    // Rotate .1 → .2
    if workspace.exists(backup_1) {
        let _ = workspace.rename(backup_1, backup_2);
    }

    // Rotate .backup → .1
    if workspace.exists(backup_base) {
        let _ = workspace.rename(backup_base, backup_1);
    }

    // Write new backup atomically to prevent corruption
    workspace.write_atomic(backup_base, &content).map_err(|e| {
        std::io::Error::new(e.kind(), format!("Failed to write PROMPT.md backup: {e}"))
    })?;

    // Set read-only permissions on all backups (best-effort)
    let readonly_warning = [backup_base, backup_1, backup_2]
        .iter()
        .filter(|backup_path| workspace.exists(backup_path))
        .find_map(|backup_path| {
            workspace
                .set_readonly(backup_path)
                .err()
                .map(|e| e.to_string())
        });

    Ok(readonly_warning)
}

/// Make PROMPT.md read-only using the Workspace abstraction.
///
/// This function mirrors `make_prompt_read_only_at` but uses the `Workspace` trait
/// for all file operations.
///
/// # Returns
///
/// Returns `Option<String>` where:
/// - `None` - permissions set successfully or file doesn't exist
/// - `Some(warning)` - couldn't set read-only permissions
pub fn make_prompt_read_only_with_workspace(workspace: &dyn Workspace) -> Option<String> {
    let prompt_path = Path::new("PROMPT.md");

    // If PROMPT.md doesn't exist, that's fine - nothing to protect
    if !workspace.exists(prompt_path) {
        return None;
    }

    // Try to set read-only permissions
    match workspace.set_readonly(prompt_path) {
        Ok(()) => None,
        Err(e) => Some(format!(
            "Failed to set read-only permissions on PROMPT.md: {e}"
        )),
    }
}

/// Make PROMPT.md writable again using the Workspace abstraction.
///
/// This function mirrors `make_prompt_writable_at` but uses the `Workspace` trait
/// for all file operations.
///
/// # Returns
///
/// Returns `Option<String>` where:
/// - `None` - permissions restored successfully or file doesn't exist
/// - `Some(warning)` - couldn't restore write permissions
pub fn make_prompt_writable_with_workspace(workspace: &dyn Workspace) -> Option<String> {
    let prompt_path = Path::new("PROMPT.md");

    // If PROMPT.md doesn't exist, that's fine - nothing to modify
    if !workspace.exists(prompt_path) {
        return None;
    }

    // Try to restore write permissions
    match workspace.set_writable(prompt_path) {
        Ok(()) => None,
        Err(e) => Some(format!("Failed to set write permissions on PROMPT.md: {e}")),
    }
}

// ============================================================================
// Diff backup functions for oversized content
// ============================================================================

/// Path for diff backup file.
const DIFF_BACKUP_PATH: &str = ".agent/DIFF.backup";

/// Write oversized diff content to a backup file.
///
/// When a diff exceeds the inline size limit, this function writes it
/// to `.agent/DIFF.backup` so agents can read it if needed.
///
/// # Arguments
///
/// * `workspace` - Workspace for file operations
/// * `diff_content` - The diff content to write
///
/// # Returns
///
/// Returns `Ok(PathBuf)` with the backup path on success, or an error.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn write_diff_backup_with_workspace(
    workspace: &dyn Workspace,
    diff_content: &str,
) -> std::io::Result<std::path::PathBuf> {
    let backup_path = Path::new(DIFF_BACKUP_PATH);

    // Ensure .agent directory exists
    workspace.create_dir_all(Path::new(".agent"))?;

    // Write the diff content
    workspace.write(backup_path, diff_content)?;

    Ok(backup_path.to_path_buf())
}

// Note: Old tests using with_temp_cwd have been removed since production
// code now uses workspace-based functions (_with_workspace variants).
// The non-workspace functions have been removed. See workspace_tests module
// below for the active tests covering current behavior.

/// Tests for workspace-based backup functions
#[cfg(all(test, feature = "test-utils"))]
mod workspace_tests {
    use super::*;
    use crate::workspace::{MemoryWorkspace, Workspace};

    #[test]
    fn test_create_prompt_backup_with_workspace_creates_file() {
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "# Test Content\n");

        let result = create_prompt_backup_with_workspace(&workspace);
        assert!(result.is_ok());

        // Backup should exist with same content
        assert!(workspace.exists(Path::new(".agent/PROMPT.md.backup")));
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup").unwrap(),
            "# Test Content\n"
        );
    }

    #[test]
    fn test_create_prompt_backup_with_workspace_missing_prompt() {
        let workspace = MemoryWorkspace::new_test();
        // No PROMPT.md exists

        let result = create_prompt_backup_with_workspace(&workspace);
        assert!(result.is_ok());
        assert!(result.unwrap().is_none()); // No warning

        // No backup should be created
        assert!(!workspace.exists(Path::new(".agent/PROMPT.md.backup")));
    }

    #[test]
    fn test_create_prompt_backup_with_workspace_rotation() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("PROMPT.md", "# Version 1\n")
            .with_dir(".agent");

        // First backup
        create_prompt_backup_with_workspace(&workspace).unwrap();
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup").unwrap(),
            "# Version 1\n"
        );

        // Update PROMPT.md and create second backup
        workspace
            .write(Path::new("PROMPT.md"), "# Version 2\n")
            .unwrap();
        create_prompt_backup_with_workspace(&workspace).unwrap();

        // Check rotation: .backup has v2, .backup.1 has v1
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup").unwrap(),
            "# Version 2\n"
        );
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup.1").unwrap(),
            "# Version 1\n"
        );

        // Third backup
        workspace
            .write(Path::new("PROMPT.md"), "# Version 3\n")
            .unwrap();
        create_prompt_backup_with_workspace(&workspace).unwrap();

        // Check: .backup=v3, .backup.1=v2, .backup.2=v1
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup").unwrap(),
            "# Version 3\n"
        );
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup.1").unwrap(),
            "# Version 2\n"
        );
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup.2").unwrap(),
            "# Version 1\n"
        );
    }

    #[test]
    fn test_create_prompt_backup_with_workspace_deletes_oldest() {
        let workspace = MemoryWorkspace::new_test().with_dir(".agent");

        // Create 4 backups - oldest (v1) should be deleted
        for i in 1..=4 {
            workspace
                .write(Path::new("PROMPT.md"), &format!("# Version {i}\n"))
                .unwrap();
            create_prompt_backup_with_workspace(&workspace).unwrap();
        }

        // Only 3 backups should exist
        assert!(workspace.exists(Path::new(".agent/PROMPT.md.backup")));
        assert!(workspace.exists(Path::new(".agent/PROMPT.md.backup.1")));
        assert!(workspace.exists(Path::new(".agent/PROMPT.md.backup.2")));

        // Content: .backup=v4, .backup.1=v3, .backup.2=v2 (v1 deleted)
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup").unwrap(),
            "# Version 4\n"
        );
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup.1").unwrap(),
            "# Version 3\n"
        );
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup.2").unwrap(),
            "# Version 2\n"
        );
    }

    #[test]
    fn test_make_prompt_read_only_with_workspace() {
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "# Test\n");

        // Should succeed (no-op for in-memory workspace, but function exists)
        let result = make_prompt_read_only_with_workspace(&workspace);
        assert!(result.is_none());
    }

    #[test]
    fn test_make_prompt_read_only_with_workspace_missing() {
        let workspace = MemoryWorkspace::new_test();
        // No PROMPT.md

        let result = make_prompt_read_only_with_workspace(&workspace);
        assert!(result.is_none()); // No warning when file doesn't exist
    }

    #[test]
    fn test_make_prompt_writable_with_workspace() {
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "# Test\n");

        let result = make_prompt_writable_with_workspace(&workspace);
        assert!(result.is_none());
    }

    #[test]
    fn test_write_diff_backup_with_workspace() {
        let workspace = MemoryWorkspace::new_test();
        let diff = "+added\n-removed";

        let result = write_diff_backup_with_workspace(&workspace, diff);
        assert!(result.is_ok());

        let path = result.unwrap();
        assert_eq!(path, Path::new(".agent/DIFF.backup"));
        assert_eq!(workspace.get_file(".agent/DIFF.backup").unwrap(), diff);
    }

    #[test]
    fn test_write_diff_backup_creates_agent_dir() {
        let workspace = MemoryWorkspace::new_test();
        // No .agent directory exists

        let diff = "some diff content";
        let result = write_diff_backup_with_workspace(&workspace, diff);
        assert!(result.is_ok());

        // Verify .agent directory was created and file exists
        assert!(workspace.exists(Path::new(".agent")));
        assert!(workspace.exists(Path::new(".agent/DIFF.backup")));
        assert_eq!(workspace.get_file(".agent/DIFF.backup").unwrap(), diff);
    }

    #[test]
    fn test_write_diff_backup_overwrites_existing() {
        let workspace = MemoryWorkspace::new_test().with_file(".agent/DIFF.backup", "old content");

        let new_diff = "new diff content";
        let result = write_diff_backup_with_workspace(&workspace, new_diff);
        assert!(result.is_ok());

        // Should have overwritten the old content
        assert_eq!(workspace.get_file(".agent/DIFF.backup").unwrap(), new_diff);
    }
}
