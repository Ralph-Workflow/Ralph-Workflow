// Imports and helper functions for PROMPT.md validation.

use crate::workspace::{Workspace, WorkspaceFs};
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
    let restored_from = (!file_exists)
        .then(|| try_restore_from_backup_with_workspace(workspace, prompt_path))
        .flatten();

    if !file_exists && restored_from.is_none() {
        let error = if interactive && std::io::IsTerminal::is_terminal(&std::io::stdout()) {
            "PROMPT.md not found. Use 'ralph --init <template>' to create one.".to_string()
        } else {
            "PROMPT.md not found. Run 'ralph --list-work-guides' to see available Work Guides, \
             then 'ralph --init <template>' to create one."
                .to_string()
        };

        return PromptValidationResult {
            file_state: FileState::Missing,
            has_goal: false,
            has_acceptance: false,
            warnings: Vec::new(),
            errors: vec![error],
        };
    }

    let restoration_warnings: Vec<String> = restored_from
        .into_iter()
        .map(|source| format!("PROMPT.md was missing and was automatically restored from {source}"))
        .collect();

    let content = match workspace.read(prompt_path) {
        Ok(c) => c,
        Err(e) => {
            return PromptValidationResult {
                file_state: FileState::Empty,
                has_goal: false,
                has_acceptance: false,
                warnings: restoration_warnings,
                errors: vec![format!("Failed to read PROMPT.md: {e}")],
            };
        }
    };

    let file_state = if content.trim().is_empty() {
        FileState::Empty
    } else {
        FileState::Present
    };

    if matches!(file_state, FileState::Empty) {
        return PromptValidationResult {
            file_state,
            has_goal: false,
            has_acceptance: false,
            warnings: restoration_warnings,
            errors: vec!["PROMPT.md is empty".to_string()],
        };
    }

    let has_goal = check_goal_section(&content);
    let has_acceptance = check_acceptance_section(&content);

    let goal_msg = "PROMPT.md missing '## Goal' section".to_string();
    let acceptance_msg = "PROMPT.md missing acceptance checks section".to_string();

    let warnings = restoration_warnings
        .into_iter()
        .chain((!strict && !has_goal).then_some(goal_msg.clone()))
        .chain((!strict && !has_acceptance).then_some(acceptance_msg.clone()))
        .collect();

    let errors = [
        (strict && !has_goal).then_some(goal_msg),
        (strict && !has_acceptance).then_some(acceptance_msg),
    ]
    .into_iter()
    .flatten()
    .collect();

    PromptValidationResult {
        file_state,
        has_goal,
        has_acceptance,
        warnings,
        errors,
    }
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

    backup_paths.into_iter().find_map(|(backup_path, name)| {
        workspace
            .exists(backup_path)
            .then(|| workspace.read(backup_path).ok())
            .flatten()
            .filter(|backup_content| !backup_content.trim().is_empty())
            .filter(|backup_content| workspace.write(prompt_path, backup_content).is_ok())
            .map(|_| name.to_string())
    })
}
