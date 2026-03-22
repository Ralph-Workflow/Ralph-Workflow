// Serialization and deserialization logic for checkpoint state.
//
// This file contains workspace-based checkpoint functions for loading,
// saving, and validating checkpoints.

/// Typed error for checkpoint loading failures.
///
/// Private to this module; callers convert to `io::Error` at the boundary.
#[derive(Debug, PartialEq)]
pub(super) enum CheckpointLoadError {
    /// The JSON content could not be parsed.
    InvalidJson(String),
    /// The checkpoint JSON has no `version` field or it is not a number.
    MissingVersion,
    /// The checkpoint version is newer than this binary supports.
    UnsupportedVersionTooNew(u32),
    /// The checkpoint version is legacy (v1 or earlier) and is no longer supported.
    LegacyVersion(u32),
}

impl std::fmt::Display for CheckpointLoadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidJson(msg) => write!(f, "checkpoint JSON parse error: {msg}"),
            Self::MissingVersion => write!(
                f,
                "Invalid checkpoint format: missing or invalid version field.                  Supported versions: 2 (migrated) and 3 (current)."
            ),
            Self::UnsupportedVersionTooNew(v) => write!(
                f,
                "Invalid checkpoint format: version {v} is newer than this binary supports.                  Supported versions: 2 (migrated) and 3 (current).                  Please upgrade Ralph Workflow to resume this checkpoint."
            ),
            Self::LegacyVersion(v) => write!(
                f,
                "Invalid checkpoint format: version {v} is no longer supported (v1 and earlier).                  Supported versions: 2 (best-effort migration) and 3 (current).                  Legacy checkpoint formats are no longer supported.                  To start fresh without data loss:                  cp .agent/checkpoint.json .agent/checkpoint.backup.json && rm .agent/checkpoint.json"
            ),
        }
    }
}

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
) -> Result<PipelineCheckpoint, CheckpointLoadError> {
    let parsed_value: serde_json::Value = serde_json::from_str(content)
        .map_err(|e| CheckpointLoadError::InvalidJson(e.to_string()))?;
    let version = parsed_value
        .get("version")
        .and_then(|v| v.as_u64())
        .ok_or(CheckpointLoadError::MissingVersion)? as u32;

    if version == 2 {
        let checkpoint: PipelineCheckpoint = serde_json::from_str(content)
            .map_err(|e| CheckpointLoadError::InvalidJson(e.to_string()))?;
        return Ok(PipelineCheckpoint { version: 3, ..checkpoint });
    } else if version == 3 {
        let checkpoint: PipelineCheckpoint = serde_json::from_str(content)
            .map_err(|e| CheckpointLoadError::InvalidJson(e.to_string()))?;
        return Ok(checkpoint);
    } else if version > 3 {
        return Err(CheckpointLoadError::UnsupportedVersionTooNew(version));
    }

    Err(CheckpointLoadError::LegacyVersion(version))
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
