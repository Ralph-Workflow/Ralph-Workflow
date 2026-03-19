//! Rebase checkpoint system for fault tolerance.
//!
//! This module provides types and persistence for rebase state,
//! allowing recovery from interrupted or failed rebase operations.
//!
//! # Workspace Support
//!
//! This module provides two sets of functions:
//! - Standard functions using `std::fs` for production use
//! - `_with_workspace` variants for testability with `MemoryWorkspace`

#![deny(unsafe_code)]

use crate::workspace::Workspace;
use std::fs;
use std::io;
use std::path::Path;

/// Default directory for Ralph's internal files.
const AGENT_DIR: &str = ".agent";

/// Rebase checkpoint file name.
const REBASE_CHECKPOINT_FILE: &str = "rebase_checkpoint.json";

/// Get the rebase checkpoint file path.
///
/// The checkpoint is stored in `.agent/rebase_checkpoint.json`
/// relative to the current working directory.
#[must_use]
pub fn rebase_checkpoint_path() -> String {
    format!("{AGENT_DIR}/{REBASE_CHECKPOINT_FILE}")
}

/// Get the rebase checkpoint backup file path.
///
/// The backup is stored in `.agent/rebase_checkpoint.json.bak`
/// and is used for corruption recovery.
#[must_use]
pub fn rebase_checkpoint_backup_path() -> String {
    format!("{AGENT_DIR}/{REBASE_CHECKPOINT_FILE}.bak")
}

// =============================================================================
// Types (from rebase_checkpoint/types.rs)
// =============================================================================

// Checkpoint type definitions for rebase operations.
//
// This file contains RebasePhase enum and RebaseCheckpoint struct
// with their implementations.

/// Phase of a rebase operation.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum RebasePhase {
    /// Rebase has not started.
    NotStarted,
    /// Pre-rebase validation in progress.
    PreRebaseCheck,
    /// Rebase operation is in progress.
    RebaseInProgress,
    /// Conflicts detected, awaiting resolution.
    ConflictDetected,
    /// Conflict resolution in progress.
    ConflictResolutionInProgress,
    /// Completing rebase after conflict resolution.
    CompletingRebase,
    /// Rebase completed successfully.
    RebaseComplete,
    /// Rebase was aborted.
    RebaseAborted,
}

impl RebasePhase {
    /// Get the maximum number of recovery attempts allowed for this phase.
    ///
    /// Different phases have different recovery limits:
    /// - `ConflictResolutionInProgress`: Higher limit (5) - conflicts may need multiple AI attempts
    /// - `ConflictDetected`: Medium limit (3) - waiting for AI to process
    /// - `RebaseInProgress`: Lower limit (2) - transient Git issues
    /// - `CompletingRebase`: Lower limit (2) - final stages should be quick
    /// - `PreRebaseCheck`: Low limit (1) - validation should pass immediately
    /// - Other phases: Default limit (3)
    ///
    /// # Returns
    ///
    /// The maximum number of recovery attempts for this phase.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn max_recovery_attempts(&self) -> u32 {
        match self {
            Self::ConflictResolutionInProgress => 5,
            Self::RebaseInProgress | Self::CompletingRebase => 2,
            Self::PreRebaseCheck => 1,
            Self::ConflictDetected
            | Self::NotStarted
            | Self::RebaseComplete
            | Self::RebaseAborted => 3,
        }
    }
}

/// Checkpoint data for rebase operations.
///
/// This structure contains all the information needed to resume
/// a rebase operation after an interruption.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RebaseCheckpoint {
    /// Current phase of the rebase.
    pub phase: RebasePhase,
    /// Upstream branch being rebased onto.
    pub upstream_branch: String,
    /// Files with conflicts.
    pub conflicted_files: Vec<String>,
    /// Files that have been resolved.
    pub resolved_files: Vec<String>,
    /// Number of errors encountered.
    pub error_count: u32,
    /// Last error message.
    pub last_error: Option<String>,
    /// Timestamp of checkpoint.
    pub timestamp: String,
    /// Number of errors encountered in the current phase.
    #[serde(default)]
    pub phase_error_count: u32,
}

impl Default for RebaseCheckpoint {
    fn default() -> Self {
        Self {
            phase: RebasePhase::NotStarted,
            upstream_branch: String::new(),
            conflicted_files: Vec::new(),
            resolved_files: Vec::new(),
            error_count: 0,
            last_error: None,
            timestamp: chrono::Utc::now().to_rfc3339(),
            phase_error_count: 0,
        }
    }
}

impl RebaseCheckpoint {
    /// Create a new rebase checkpoint.
    #[must_use]
    pub fn new(upstream_branch: String) -> Self {
        Self {
            phase: RebasePhase::NotStarted,
            upstream_branch,
            conflicted_files: Vec::new(),
            resolved_files: Vec::new(),
            error_count: 0,
            last_error: None,
            timestamp: chrono::Utc::now().to_rfc3339(),
            phase_error_count: 0,
        }
    }

    /// Set the phase of the rebase.
    ///
    /// Resets the phase error count when transitioning to a new phase.
    #[must_use]
    pub fn with_phase(self, phase: RebasePhase) -> Self {
        let phase_error_count = if self.phase != phase {
            0
        } else {
            self.phase_error_count
        };
        Self {
            phase,
            phase_error_count,
            timestamp: chrono::Utc::now().to_rfc3339(),
            ..self
        }
    }

    /// Add a conflicted file.
    #[must_use]
    pub fn with_conflicted_file(self, file: String) -> Self {
        let conflicted_files = if !self.conflicted_files.contains(&file) {
            self.conflicted_files
                .into_iter()
                .chain(std::iter::once(file))
                .collect()
        } else {
            self.conflicted_files
        };
        Self {
            conflicted_files,
            ..self
        }
    }

    /// Add a resolved file.
    #[must_use]
    pub fn with_resolved_file(self, file: String) -> Self {
        let resolved_files = if !self.resolved_files.contains(&file) {
            self.resolved_files
                .into_iter()
                .chain(std::iter::once(file))
                .collect()
        } else {
            self.resolved_files
        };
        Self {
            resolved_files,
            ..self
        }
    }

    /// Add an error.
    ///
    /// Increments both the global error count and the phase-specific error count.
    #[must_use]
    pub fn with_error(self, error: String) -> Self {
        Self {
            error_count: self.error_count.saturating_add(1),
            phase_error_count: self.phase_error_count.saturating_add(1),
            last_error: Some(error),
            timestamp: chrono::Utc::now().to_rfc3339(),
            ..self
        }
    }

    /// Check if all conflicts are resolved.
    #[must_use]
    pub fn all_conflicts_resolved(&self) -> bool {
        self.conflicted_files
            .iter()
            .all(|f| self.resolved_files.contains(f))
    }

    /// Get the number of unresolved conflicts.
    #[must_use]
    pub fn unresolved_conflict_count(&self) -> usize {
        self.conflicted_files
            .iter()
            .filter(|f| !self.resolved_files.contains(f))
            .count()
    }
}

// =============================================================================
// Persistence (from rebase_checkpoint/persistence.rs)
// =============================================================================

// Save/load checkpoint operations for rebase state.
//
// This file contains serialization, file I/O, backup, and restore
// operations for rebase checkpoints.

/// Save a rebase checkpoint to disk.
///
/// Writes the checkpoint atomically by writing to a temp file first,
/// then renaming to the final path. This prevents corruption if the
/// process is interrupted during the write.
///
/// Also creates a backup before overwriting an existing checkpoint.
///
/// # Errors
///
/// Returns an error if serialization fails or the file cannot be written.
pub fn save_rebase_checkpoint(checkpoint: &RebaseCheckpoint) -> io::Result<()> {
    let json = serde_json::to_string_pretty(checkpoint).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Failed to serialize rebase checkpoint: {e}"),
        )
    })?;

    // Ensure the .agent directory exists before attempting to write
    fs::create_dir_all(AGENT_DIR)?;

    // Check if a checkpoint already exists (we'll need this info after saving)
    let checkpoint_existed = Path::new(&rebase_checkpoint_path()).exists();

    // Create backup before overwriting existing checkpoint
    let _ = backup_checkpoint();

    // Write atomically by writing to temp file then renaming
    let checkpoint_path_str = rebase_checkpoint_path();
    let temp_path = format!("{checkpoint_path_str}.tmp");

    // Ensure temp file is cleaned up even if write or rename fails
    let write_result = fs::write(&temp_path, &json);
    if write_result.is_err() {
        let _ = fs::remove_file(&temp_path);
        return write_result;
    }

    let rename_result = fs::rename(&temp_path, &checkpoint_path_str);
    if rename_result.is_err() {
        let _ = fs::remove_file(&temp_path);
        return rename_result;
    }

    // If this was the first save (no existing checkpoint before),
    // create a backup now so we always have a backup for recovery
    if !checkpoint_existed {
        let _ = backup_checkpoint();
    }

    Ok(())
}

/// Load an existing rebase checkpoint if one exists.
///
/// Returns `Ok(Some(checkpoint))` if a valid checkpoint was loaded,
/// `Ok(None)` if no checkpoint file exists, or an error if the file
/// exists but cannot be parsed.
///
/// If the main checkpoint is corrupted, attempts to restore from backup.
///
/// # Errors
///
/// Returns an error if the checkpoint file exists but cannot be read
/// or contains invalid JSON, and no valid backup exists.
pub fn load_rebase_checkpoint() -> io::Result<Option<RebaseCheckpoint>> {
    let checkpoint = rebase_checkpoint_path();
    let path = Path::new(&checkpoint);
    if !path.exists() {
        return Ok(None);
    }

    let content = fs::read_to_string(path)?;
    let loaded_checkpoint: RebaseCheckpoint = match serde_json::from_str(&content) {
        Ok(cp) => cp,
        Err(e) => {
            let backup_result = restore_from_backup();
            return if backup_result.is_err() {
                Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!(
                        "Checkpoint corrupted: {e}; backup restore failed: {}",
                        backup_result.unwrap_err()
                    ),
                ))
            } else {
                backup_result
            };
        }
    };

    if let Err(e) = validate_checkpoint(&loaded_checkpoint) {
        let backup_result = restore_from_backup();
        return if backup_result.is_err() {
            Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "Checkpoint validation failed: {e}; backup restore failed: {}",
                    backup_result.unwrap_err()
                ),
            ))
        } else {
            backup_result
        };
    }

    Ok(Some(loaded_checkpoint))
}

/// Delete the rebase checkpoint file.
///
/// Called on successful rebase completion to clean up the checkpoint.
/// Does nothing if the checkpoint file doesn't exist.
///
/// # Errors
///
/// Returns an error if the file exists but cannot be deleted.
pub fn clear_rebase_checkpoint() -> io::Result<()> {
    let checkpoint = rebase_checkpoint_path();
    let path = Path::new(&checkpoint);
    if path.exists() {
        fs::remove_file(path)?;
    }
    Ok(())
}

/// Check if a rebase checkpoint exists.
///
/// Returns `true` if a checkpoint file exists, `false` otherwise.
#[must_use]
pub fn rebase_checkpoint_exists() -> bool {
    Path::new(&rebase_checkpoint_path()).exists()
}

/// Validate a checkpoint's integrity.
///
/// Checks that all required fields are present and valid.
/// Returns `Ok(())` if valid, or an error describing the issue.
///
/// # Errors
///
/// Returns an error if the checkpoint is missing required fields or has invalid values.
#[cfg(any(test, feature = "test-utils"))]
pub fn validate_checkpoint(checkpoint: &RebaseCheckpoint) -> io::Result<()> {
    validate_checkpoint_impl(checkpoint)
}

/// Validate a checkpoint's integrity.
///
/// Checks that all required fields are present and valid.
/// Returns `Ok(())` if valid, or an error describing the issue.
#[cfg(not(any(test, feature = "test-utils")))]
fn validate_checkpoint(checkpoint: &RebaseCheckpoint) -> io::Result<()> {
    validate_checkpoint_impl(checkpoint)
}

/// Implementation of checkpoint validation.
fn validate_checkpoint_impl(checkpoint: &RebaseCheckpoint) -> io::Result<()> {
    // Validate upstream branch is not empty (unless it's a new checkpoint)
    if checkpoint.phase != RebasePhase::NotStarted && checkpoint.upstream_branch.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Checkpoint has empty upstream branch",
        ));
    }

    // Validate timestamp format
    if chrono::DateTime::parse_from_rfc3339(&checkpoint.timestamp).is_err() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Checkpoint has invalid timestamp format",
        ));
    }

    checkpoint.resolved_files.iter().try_for_each(|resolved| {
        if checkpoint.conflicted_files.contains(resolved) {
            return Ok(());
        }
        Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Resolved file '{resolved}' not found in conflicted files list"),
        ))
    })?;

    Ok(())
}

/// Create a backup of the current checkpoint.
///
/// Copies the current checkpoint file to a `.bak` file.
/// Returns `Ok(())` if backup succeeded, or an error if it failed.
///
/// If the checkpoint file doesn't exist, this is not an error
/// (the backup simply doesn't exist).
fn backup_checkpoint() -> io::Result<()> {
    let checkpoint_path = rebase_checkpoint_path();
    let backup_path = rebase_checkpoint_backup_path();
    let checkpoint = Path::new(&checkpoint_path);
    let backup = Path::new(&backup_path);

    if !checkpoint.exists() {
        // No checkpoint to back up - this is fine
        return Ok(());
    }

    // Remove existing backup if it exists
    if backup.exists() {
        fs::remove_file(backup)?;
    }

    // Copy checkpoint to backup
    fs::copy(checkpoint, backup)?;
    Ok(())
}

/// Restore a checkpoint from backup.
///
/// Attempts to restore from the backup file if the main checkpoint
/// is corrupted or missing. Returns `Ok(Some(checkpoint))` if restored,
/// `Ok(None)` if no backup exists, or an error if restoration failed.
fn restore_from_backup() -> io::Result<Option<RebaseCheckpoint>> {
    let backup_path = rebase_checkpoint_backup_path();
    let backup = Path::new(&backup_path);

    if !backup.exists() {
        return Ok(None);
    }

    let content = fs::read_to_string(backup)?;
    let checkpoint: RebaseCheckpoint = serde_json::from_str(&content).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Failed to parse backup checkpoint: {e}"),
        )
    })?;

    // Validate the restored checkpoint
    validate_checkpoint(&checkpoint)?;

    // If valid, copy backup back to main checkpoint
    let checkpoint_path = rebase_checkpoint_path();
    fs::copy(backup, checkpoint_path)?;

    Ok(Some(checkpoint))
}

// =============================================================================
// Workspace-aware variants for testability
// =============================================================================

/// Save a rebase checkpoint using workspace abstraction.
///
/// This is the architecture-conformant version that uses the Workspace trait
/// instead of direct filesystem access, allowing for proper testing with
/// `MemoryWorkspace`.
///
/// Writes the checkpoint atomically by writing to a temp file first,
/// then renaming to the final path.
///
/// # Arguments
///
/// * `checkpoint` - The checkpoint to save
/// * `workspace` - The workspace to use for filesystem operations
///
/// # Errors
///
/// Returns an error if serialization fails or the file cannot be written.
pub fn save_rebase_checkpoint_with_workspace(
    checkpoint: &RebaseCheckpoint,
    workspace: &dyn Workspace,
) -> io::Result<()> {
    let json = serde_json::to_string_pretty(checkpoint).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Failed to serialize rebase checkpoint: {e}"),
        )
    })?;

    let agent_dir = Path::new(AGENT_DIR);
    let checkpoint_path = Path::new(AGENT_DIR).join(REBASE_CHECKPOINT_FILE);
    let backup_path = Path::new(AGENT_DIR).join(format!("{REBASE_CHECKPOINT_FILE}.bak"));

    // Ensure the .agent directory exists
    workspace.create_dir_all(agent_dir)?;

    // Check if a checkpoint already exists
    let checkpoint_existed = workspace.exists(&checkpoint_path);

    // Create backup before overwriting existing checkpoint
    if checkpoint_existed {
        let _ = backup_checkpoint_with_workspace(workspace);
    }

    // Write the checkpoint (workspace.write_atomic handles atomicity)
    workspace.write_atomic(&checkpoint_path, &json)?;

    // If this was the first save, create a backup now
    if !checkpoint_existed {
        let _ = backup_checkpoint_with_workspace(workspace);
    }

    // Also clean up backup path if it exists and is empty
    if workspace.exists(&backup_path) {
        if let Ok(content) = workspace.read(&backup_path) {
            if content.trim().is_empty() {
                let _ = workspace.remove(&backup_path);
            }
        }
    }

    Ok(())
}

/// Load an existing rebase checkpoint using workspace abstraction.
///
/// This is the architecture-conformant version that uses the Workspace trait
/// instead of direct filesystem access, allowing for proper testing with
/// `MemoryWorkspace`.
///
/// # Arguments
///
/// * `workspace` - The workspace to use for filesystem operations
///
/// # Returns
///
/// Returns `Ok(Some(checkpoint))` if a valid checkpoint was loaded,
/// `Ok(None)` if no checkpoint file exists, or an error if the file
/// exists but cannot be parsed and no valid backup exists.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn load_rebase_checkpoint_with_workspace(
    workspace: &dyn Workspace,
) -> io::Result<Option<RebaseCheckpoint>> {
    let checkpoint_path = Path::new(AGENT_DIR).join(REBASE_CHECKPOINT_FILE);

    if !workspace.exists(&checkpoint_path) {
        return Ok(None);
    }

    let content = workspace.read(&checkpoint_path)?;
    let loaded_checkpoint: RebaseCheckpoint = match serde_json::from_str(&content) {
        Ok(cp) => cp,
        Err(e) => {
            let backup_result = restore_from_backup_with_workspace(workspace);
            return if backup_result.is_err() {
                Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!(
                        "Checkpoint corrupted: {e}; backup restore failed: {}",
                        backup_result.unwrap_err()
                    ),
                ))
            } else {
                backup_result
            };
        }
    };

    if let Err(e) = validate_checkpoint_impl(&loaded_checkpoint) {
        let backup_result = restore_from_backup_with_workspace(workspace);
        return if backup_result.is_err() {
            Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "Checkpoint validation failed: {e}; backup restore failed: {}",
                    backup_result.unwrap_err()
                ),
            ))
        } else {
            backup_result
        };
    }

    Ok(Some(loaded_checkpoint))
}

/// Delete the rebase checkpoint file using workspace abstraction.
///
/// This is the architecture-conformant version that uses the Workspace trait
/// instead of direct filesystem access, allowing for proper testing with
/// `MemoryWorkspace`.
///
/// # Arguments
///
/// * `workspace` - The workspace to use for filesystem operations
///
/// # Errors
///
/// Returns an error if the file exists but cannot be deleted.
pub fn clear_rebase_checkpoint_with_workspace(workspace: &dyn Workspace) -> io::Result<()> {
    let checkpoint_path = Path::new(AGENT_DIR).join(REBASE_CHECKPOINT_FILE);

    if workspace.exists(&checkpoint_path) {
        workspace.remove(&checkpoint_path)?;
    }
    Ok(())
}

/// Check if a rebase checkpoint exists using workspace abstraction.
///
/// # Arguments
///
/// * `workspace` - The workspace to use for filesystem operations
///
/// # Returns
///
/// Returns `true` if a checkpoint file exists, `false` otherwise.
pub fn rebase_checkpoint_exists_with_workspace(workspace: &dyn Workspace) -> bool {
    let checkpoint_path = Path::new(AGENT_DIR).join(REBASE_CHECKPOINT_FILE);
    workspace.exists(&checkpoint_path)
}

/// Create a backup of the current checkpoint using workspace abstraction.
fn backup_checkpoint_with_workspace(workspace: &dyn Workspace) -> io::Result<()> {
    let checkpoint_path = Path::new(AGENT_DIR).join(REBASE_CHECKPOINT_FILE);
    let backup_path = Path::new(AGENT_DIR).join(format!("{REBASE_CHECKPOINT_FILE}.bak"));

    if !workspace.exists(&checkpoint_path) {
        return Ok(());
    }

    // Remove existing backup if it exists
    if workspace.exists(&backup_path) {
        workspace.remove(&backup_path)?;
    }

    // Copy checkpoint to backup (read + write since workspace doesn't have copy)
    let content = workspace.read(&checkpoint_path)?;
    workspace.write(&backup_path, &content)?;

    Ok(())
}

/// Restore a checkpoint from backup using workspace abstraction.
fn restore_from_backup_with_workspace(
    workspace: &dyn Workspace,
) -> io::Result<Option<RebaseCheckpoint>> {
    let checkpoint_path = Path::new(AGENT_DIR).join(REBASE_CHECKPOINT_FILE);
    let backup_path = Path::new(AGENT_DIR).join(format!("{REBASE_CHECKPOINT_FILE}.bak"));

    if !workspace.exists(&backup_path) {
        return Ok(None);
    }

    let content = workspace.read(&backup_path)?;
    let checkpoint: RebaseCheckpoint = serde_json::from_str(&content).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Failed to parse backup checkpoint: {e}"),
        )
    })?;

    // Validate the restored checkpoint
    validate_checkpoint_impl(&checkpoint)?;

    // If valid, copy backup back to main checkpoint
    workspace.write(&checkpoint_path, &content)?;

    Ok(Some(checkpoint))
}

// =============================================================================
// Tests (from rebase_checkpoint/tests.rs)
// =============================================================================

// Tests for rebase checkpoint functionality.
//
// Contains pure unit tests for checkpoint types and workspace-aware variants.
// Tests requiring CWD-relative filesystem I/O are in tests/system_tests/rebase_checkpoint/.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rebase_checkpoint_default() {
        let checkpoint = RebaseCheckpoint::default();
        assert_eq!(checkpoint.phase, RebasePhase::NotStarted);
        assert!(checkpoint.upstream_branch.is_empty());
        assert!(checkpoint.conflicted_files.is_empty());
        assert!(checkpoint.resolved_files.is_empty());
        assert_eq!(checkpoint.error_count, 0);
        assert!(checkpoint.last_error.is_none());
    }

    #[test]
    fn test_rebase_checkpoint_new() {
        let checkpoint = RebaseCheckpoint::new("main".to_string());
        assert_eq!(checkpoint.phase, RebasePhase::NotStarted);
        assert_eq!(checkpoint.upstream_branch, "main");
    }

    #[test]
    fn test_rebase_checkpoint_with_phase() {
        let checkpoint =
            RebaseCheckpoint::new("main".to_string()).with_phase(RebasePhase::RebaseInProgress);
        assert_eq!(checkpoint.phase, RebasePhase::RebaseInProgress);
    }

    #[test]
    fn test_rebase_checkpoint_with_conflicted_file() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_conflicted_file("file1.txt".to_string())
            .with_conflicted_file("file2.txt".to_string());

        assert_eq!(
            checkpoint.conflicted_files.len(),
            2,
            "Should track both files"
        );
        assert!(
            checkpoint
                .conflicted_files
                .contains(&"file1.txt".to_string()),
            "Should contain file1.txt"
        );
        assert!(
            checkpoint
                .conflicted_files
                .contains(&"file2.txt".to_string()),
            "Should contain file2.txt"
        );

        // Adding duplicate should not increase count
        let checkpoint = checkpoint.with_conflicted_file("file1.txt".to_string());
        assert_eq!(
            checkpoint.conflicted_files.len(),
            2,
            "Should not increase count for duplicate"
        );
        assert!(
            checkpoint
                .conflicted_files
                .contains(&"file1.txt".to_string()),
            "Should still contain file1.txt"
        );
    }

    #[test]
    fn test_rebase_checkpoint_with_resolved_file() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_conflicted_file("file1.txt".to_string())
            .with_resolved_file("file1.txt".to_string());
        assert!(checkpoint.resolved_files.contains(&"file1.txt".to_string()));
    }

    #[test]
    fn test_rebase_checkpoint_with_error() {
        let checkpoint =
            RebaseCheckpoint::new("main".to_string()).with_error("Test error".to_string());
        assert_eq!(checkpoint.error_count, 1);
        assert_eq!(checkpoint.last_error, Some("Test error".to_string()));
    }

    #[test]
    fn test_rebase_checkpoint_all_conflicts_resolved() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_conflicted_file("file1.txt".to_string())
            .with_conflicted_file("file2.txt".to_string())
            .with_resolved_file("file1.txt".to_string())
            .with_resolved_file("file2.txt".to_string());
        assert!(checkpoint.all_conflicts_resolved());
    }

    #[test]
    fn test_rebase_checkpoint_unresolved_conflict_count() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_conflicted_file("file1.txt".to_string())
            .with_conflicted_file("file2.txt".to_string())
            .with_resolved_file("file1.txt".to_string());
        assert_eq!(checkpoint.unresolved_conflict_count(), 1);
    }

    #[test]
    fn test_rebase_phase_equality() {
        assert_eq!(RebasePhase::NotStarted, RebasePhase::NotStarted);
        assert_ne!(RebasePhase::NotStarted, RebasePhase::RebaseInProgress);
    }

    #[test]
    fn test_rebase_checkpoint_path() {
        let path = rebase_checkpoint_path();
        assert!(path.contains(".agent"));
        assert!(path.contains("rebase_checkpoint.json"));
    }

    #[test]
    fn test_rebase_checkpoint_serialization() {
        let checkpoint = RebaseCheckpoint::new("feature-branch".to_string())
            .with_phase(RebasePhase::ConflictResolutionInProgress)
            .with_conflicted_file("src/lib.rs".to_string())
            .with_resolved_file("src/main.rs".to_string())
            .with_error("Test error".to_string());

        let json = serde_json::to_string(&checkpoint).unwrap();
        assert!(json.contains("feature-branch"));
        assert!(json.contains("src/lib.rs"));

        let deserialized: RebaseCheckpoint = serde_json::from_str(&json).unwrap();
        assert_eq!(deserialized.phase, checkpoint.phase);
        assert_eq!(deserialized.upstream_branch, checkpoint.upstream_branch);
    }

    #[test]
    fn test_validate_checkpoint_valid() {
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_phase(RebasePhase::RebaseInProgress)
            .with_conflicted_file("file1.rs".to_string())
            .with_resolved_file("file1.rs".to_string());

        assert!(validate_checkpoint(&checkpoint).is_ok());
    }

    #[test]
    fn test_validate_checkpoint_empty_upstream() {
        // NotStarted phase allows empty upstream
        let checkpoint = RebaseCheckpoint::new(String::new()).with_phase(RebasePhase::NotStarted);
        assert!(validate_checkpoint(&checkpoint).is_ok());

        // Other phases require non-empty upstream
        let checkpoint =
            RebaseCheckpoint::new(String::new()).with_phase(RebasePhase::RebaseInProgress);
        assert!(validate_checkpoint(&checkpoint).is_err());
    }

    #[test]
    fn test_validate_checkpoint_invalid_timestamp() {
        let mut checkpoint = RebaseCheckpoint::new("main".to_string());
        checkpoint.timestamp = "invalid-timestamp".to_string();

        assert!(validate_checkpoint(&checkpoint).is_err());
    }

    #[test]
    fn test_validate_checkpoint_resolved_without_conflicted() {
        let checkpoint =
            RebaseCheckpoint::new("main".to_string()).with_resolved_file("file1.rs".to_string());

        // Resolved file not in conflicted list should fail validation
        assert!(validate_checkpoint(&checkpoint).is_err());
    }
}

#[cfg(all(test, feature = "test-utils"))]
mod workspace_tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_save_and_load_checkpoint_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_phase(RebasePhase::ConflictDetected)
            .with_conflicted_file("file1.rs".to_string());

        // Save checkpoint
        save_rebase_checkpoint_with_workspace(&checkpoint, &workspace).unwrap();

        // Verify it exists
        assert!(rebase_checkpoint_exists_with_workspace(&workspace));

        // Load it back
        let loaded = load_rebase_checkpoint_with_workspace(&workspace)
            .unwrap()
            .expect("checkpoint should exist after save");

        assert_eq!(loaded.phase, RebasePhase::ConflictDetected);
        assert_eq!(loaded.upstream_branch, "main");
        assert_eq!(
            loaded.conflicted_files.len(),
            1,
            "Should have one conflicted file"
        );
        assert!(
            loaded.conflicted_files.contains(&"file1.rs".to_string()),
            "Should contain file1.rs"
        );
    }

    #[test]
    fn test_clear_checkpoint_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        let checkpoint = RebaseCheckpoint::new("main".to_string());
        save_rebase_checkpoint_with_workspace(&checkpoint, &workspace).unwrap();
        assert!(rebase_checkpoint_exists_with_workspace(&workspace));

        clear_rebase_checkpoint_with_workspace(&workspace).unwrap();
        assert!(!rebase_checkpoint_exists_with_workspace(&workspace));
    }

    #[test]
    fn test_load_nonexistent_checkpoint_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        let result = load_rebase_checkpoint_with_workspace(&workspace).unwrap();
        assert!(result.is_none());
    }

    #[test]
    fn test_checkpoint_backup_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        // Create and save first checkpoint
        let checkpoint1 =
            RebaseCheckpoint::new("main".to_string()).with_phase(RebasePhase::RebaseInProgress);
        save_rebase_checkpoint_with_workspace(&checkpoint1, &workspace).unwrap();

        // Save another checkpoint (should create backup of first)
        let checkpoint2 =
            RebaseCheckpoint::new("main".to_string()).with_phase(RebasePhase::RebaseComplete);
        save_rebase_checkpoint_with_workspace(&checkpoint2, &workspace).unwrap();

        // Load should return the latest checkpoint
        let loaded = load_rebase_checkpoint_with_workspace(&workspace)
            .unwrap()
            .expect("checkpoint should exist");
        assert_eq!(loaded.phase, RebasePhase::RebaseComplete);
    }

    #[test]
    fn test_corrupted_checkpoint_restores_from_backup_with_workspace() {
        let workspace = MemoryWorkspace::new_test();
        let checkpoint_path = Path::new(AGENT_DIR).join(REBASE_CHECKPOINT_FILE);
        let backup_path = Path::new(AGENT_DIR).join(format!("{REBASE_CHECKPOINT_FILE}.bak"));

        // Create and save a valid checkpoint
        let checkpoint = RebaseCheckpoint::new("main".to_string())
            .with_phase(RebasePhase::ConflictDetected)
            .with_conflicted_file("file.rs".to_string());
        save_rebase_checkpoint_with_workspace(&checkpoint, &workspace).unwrap();

        // Verify backup exists
        assert!(workspace.exists(&backup_path));

        // Corrupt the main checkpoint
        workspace
            .write(&checkpoint_path, "corrupted data {{{")
            .unwrap();

        // Loading should restore from backup
        let loaded = load_rebase_checkpoint_with_workspace(&workspace)
            .unwrap()
            .expect("should restore from backup");

        assert_eq!(loaded.phase, RebasePhase::ConflictDetected);
    }
}
