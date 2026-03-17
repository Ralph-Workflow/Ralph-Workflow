// Imports and helper functions for PROMPT.md validation.

use crate::workspace::{Workspace, WorkspaceFs};
use std::fs;
use std::io::IsTerminal;
use std::path::Path;

pub(super) fn contains_ascii_case_insensitive(haystack: &str, needle: &str) -> bool {
    if needle.is_empty() {
        return true;
    }
    if needle.len() > haystack.len() {
        return false;
    }

    let needle = needle.as_bytes();
    haystack.as_bytes().windows(needle.len()).any(|window| {
        window
            .iter()
            .zip(needle.iter())
            .all(|(a, b)| a.eq_ignore_ascii_case(b))
    })
}

/// File existence state for PROMPT.md validation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileState {
    /// File does not exist
    Missing,
    /// File exists but is empty
    Empty,
    /// File exists with content
    Present,
}

/// Result of PROMPT.md validation.
///
/// Contains flags indicating what was found and any errors or warnings.
#[derive(Debug, Clone)]
// Each boolean represents a distinct aspect of PROMPT.md validation.
// These are independent flags tracking different validation dimensions, not
// a state machine, so bools are the appropriate type.
pub struct PromptValidationResult {
    /// File existence and content state
    pub file_state: FileState,
    /// Whether a Goal section was found
    pub has_goal: bool,
    /// Whether an Acceptance section was found
    pub has_acceptance: bool,
    /// List of warnings (non-blocking issues)
    pub warnings: Vec<String>,
    /// List of errors (blocking issues)
    pub errors: Vec<String>,
}

impl PromptValidationResult {
    /// Returns true if PROMPT.md exists.
    #[must_use]
    pub const fn exists(&self) -> bool {
        matches!(self.file_state, FileState::Present | FileState::Empty)
    }

    /// Returns true if PROMPT.md has non-empty content.
    #[must_use]
    pub const fn has_content(&self) -> bool {
        matches!(self.file_state, FileState::Present)
    }
}

impl PromptValidationResult {
    /// Returns true if validation passed (no errors).
    #[must_use]
    pub const fn is_valid(&self) -> bool {
        self.errors.is_empty()
    }

    /// Returns true if validation passed with no warnings.
    #[must_use]
    pub const fn is_perfect(&self) -> bool {
        self.errors.is_empty() && self.warnings.is_empty()
    }
}

/// Restore PROMPT.md from backup if missing or empty.
///
/// This is a lightweight periodic check called during pipeline execution
/// to detect and recover from accidental PROMPT.md deletion by agents.
/// Unlike `validate_prompt_md()`, this function only checks for file
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

    // Check if PROMPT.md exists and has content
    let prompt_ok = prompt_path
        .exists()
        .then(|| fs::read_to_string(prompt_path).ok())
        .flatten()
        .is_some_and(|s| !s.trim().is_empty());

    if prompt_ok {
        return Ok(true);
    }

    // PROMPT.md is missing or empty - try to restore from backup chain
    let backup_paths = [
        Path::new(".agent/PROMPT.md.backup"),
        Path::new(".agent/PROMPT.md.backup.1"),
        Path::new(".agent/PROMPT.md.backup.2"),
    ];

    for backup_path in &backup_paths {
        if backup_path.exists() {
            // Verify backup has content
            let Ok(backup_content) = fs::read_to_string(backup_path) else {
                continue;
            };

            if backup_content.trim().is_empty() {
                continue; // Try next backup
            }

            // Restore from backup
            fs::write(prompt_path, backup_content)?;

            // Set read-only permissions on restored file (best-effort)
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if let Ok(metadata) = fs::metadata(prompt_path) {
                    let mut perms = metadata.permissions();
                    perms.set_mode(0o444);
                    let _ = fs::set_permissions(prompt_path, perms);
                }
            }

            #[cfg(windows)]
            {
                if let Ok(metadata) = fs::metadata(prompt_path) {
                    let mut perms = metadata.permissions();
                    perms.set_readonly(true);
                    let _ = fs::set_permissions(prompt_path, perms);
                }
            }

            return Ok(false);
        }
    }

    // No valid backup available
    anyhow::bail!(
        "PROMPT.md is missing/empty and no valid backup available (tried .agent/PROMPT.md.backup, .agent/PROMPT.md.backup.1, .agent/PROMPT.md.backup.2)"
    );
}

/// Check content for Goal section.
pub(super) fn check_goal_section(content: &str) -> bool {
    content.contains("## Goal") || content.contains("# Goal")
}

/// Check content for Acceptance section.
pub(super) fn check_acceptance_section(content: &str) -> bool {
    content.contains("## Acceptance")
        || content.contains("# Acceptance")
        || content.contains("Acceptance Criteria")
        || contains_ascii_case_insensitive(content, "acceptance")
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

/// Validate PROMPT.md structure and content using workspace abstraction.
///
/// This is the workspace-aware version of [`validate_prompt_md`] for testability.
/// Uses the provided workspace for all file operations instead of `std::fs`.
///
/// # Arguments
///
/// * `workspace` - The workspace for file operations
/// * `strict` - In strict mode, missing sections are errors; otherwise they're warnings.
/// * `interactive` - If true and PROMPT.md doesn't exist, prompt to create from template.
///
/// # Returns
///
/// A `PromptValidationResult` containing validation findings.
pub fn validate_prompt_md_with_workspace(
    workspace: &dyn Workspace,
    strict: bool,
    interactive: bool,
) -> PromptValidationResult {
    let prompt_path = Path::new("PROMPT.md");
    let file_exists = workspace.exists(prompt_path);
    let mut result = PromptValidationResult {
        file_state: if file_exists {
            FileState::Empty
        } else {
            FileState::Missing
        },
        has_goal: false,
        has_acceptance: false,
        warnings: Vec::new(),
        errors: Vec::new(),
    };

    if !result.exists() {
        // Try to restore from backup
        if let Some(source) = try_restore_from_backup_with_workspace(workspace, prompt_path) {
            result.file_state = FileState::Empty;
            result.warnings.push(format!(
                "PROMPT.md was missing and was automatically restored from {source}"
            ));
        } else {
            // No backup available
            if interactive && std::io::stdout().is_terminal() {
                result.errors.push(
                    "PROMPT.md not found. Use 'ralph --init <template>' to create one.".to_string(),
                );
            } else {
                result.errors.push(
                    "PROMPT.md not found. Run 'ralph --list-work-guides' to see available Work Guides, \
                     then 'ralph --init <template>' to create one."
                        .to_string(),
                );
            }
            return result;
        }
    }

    let content = match workspace.read(prompt_path) {
        Ok(c) => c,
        Err(e) => {
            result.errors.push(format!("Failed to read PROMPT.md: {e}"));
            return result;
        }
    };

    result.file_state = if content.trim().is_empty() {
        FileState::Empty
    } else {
        FileState::Present
    };

    if !result.has_content() {
        result.errors.push("PROMPT.md is empty".to_string());
        return result;
    }

    // Check for Goal section
    result.has_goal = check_goal_section(&content);
    if !result.has_goal {
        let msg = "PROMPT.md missing '## Goal' section".to_string();
        if strict {
            result.errors.push(msg);
        } else {
            result.warnings.push(msg);
        }
    }

    // Check for Acceptance section
    result.has_acceptance = check_acceptance_section(&content);
    if !result.has_acceptance {
        let msg = "PROMPT.md missing acceptance checks section".to_string();
        if strict {
            result.errors.push(msg);
        } else {
            result.warnings.push(msg);
        }
    }

    result
}

/// Attempt to restore PROMPT.md from backup files using workspace.
fn try_restore_from_backup_with_workspace(
    workspace: &dyn Workspace,
    prompt_path: &Path,
) -> Option<String> {
    let backup_paths = [
        (
            Path::new(".agent/PROMPT.md.backup"),
            ".agent/PROMPT.md.backup",
        ),
        (
            Path::new(".agent/PROMPT.md.backup.1"),
            ".agent/PROMPT.md.backup.1",
        ),
        (
            Path::new(".agent/PROMPT.md.backup.2"),
            ".agent/PROMPT.md.backup.2",
        ),
    ];

    for (backup_path, name) in backup_paths {
        if workspace.exists(backup_path) {
            let Ok(backup_content) = workspace.read(backup_path) else {
                continue;
            };

            if backup_content.trim().is_empty() {
                continue;
            }

            if workspace.write(prompt_path, &backup_content).is_ok() {
                return Some(name.to_string());
            }
        }
    }

    None
}
