//! Backup management for PROMPT.md.
//!
//! This module handles creation and rotation of PROMPT.md backups to protect
//! against accidental deletion or modification.

use std::io;
use std::path::Path;

use crate::workspace::Workspace;

pub fn create_prompt_backup_with_workspace(
    workspace: &dyn Workspace,
) -> io::Result<Option<String>> {
    let prompt_path = Path::new("PROMPT.md");

    if !workspace.exists(prompt_path) {
        return Ok(None);
    }

    let agent_dir = Path::new(".agent");
    let backup_base = Path::new(".agent/PROMPT.md.backup");
    let backup_1 = Path::new(".agent/PROMPT.md.backup.1");
    let backup_2 = Path::new(".agent/PROMPT.md.backup.2");

    workspace.create_dir_all(agent_dir)?;

    let content = workspace.read(prompt_path).map_err(|e| {
        io::Error::new(
            e.kind(),
            format!("Failed to read PROMPT.md for backup: {e}"),
        )
    })?;

    let _ = workspace.remove_if_exists(backup_2);

    if workspace.exists(backup_1) {
        let _ = workspace.rename(backup_1, backup_2);
    }

    if workspace.exists(backup_base) {
        let _ = workspace.rename(backup_base, backup_1);
    }

    workspace
        .write_atomic(backup_base, &content)
        .map_err(|e| io::Error::new(e.kind(), format!("Failed to write PROMPT.md backup: {e}")))?;

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

pub fn make_prompt_read_only_with_workspace(workspace: &dyn Workspace) -> Option<String> {
    let prompt_path = Path::new("PROMPT.md");

    if !workspace.exists(prompt_path) {
        return None;
    }

    match workspace.set_readonly(prompt_path) {
        Ok(()) => None,
        Err(e) => Some(format!(
            "Failed to set read-only permissions on PROMPT.md: {e}"
        )),
    }
}

pub fn make_prompt_writable_with_workspace(workspace: &dyn Workspace) -> Option<String> {
    let prompt_path = Path::new("PROMPT.md");

    if !workspace.exists(prompt_path) {
        return None;
    }

    match workspace.set_writable(prompt_path) {
        Ok(()) => None,
        Err(e) => Some(format!("Failed to set write permissions on PROMPT.md: {e}")),
    }
}

const DIFF_BACKUP_PATH: &str = ".agent/DIFF.backup";

pub fn write_diff_backup_with_workspace(
    workspace: &dyn Workspace,
    diff_content: &str,
) -> io::Result<std::path::PathBuf> {
    let backup_path = Path::new(DIFF_BACKUP_PATH);

    workspace.create_dir_all(Path::new(".agent"))?;

    workspace.write(backup_path, diff_content)?;

    Ok(backup_path.to_path_buf())
}

#[cfg(all(test, feature = "test-utils"))]
mod tests {
    use super::*;
    use crate::workspace::{MemoryWorkspace, Workspace};

    #[test]
    fn test_create_prompt_backup_with_workspace_creates_file() {
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "# Test Content\n");

        let result = create_prompt_backup_with_workspace(&workspace);
        assert!(result.is_ok());

        assert!(workspace.exists(Path::new(".agent/PROMPT.md.backup")));
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup").unwrap(),
            "# Test Content\n"
        );
    }

    #[test]
    fn test_create_prompt_backup_with_workspace_missing_prompt() {
        let workspace = MemoryWorkspace::new_test();

        let result = create_prompt_backup_with_workspace(&workspace);
        assert!(result.is_ok());
        assert!(result.unwrap().is_none());

        assert!(!workspace.exists(Path::new(".agent/PROMPT.md.backup")));
    }

    #[test]
    fn test_create_prompt_backup_with_workspace_rotation() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("PROMPT.md", "# Version 1\n")
            .with_dir(".agent");

        create_prompt_backup_with_workspace(&workspace).unwrap();
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup").unwrap(),
            "# Version 1\n"
        );

        workspace
            .write(Path::new("PROMPT.md"), "# Version 2\n")
            .unwrap();
        create_prompt_backup_with_workspace(&workspace).unwrap();

        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup").unwrap(),
            "# Version 2\n"
        );
        assert_eq!(
            workspace.get_file(".agent/PROMPT.md.backup.1").unwrap(),
            "# Version 1\n"
        );

        workspace
            .write(Path::new("PROMPT.md"), "# Version 3\n")
            .unwrap();
        create_prompt_backup_with_workspace(&workspace).unwrap();

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

        for i in 1..=4 {
            workspace
                .write(Path::new("PROMPT.md"), &format!("# Version {i}\n"))
                .unwrap();
            create_prompt_backup_with_workspace(&workspace).unwrap();
        }

        assert!(workspace.exists(Path::new(".agent/PROMPT.md.backup")));
        assert!(workspace.exists(Path::new(".agent/PROMPT.md.backup.1")));
        assert!(workspace.exists(Path::new(".agent/PROMPT.md.backup.2")));

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

        let result = make_prompt_read_only_with_workspace(&workspace);
        assert!(result.is_none());
    }

    #[test]
    fn test_make_prompt_read_only_with_workspace_missing() {
        let workspace = MemoryWorkspace::new_test();

        let result = make_prompt_read_only_with_workspace(&workspace);
        assert!(result.is_none());
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

        let diff = "some diff content";
        let result = write_diff_backup_with_workspace(&workspace, diff);
        assert!(result.is_ok());

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

        assert_eq!(workspace.get_file(".agent/DIFF.backup").unwrap(), new_diff);
    }
}
