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
#[expect(clippy::print_stderr, reason = "checkpoint recovery warning messages")]
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
            // Checkpoint is corrupted - try to restore from backup
            eprintln!("Checkpoint corrupted, attempting restore from backup: {e}");
            return restore_from_backup();
        }
    };

    // Validate the loaded checkpoint
    if let Err(e) = validate_checkpoint(&loaded_checkpoint) {
        eprintln!("Checkpoint validation failed, attempting restore from backup: {e}");
        return restore_from_backup();
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

    // Validate resolved files are a subset of conflicted files
    for resolved in &checkpoint.resolved_files {
        if !checkpoint.conflicted_files.contains(resolved) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("Resolved file '{resolved}' not found in conflicted files list"),
            ));
        }
    }

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
#[expect(clippy::print_stderr, reason = "checkpoint recovery warning messages")]
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
            // Checkpoint is corrupted - try to restore from backup
            eprintln!("Checkpoint corrupted, attempting restore from backup: {e}");
            return restore_from_backup_with_workspace(workspace);
        }
    };

    // Validate the loaded checkpoint
    if let Err(e) = validate_checkpoint_impl(&loaded_checkpoint) {
        eprintln!("Checkpoint validation failed, attempting restore from backup: {e}");
        return restore_from_backup_with_workspace(workspace);
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
