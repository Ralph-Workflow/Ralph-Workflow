use std::path::Path;

use crate::files::context::{VAGUE_ISSUES_LINE, VAGUE_NOTES_LINE, VAGUE_STATUS_LINE};
use crate::files::integrity;
use crate::files::recovery;
use crate::workspace::Workspace;

// Direct filesystem operations in boundary module (io.rs stem → exempt from forbid_io_effects).
include!("agent_files/io.rs");

/// XSD schemas for XML validation - included at compile time.
/// These are written to `.agent/xsd/` at pipeline start for agent self-validation.
const PLAN_XSD_SCHEMA: &str = include_str!("llm_output_extraction/plan.xsd");
const DEVELOPMENT_RESULT_XSD_SCHEMA: &str =
    include_str!("llm_output_extraction/development_result.xsd");
const ISSUES_XSD_SCHEMA: &str = include_str!("llm_output_extraction/issues.xsd");
const FIX_RESULT_XSD_SCHEMA: &str = include_str!("llm_output_extraction/fix_result.xsd");
const COMMIT_MESSAGE_XSD_SCHEMA: &str = include_str!("llm_output_extraction/commit_message.xsd");

/// Files that Ralph generates during a run and should clean up.
pub const GENERATED_FILES: &[&str] = &[
    ".agent/PLAN.md",
    ".agent/commit-message.txt",
    ".agent/checkpoint.json.tmp",
    ".git/ralph/no_agent_commit",
];

/// Ensure required files and directories exist using workspace.
///
/// Creates the `.agent/logs` and `.agent/tmp` directories if they don't exist.
/// Also writes XSD schemas to `.agent/tmp/` for agent self-validation.
///
/// When `isolation_mode` is true (the default), STATUS.md, NOTES.md and ISSUES.md
/// are NOT created. This prevents context contamination from previous runs.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn ensure_files_with_workspace(
    workspace: &dyn Workspace,
    isolation_mode: bool,
) -> std::io::Result<()> {
    let agent_dir = Path::new(".agent");

    // Best-effort state repair before we start touching `.agent/` contents.
    // If the state is unrecoverable, fail early with a clear error.
    if let recovery::RecoveryStatus::Unrecoverable(msg) =
        recovery::auto_repair_with_workspace(workspace, agent_dir)?
    {
        return Err(std::io::Error::other(format!(
            "Failed to repair .agent state: {msg}"
        )));
    }

    integrity::check_filesystem_ready_with_workspace(workspace, agent_dir)?;
    workspace.create_dir_all(&agent_dir.join("logs"))?;
    workspace.create_dir_all(&agent_dir.join("tmp"))?;

    // Clean up any stale XML files from previous runs that might be locked
    let tmp_dir = agent_dir.join("tmp");
    let _ = integrity::cleanup_stale_xml_files_with_workspace(workspace, &tmp_dir, false);
    // Note: cleanup is best-effort, failures are not fatal

    // Write XSD schemas to .agent/tmp/ for agent self-validation
    setup_xsd_schemas_with_workspace(workspace)?;

    // Only create STATUS.md, NOTES.md and ISSUES.md when NOT in isolation mode
    if !isolation_mode {
        // Always overwrite/truncate these files to a single vague sentence to
        // avoid detailed context persisting across runs.
        workspace.write_atomic(
            &agent_dir.join("STATUS.md"),
            &format!("{VAGUE_STATUS_LINE}\n"),
        )?;
        workspace.write_atomic(
            &agent_dir.join("NOTES.md"),
            &format!("{VAGUE_NOTES_LINE}\n"),
        )?;
        workspace.write_atomic(
            &agent_dir.join("ISSUES.md"),
            &format!("{VAGUE_ISSUES_LINE}\n"),
        )?;
    }

    Ok(())
}

// ============================================================================
// Workspace-based functions (for testability with MemoryWorkspace)
// ============================================================================

/// Check if a file contains a specific marker string using the Workspace trait.
///
/// This is the workspace-based version of `file_contains_marker`.
///
/// # Arguments
///
/// * `workspace` - The workspace for file operations
/// * `path` - Relative path within the workspace
/// * `marker` - String to search for
///
/// Returns `Ok(true)` if the marker is found, `Ok(false)` if not found or file doesn't exist.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn file_contains_marker_with_workspace(
    workspace: &dyn Workspace,
    path: &Path,
    marker: &str,
) -> std::io::Result<bool> {
    if !workspace.exists(path) {
        return Ok(false);
    }

    let content = workspace.read(path)?;
    Ok(content.lines().any(|line| line.contains(marker)))
}

/// Delete the PLAN.md file using the workspace.
///
/// This is the workspace-based version of `delete_plan_file_at`.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn delete_plan_file_with_workspace(workspace: &dyn Workspace) -> std::io::Result<()> {
    let plan_path = Path::new(".agent/PLAN.md");
    if workspace.exists(plan_path) {
        workspace.remove(plan_path)?;
    }
    Ok(())
}

/// Delete the commit-message.txt file using the workspace.
///
/// This is the workspace-based version of `delete_commit_message_file_at`.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn delete_commit_message_file_with_workspace(workspace: &dyn Workspace) -> std::io::Result<()> {
    let msg_path = Path::new(".agent/commit-message.txt");
    if workspace.exists(msg_path) {
        workspace.remove(msg_path)?;
    }
    Ok(())
}

/// Read commit message from file using the workspace.
///
/// This is the workspace-based version of `read_commit_message_file_at`.
///
/// # Errors
///
/// Returns an error if the file doesn't exist, cannot be read, or is empty.
pub fn read_commit_message_file_with_workspace(
    workspace: &dyn Workspace,
) -> std::io::Result<String> {
    let msg_path = Path::new(".agent/commit-message.txt");

    if workspace.exists(msg_path) {
        // Use workspace-based verification
        if !super::integrity::verify_file_not_corrupted_with_workspace(workspace, msg_path)? {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                ".agent/commit-message.txt appears corrupted",
            ));
        }
    }

    let content = workspace.read(msg_path).map_err(|e| {
        std::io::Error::new(
            e.kind(),
            format!("Failed to read .agent/commit-message.txt: {e}"),
        )
    })?;

    let trimmed = content.trim();
    if trimmed.is_empty() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            ".agent/commit-message.txt is empty",
        ));
    }
    Ok(trimmed.to_string())
}

/// Write commit message to file using the workspace.
///
/// Uses atomic write to ensure the file is either fully written or not written
/// at all, preventing partial writes on crash/interruption.
///
/// This is the workspace-based version of `write_commit_message_file_at`.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn write_commit_message_file_with_workspace(
    workspace: &dyn Workspace,
    message: &str,
) -> std::io::Result<()> {
    let msg_path = Path::new(".agent/commit-message.txt");
    workspace.write_atomic(msg_path, message)
}

/// Clean up generated files using the workspace.
///
/// This is the workspace-based version of `cleanup_generated_files_at`.
pub fn cleanup_generated_files_with_workspace(workspace: &dyn Workspace) {
    let _ = GENERATED_FILES
        .iter()
        .map(|file| workspace.remove(Path::new(file)))
        .collect::<Vec<_>>();
}

/// Write XSD schemas to .agent/tmp/ using the workspace.
///
/// This is the workspace-based version of `setup_xsd_schemas_at`.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn setup_xsd_schemas_with_workspace(workspace: &dyn Workspace) -> std::io::Result<()> {
    let tmp_dir = Path::new(".agent/tmp");
    workspace.create_dir_all(tmp_dir)?;

    workspace.write(&tmp_dir.join("plan.xsd"), PLAN_XSD_SCHEMA)?;
    workspace.write(
        &tmp_dir.join("development_result.xsd"),
        DEVELOPMENT_RESULT_XSD_SCHEMA,
    )?;
    workspace.write(&tmp_dir.join("issues.xsd"), ISSUES_XSD_SCHEMA)?;
    workspace.write(&tmp_dir.join("fix_result.xsd"), FIX_RESULT_XSD_SCHEMA)?;
    workspace.write(
        &tmp_dir.join("commit_message.xsd"),
        COMMIT_MESSAGE_XSD_SCHEMA,
    )?;

    Ok(())
}

#[cfg(test)]
mod tests {
    // =========================================================================
    // Workspace-based tests (for testability without real filesystem)
    // =========================================================================

    #[cfg(feature = "test-utils")]
    mod workspace_tests {
        use super::super::*;
        use crate::workspace::MemoryWorkspace;

        #[test]
        fn test_file_contains_marker_with_workspace() {
            let workspace =
                MemoryWorkspace::new_test().with_file("test.txt", "line1\nMARKER_TEST\nline3");

            assert!(file_contains_marker_with_workspace(
                &workspace,
                Path::new("test.txt"),
                "MARKER_TEST"
            )
            .unwrap());
            assert!(!file_contains_marker_with_workspace(
                &workspace,
                Path::new("test.txt"),
                "NONEXISTENT"
            )
            .unwrap());
        }

        #[test]
        fn test_file_contains_marker_with_workspace_missing() {
            let workspace = MemoryWorkspace::new_test();

            let result = file_contains_marker_with_workspace(
                &workspace,
                Path::new("nonexistent.txt"),
                "MARKER",
            );
            assert!(!result.unwrap());
        }

        #[test]
        fn test_delete_plan_file_with_workspace() {
            let workspace = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "test plan");

            assert!(workspace.exists(Path::new(".agent/PLAN.md")));

            delete_plan_file_with_workspace(&workspace).unwrap();

            assert!(!workspace.exists(Path::new(".agent/PLAN.md")));
        }

        #[test]
        fn test_delete_plan_file_with_workspace_nonexistent() {
            let workspace = MemoryWorkspace::new_test();

            // Should succeed even if file doesn't exist
            delete_plan_file_with_workspace(&workspace).unwrap();
        }

        #[test]
        fn test_read_commit_message_file_with_workspace() {
            let workspace = MemoryWorkspace::new_test()
                .with_file(".agent/commit-message.txt", "feat: test commit\n");

            let msg = read_commit_message_file_with_workspace(&workspace).unwrap();
            assert_eq!(msg, "feat: test commit");
        }

        #[test]
        fn test_read_commit_message_file_with_workspace_empty() {
            let workspace =
                MemoryWorkspace::new_test().with_file(".agent/commit-message.txt", "   \n");

            assert!(read_commit_message_file_with_workspace(&workspace).is_err());
        }

        #[test]
        fn test_write_commit_message_file_with_workspace() {
            let workspace = MemoryWorkspace::new_test();

            write_commit_message_file_with_workspace(&workspace, "feat: new feature").unwrap();

            assert!(workspace.exists(Path::new(".agent/commit-message.txt")));
            let content = workspace
                .read(Path::new(".agent/commit-message.txt"))
                .unwrap();
            assert_eq!(content, "feat: new feature");
        }

        #[test]
        fn test_delete_commit_message_file_with_workspace() {
            let workspace =
                MemoryWorkspace::new_test().with_file(".agent/commit-message.txt", "test message");

            assert!(workspace.exists(Path::new(".agent/commit-message.txt")));

            delete_commit_message_file_with_workspace(&workspace).unwrap();

            assert!(!workspace.exists(Path::new(".agent/commit-message.txt")));
        }

        #[test]
        fn test_cleanup_generated_files_with_workspace() {
            let workspace = MemoryWorkspace::new_test()
                .with_file(".agent/PLAN.md", "plan")
                .with_file(".agent/commit-message.txt", "msg")
                .with_file(".git/ralph/no_agent_commit", "");

            cleanup_generated_files_with_workspace(&workspace);

            assert!(!workspace.exists(Path::new(".agent/PLAN.md")));
            assert!(!workspace.exists(Path::new(".agent/commit-message.txt")));
            assert!(!workspace.exists(Path::new(".git/ralph/no_agent_commit")));
        }

        #[test]
        fn test_setup_xsd_schemas_with_workspace() {
            let workspace = MemoryWorkspace::new_test();

            setup_xsd_schemas_with_workspace(&workspace).unwrap();

            // Verify all schemas are written
            assert!(workspace.exists(Path::new(".agent/tmp/plan.xsd")));
            assert!(workspace.exists(Path::new(".agent/tmp/development_result.xsd")));
            assert!(workspace.exists(Path::new(".agent/tmp/issues.xsd")));
            assert!(workspace.exists(Path::new(".agent/tmp/fix_result.xsd")));
            assert!(workspace.exists(Path::new(".agent/tmp/commit_message.xsd")));

            // Verify content
            let plan_xsd = workspace.read(Path::new(".agent/tmp/plan.xsd")).unwrap();
            assert!(plan_xsd.contains("xs:schema"));
            assert!(plan_xsd.contains("ralph-plan"));
        }
    }
}
