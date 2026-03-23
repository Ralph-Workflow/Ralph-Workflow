// agent_files/io.rs — boundary module for direct filesystem operations.
// Exempt from forbid_io_effects because the file stem is `io`.

/// Check if a file contains a specific marker string using std::fs.
///
/// This is the non-workspace version that operates on absolute paths
/// outside the workspace abstraction (e.g., `.git/hooks/`).
///
/// Returns `Ok(true)` if the marker is found, `Ok(false)` if not found or file doesn't exist.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn file_contains_marker(path: &Path, marker: &str) -> std::io::Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    let content = std::fs::read_to_string(path)?;
    Ok(content.lines().any(|line| line.contains(marker)))
}
