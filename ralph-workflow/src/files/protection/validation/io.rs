// Boundary module: direct filesystem I/O for PROMPT.md validation.
// File named io.rs — recognized as boundary module by forbid_io_effects lint.
// Contains functions that must access the OS filesystem directly (not via Workspace trait).

use std::fs;

/// Restore PROMPT.md from backup if missing or empty.
///
/// This is a lightweight periodic check called during pipeline execution
/// to detect and recover from accidental PROMPT.md deletion by agents.
/// Unlike `validate_prompt_md_with_workspace()`, this function only checks for file
/// existence and non-empty content - it doesn't validate structure.
///
/// # Auto-Restore
///
/// If PROMPT.md is missing or empty but a backup exists, the backup is
/// automatically copied to PROMPT.md. Tries backups in order:
/// - `.agent/PROMPT.md.backup`
/// - `.agent/PROMPT.md.backup.1`
/// - `.agent/PROMPT.md.backup.2`
///
/// # Returns
///
/// Restores the PROMPT.md file from backup if it's missing or empty.
///
/// # Errors
///
/// Returns an error if the prompt file is missing/empty and no valid backup is available.
pub fn restore_prompt_if_needed() -> anyhow::Result<bool> {
    let prompt_path = Path::new("PROMPT.md");
    if is_prompt_present(prompt_path) {
        return Ok(true);
    }
    if try_restore_backups(prompt_path) {
        return Ok(false);
    }
    anyhow::bail!(
        "PROMPT.md is missing/empty and no valid backup available (tried .agent/PROMPT.md.backup, .agent/PROMPT.md.backup.1, .agent/PROMPT.md.backup.2)"
    );
}

/// Check if PROMPT.md exists and has non-empty content.
fn is_prompt_present(prompt_path: &Path) -> bool {
    prompt_path
        .exists()
        .then(|| fs::read_to_string(prompt_path).ok())
        .flatten()
        .is_some_and(|s| !s.trim().is_empty())
}

/// Try to restore PROMPT.md from the backup chain; return true on success.
fn try_restore_backups(prompt_path: &Path) -> bool {
    let backup_paths = [
        Path::new(".agent/PROMPT.md.backup"),
        Path::new(".agent/PROMPT.md.backup.1"),
        Path::new(".agent/PROMPT.md.backup.2"),
    ];

    backup_paths
        .iter()
        .filter(|backup_path| backup_path.exists())
        .find_map(|backup_path| {
            let backup_content = fs::read_to_string(backup_path).ok()?;
            if backup_content.trim().is_empty() {
                return None;
            }
            fs::write(prompt_path, backup_content).ok()?;
            set_prompt_readonly_permissions(prompt_path);
            Some(())
        })
        .is_some()
}

fn set_prompt_readonly_permissions(prompt_path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(prompt_path, fs::Permissions::from_mode(0o444));
    }

    #[cfg(windows)]
    {
        use std::os::windows::fs::{PermissionsExt, FILE_ATTRIBUTE_READONLY};
        let _ = fs::set_permissions(
            prompt_path,
            fs::Permissions::from_attributes(FILE_ATTRIBUTE_READONLY),
        );
    }
}

/// Validate PROMPT.md structure and content.
///
/// Checks for:
/// - File existence and non-empty content (auto-restores from backup if missing)
/// - Goal section (## Goal or # Goal)
/// - Acceptance section (## Acceptance, Acceptance Criteria, or acceptance)
///
/// Uses a `WorkspaceFs` rooted at the current directory for all file operations.
///
/// # Auto-Restore
///
/// If PROMPT.md is missing but `.agent/PROMPT.md.backup` exists, the backup is
/// automatically copied to PROMPT.md. This prevents accidental deletion by agents.
///
/// # Arguments
///
/// * `strict` - In strict mode, missing sections are errors; otherwise they're warnings.
/// * `interactive` - If true and PROMPT.md doesn't exist, prompt to create from template.
///   Also requires stdout to be a terminal for interactive prompts.
///
/// # Returns
///
/// A `PromptValidationResult` containing validation findings.
#[must_use]
pub fn validate_prompt_md(strict: bool, interactive: bool) -> PromptValidationResult {
    let root = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    let workspace = WorkspaceFs::new(root);
    validate_prompt_md_with_workspace(&workspace, strict, interactive)
}
