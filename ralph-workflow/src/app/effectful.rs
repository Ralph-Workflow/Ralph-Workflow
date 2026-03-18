//! Effectful app operations that use `AppEffect` handlers.
//!
//! This module provides functions that execute CLI operations via an
//! [`AppEffectHandler`], enabling testing without real side effects.
//!
//! # Architecture
//!
//! Each function in this module:
//! 1. Takes an `AppEffectHandler` reference
//! 2. Executes effects through the handler
//! 3. Returns strongly-typed results
//!
//! In production, use `RealAppEffectHandler` for actual I/O.
//! In tests, use `MockAppEffectHandler` to verify behavior without side effects.
//!
//! # Example
//!
//! ```ignore
//! use ralph_workflow::app::effectful::handle_reset_start_commit;
//! use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
//!
//! // Test without real git or filesystem
//! let mut handler = MockAppEffectHandler::new()
//!     .with_head_oid("abc123");
//!
//! let result = handle_reset_start_commit(&mut handler, None);
//! assert!(result.is_ok());
//! ```

use super::effect::{AppEffect, AppEffectHandler, AppEffectResult};
use std::path::PathBuf;

/// XSD schemas for XML validation - included at compile time.
const PLAN_XSD_SCHEMA: &str = include_str!("../files/llm_output_extraction/plan.xsd");
const DEVELOPMENT_RESULT_XSD_SCHEMA: &str =
    include_str!("../files/llm_output_extraction/development_result.xsd");
const DEVELOPMENT_CONTINUATION_RESULT_XSD_SCHEMA: &str =
    include_str!("../files/llm_output_extraction/development_continuation_result.xsd");
const ISSUES_XSD_SCHEMA: &str = include_str!("../files/llm_output_extraction/issues.xsd");
const FIX_RESULT_XSD_SCHEMA: &str = include_str!("../files/llm_output_extraction/fix_result.xsd");
const COMMIT_MESSAGE_XSD_SCHEMA: &str =
    include_str!("../files/llm_output_extraction/commit_message.xsd");

// Re-use the canonical vague line constants from context module
use crate::files::io::context::{VAGUE_ISSUES_LINE, VAGUE_NOTES_LINE, VAGUE_STATUS_LINE};

/// Handle the `--reset-start-commit` command using effects.
///
/// This function resets the `.agent/start_commit` file to track the
/// merge-base with the default branch (or HEAD if on main/master).
///
/// # Arguments
///
/// * `handler` - The effect handler to execute operations through
/// * `working_dir_override` - Optional directory override (for testing)
///
/// # Returns
///
/// Returns the OID that was written to the `start_commit` file, or an error.
///
/// # Effects Emitted
///
/// 1. `SetCurrentDir` - If `working_dir_override` is provided
/// 2. `GitRequireRepo` - Validates git repository exists
/// 3. `GitGetRepoRoot` - Gets the repository root path
/// 4. `SetCurrentDir` - Changes to repo root (if no override)
/// 5. `GitResetStartCommit` - Resets the start commit reference
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_reset_start_commit<H: AppEffectHandler>(
    handler: &mut H,
    working_dir_override: Option<&PathBuf>,
) -> Result<String, String> {
    // Effect 1: Set CWD if override provided
    if let Some(dir) = working_dir_override {
        match handler.execute(AppEffect::SetCurrentDir { path: dir.clone() }) {
            AppEffectResult::Ok => {}
            AppEffectResult::Error(e) => return Err(e),
            other => return Err(format!("unexpected result from SetCurrentDir: {other:?}")),
        }
    }

    // Effect 2: Validate git repo
    match handler.execute(AppEffect::GitRequireRepo) {
        AppEffectResult::Ok => {}
        AppEffectResult::Error(e) => return Err(e),
        other => return Err(format!("unexpected result from GitRequireRepo: {other:?}")),
    }

    // Effect 3: Get repo root and set CWD
    let repo_root = match handler.execute(AppEffect::GitGetRepoRoot) {
        AppEffectResult::Path(p) => p,
        AppEffectResult::Error(e) => return Err(e),
        other => return Err(format!("unexpected result from GitGetRepoRoot: {other:?}")),
    };

    // Effect 4: Set CWD to repo root if no override was provided
    if working_dir_override.is_none() {
        match handler.execute(AppEffect::SetCurrentDir { path: repo_root }) {
            AppEffectResult::Ok => {}
            AppEffectResult::Error(e) => return Err(e),
            other => return Err(format!("unexpected result from SetCurrentDir: {other:?}")),
        }
    }

    // Effect 5: Reset start commit
    match handler.execute(AppEffect::GitResetStartCommit) {
        AppEffectResult::String(oid) => Ok(oid),
        AppEffectResult::Error(e) => Err(e),
        other => Err(format!(
            "unexpected result from GitResetStartCommit: {other:?}"
        )),
    }
}

/// Save the starting commit at pipeline start using effects.
///
/// This records the current HEAD (or merge-base on feature branches) to
/// `.agent/start_commit` for incremental diff generation.
///
/// # Returns
///
/// Returns the OID that was saved, or an error.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn save_start_commit<H: AppEffectHandler>(handler: &mut H) -> Result<String, String> {
    match handler.execute(AppEffect::GitSaveStartCommit) {
        AppEffectResult::String(oid) => Ok(oid),
        AppEffectResult::Error(e) => Err(e),
        other => Err(format!(
            "unexpected result from GitSaveStartCommit: {other:?}"
        )),
    }
}

/// Check if the current branch is main/master using effects.
///
/// # Returns
///
/// Returns `true` if on main or master branch, `false` otherwise.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn is_on_main_branch<H: AppEffectHandler>(handler: &mut H) -> Result<bool, String> {
    match handler.execute(AppEffect::GitIsMainBranch) {
        AppEffectResult::Bool(b) => Ok(b),
        AppEffectResult::Error(e) => Err(e),
        other => Err(format!("unexpected result from GitIsMainBranch: {other:?}")),
    }
}

/// Get the current HEAD OID using effects.
///
/// # Returns
///
/// Returns the 40-character hex OID of HEAD, or an error.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_head_oid<H: AppEffectHandler>(handler: &mut H) -> Result<String, String> {
    match handler.execute(AppEffect::GitGetHeadOid) {
        AppEffectResult::String(oid) => Ok(oid),
        AppEffectResult::Error(e) => Err(e),
        other => Err(format!("unexpected result from GitGetHeadOid: {other:?}")),
    }
}

/// Validate that we're in a git repository using effects.
///
/// # Returns
///
/// Returns `Ok(())` if in a git repo, error otherwise.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn require_repo<H: AppEffectHandler>(handler: &mut H) -> Result<(), String> {
    match handler.execute(AppEffect::GitRequireRepo) {
        AppEffectResult::Ok => Ok(()),
        AppEffectResult::Error(e) => Err(e),
        other => Err(format!("unexpected result from GitRequireRepo: {other:?}")),
    }
}

/// Get the repository root path using effects.
///
/// # Returns
///
/// Returns the absolute path to the repository root.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_repo_root<H: AppEffectHandler>(handler: &mut H) -> Result<PathBuf, String> {
    match handler.execute(AppEffect::GitGetRepoRoot) {
        AppEffectResult::Path(p) => Ok(p),
        AppEffectResult::Error(e) => Err(e),
        other => Err(format!("unexpected result from GitGetRepoRoot: {other:?}")),
    }
}

/// Ensure required files and directories exist using effects.
///
/// Creates the `.agent/logs` and `.agent/tmp` directories if they don't exist.
/// Also writes XSD schemas to `.agent/tmp/` for agent self-validation.
///
/// When `isolation_mode` is true (the default), STATUS.md, NOTES.md and ISSUES.md
/// are NOT created. This prevents context contamination from previous runs.
///
/// # Arguments
///
/// * `handler` - The effect handler to execute operations through
/// * `isolation_mode` - If true, skip creating STATUS.md, NOTES.md, ISSUES.md
///
/// # Returns
///
/// Returns `Ok(())` on success or an error message.
///
/// # Effects Emitted
///
/// 1. `CreateDir` - Creates `.agent/logs` directory
/// 2. `CreateDir` - Creates `.agent/tmp` directory
/// 3. `WriteFile` - Writes XSD schemas to `.agent/tmp/`
/// 4. `WriteFile` - Creates STATUS.md, NOTES.md, ISSUES.md (if not isolation mode)
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn ensure_files_effectful<H: AppEffectHandler>(
    handler: &mut H,
    isolation_mode: bool,
) -> Result<(), String> {
    // Create .agent/logs directory
    match handler.execute(AppEffect::CreateDir {
        path: PathBuf::from(".agent/logs"),
    }) {
        AppEffectResult::Ok => {}
        AppEffectResult::Error(e) => return Err(format!("Failed to create .agent/logs: {e}")),
        other => return Err(format!("Unexpected result from CreateDir: {other:?}")),
    }

    // Create .agent/tmp directory
    match handler.execute(AppEffect::CreateDir {
        path: PathBuf::from(".agent/tmp"),
    }) {
        AppEffectResult::Ok => {}
        AppEffectResult::Error(e) => return Err(format!("Failed to create .agent/tmp: {e}")),
        other => return Err(format!("Unexpected result from CreateDir: {other:?}")),
    }

    // Write XSD schemas
    let schemas = [
        (".agent/tmp/plan.xsd", PLAN_XSD_SCHEMA),
        (
            ".agent/tmp/development_result.xsd",
            DEVELOPMENT_RESULT_XSD_SCHEMA,
        ),
        (
            ".agent/tmp/development_continuation_result.xsd",
            DEVELOPMENT_CONTINUATION_RESULT_XSD_SCHEMA,
        ),
        (".agent/tmp/issues.xsd", ISSUES_XSD_SCHEMA),
        (".agent/tmp/fix_result.xsd", FIX_RESULT_XSD_SCHEMA),
        (".agent/tmp/commit_message.xsd", COMMIT_MESSAGE_XSD_SCHEMA),
    ];

    for (path, content) in schemas {
        match handler.execute(AppEffect::WriteFile {
            path: PathBuf::from(path),
            content: content.to_string(),
        }) {
            AppEffectResult::Ok => {}
            AppEffectResult::Error(e) => return Err(format!("Failed to write {path}: {e}")),
            other => return Err(format!("Unexpected result from WriteFile: {other:?}")),
        }
    }

    // Only create context files in non-isolation mode
    if !isolation_mode {
        let context_files = [
            (".agent/STATUS.md", VAGUE_STATUS_LINE),
            (".agent/NOTES.md", VAGUE_NOTES_LINE),
            (".agent/ISSUES.md", VAGUE_ISSUES_LINE),
        ];

        for (path, line) in context_files {
            let content = format!("{}\n", line.lines().next().unwrap_or_default().trim());
            match handler.execute(AppEffect::WriteFile {
                path: PathBuf::from(path),
                content,
            }) {
                AppEffectResult::Ok => {}
                AppEffectResult::Error(e) => return Err(format!("Failed to write {path}: {e}")),
                other => return Err(format!("Unexpected result from WriteFile: {other:?}")),
            }
        }
    }

    Ok(())
}

/// Reset context for isolation mode by deleting STATUS.md, NOTES.md, ISSUES.md.
///
/// This function is called at the start of each Ralph run when isolation mode
/// is enabled (the default). It prevents context contamination by removing
/// any stale status, notes, or issues from previous runs.
///
/// # Arguments
///
/// * `handler` - The effect handler to execute operations through
///
/// # Returns
///
/// Returns `Ok(())` on success or an error message.
///
/// # Effects Emitted
///
/// 1. `PathExists` - Checks if each context file exists
/// 2. `DeleteFile` - Deletes each existing context file
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn reset_context_for_isolation_effectful<H: AppEffectHandler>(
    handler: &mut H,
) -> Result<(), String> {
    let context_files = [
        PathBuf::from(".agent/STATUS.md"),
        PathBuf::from(".agent/NOTES.md"),
        PathBuf::from(".agent/ISSUES.md"),
    ];

    context_files.iter().try_fold((), |(), path| {
        let exists = match handler.execute(AppEffect::PathExists { path: path.clone() }) {
            AppEffectResult::Bool(b) => b,
            AppEffectResult::Error(e) => {
                return Err(format!(
                    "Failed to check if {} exists: {}",
                    path.display(),
                    e
                ))
            }
            other => {
                return Err(format!(
                    "Unexpected result from PathExists for {}: {:?}",
                    path.display(),
                    other
                ))
            }
        };

        if exists {
            match handler.execute(AppEffect::DeleteFile { path: path.clone() }) {
                AppEffectResult::Ok => Ok(()),
                AppEffectResult::Error(e) => {
                    Err(format!("Failed to delete {}: {}", path.display(), e))
                }
                other => Err(format!(
                    "Unexpected result from DeleteFile for {}: {:?}",
                    path.display(),
                    other
                )),
            }
        } else {
            Ok(())
        }
    })?;

    Ok(())
}

/// Check if PROMPT.md exists using effects.
///
/// # Arguments
///
/// * `handler` - The effect handler to execute operations through
///
/// # Returns
///
/// Returns `Ok(true)` if PROMPT.md exists, `Ok(false)` otherwise.
///
/// # Effects Emitted
///
/// 1. `PathExists` - Checks if PROMPT.md exists
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn check_prompt_exists_effectful<H: AppEffectHandler>(handler: &mut H) -> Result<bool, String> {
    match handler.execute(AppEffect::PathExists {
        path: PathBuf::from("PROMPT.md"),
    }) {
        AppEffectResult::Bool(exists) => Ok(exists),
        AppEffectResult::Error(e) => Err(format!("Failed to check PROMPT.md: {e}")),
        other => Err(format!("Unexpected result from PathExists: {other:?}")),
    }
}

#[cfg(test)]
mod tests;
