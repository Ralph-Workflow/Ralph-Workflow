// Serialization and deserialization logic for checkpoint state.
//
// This file contains workspace-based checkpoint functions for loading,
// saving, and validating checkpoints.

/// Load a checkpoint from a string.
///
/// Load a checkpoint from a string, with minimal compatibility handling.
///
/// Supported versions:
/// - v3 (current)
/// - v2 (migrated in-memory to v3 by bumping `version`; v3-only fields remain empty)
///
/// Legacy formats (v1, pre-v1) and legacy phases (Fix, `ReviewAgain`) are not supported.
fn load_checkpoint_with_fallback(
    content: &str,
) -> Result<PipelineCheckpoint, Box<dyn std::error::Error>> {
    let checkpoint: PipelineCheckpoint = serde_json::from_str(content)?;

    if checkpoint.version == 2 {
        let migrated = PipelineCheckpoint {
            version: 3,
            ..checkpoint
        };
        return Ok(migrated);
    }

    if checkpoint.version == 3 {
        return Ok(checkpoint);
    }

    if checkpoint.version > 3 {
        return Err(format!(
            "Invalid checkpoint format: version {} is newer than this binary supports. \
             Supported versions: 2 (migrated) and 3 (current). \
             Please upgrade Ralph Workflow to resume this checkpoint.",
            checkpoint.version
        )
        .into());
    }

    Err(format!(
        "Invalid checkpoint format: version {} is no longer supported (v1 and earlier). \
         Supported versions: 2 (best-effort migration) and 3 (current). \
         Legacy checkpoint formats are no longer supported. \
         To start fresh without data loss: cp .agent/checkpoint.json .agent/checkpoint.backup.json && rm .agent/checkpoint.json",
        checkpoint.version
    )
    .into())
}

// ============================================================================
// Workspace-based checkpoint functions (for testability with MemoryWorkspace)
// ============================================================================

/// Calculate SHA-256 checksum of a file using the workspace.
///
/// # Arguments
///
/// * `workspace` - The workspace for file operations
/// * `path` - Relative path within the workspace
///
/// Returns `None` if the file doesn't exist or cannot be read.
pub fn calculate_file_checksum_with_workspace(
    workspace: &dyn Workspace,
    path: &Path,
) -> Option<String> {
    let content = workspace.read_bytes(path).ok()?;
    Some(calculate_checksum_from_bytes(&content))
}

/// Save a pipeline checkpoint using the workspace.
///
/// # Arguments
///
/// * `workspace` - The workspace for file operations
/// * `checkpoint` - The checkpoint to save
///
/// # Performance
///
/// Uses optimized serialization with pre-allocated buffer and compact JSON
/// encoding (no pretty-printing) to minimize serialization time.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn save_checkpoint_with_workspace(
    workspace: &dyn Workspace,
    checkpoint: &PipelineCheckpoint,
) -> io::Result<()> {
    let json = serde_json::to_string(checkpoint).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Failed to serialize checkpoint: {e}"),
        )
    })?;

    // Ensure the .agent directory exists
    workspace.create_dir_all(Path::new(AGENT_DIR))?;

    // Write checkpoint file atomically
    workspace.write_atomic(Path::new(&checkpoint_path()), &json)
}

/// Load an existing checkpoint using the workspace.
///
/// Returns `Ok(Some(checkpoint))` if a valid checkpoint was loaded,
/// `Ok(None)` if no checkpoint file exists, or an error if the file
/// exists but cannot be parsed.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn load_checkpoint_with_workspace(
    workspace: &dyn Workspace,
) -> io::Result<Option<PipelineCheckpoint>> {
    let checkpoint_path_str = checkpoint_path();
    let checkpoint_file = Path::new(&checkpoint_path_str);

    if !workspace.exists(checkpoint_file) {
        return Ok(None);
    }

    let content = workspace.read(checkpoint_file)?;
    let loaded_checkpoint = load_checkpoint_with_fallback(&content).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Failed to parse checkpoint: {e}"),
        )
    })?;

    Ok(Some(loaded_checkpoint))
}

/// Delete the checkpoint file using the workspace.
///
/// Does nothing if the checkpoint file doesn't exist.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn clear_checkpoint_with_workspace(workspace: &dyn Workspace) -> io::Result<()> {
    let checkpoint_path_str = checkpoint_path();
    let checkpoint_file = Path::new(&checkpoint_path_str);

    if workspace.exists(checkpoint_file) {
        workspace.remove(checkpoint_file)?;
    }
    Ok(())
}

/// Check if a checkpoint exists using the workspace.
pub fn checkpoint_exists_with_workspace(workspace: &dyn Workspace) -> bool {
    workspace.exists(Path::new(&checkpoint_path()))
}
