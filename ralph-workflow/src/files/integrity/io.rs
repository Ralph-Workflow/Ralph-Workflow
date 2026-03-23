// integrity/io.rs — boundary module for clock-read operations.
// Exempt from forbid_read_clock because the file stem is `io`.
// Called from check_filesystem_ready_with_workspace in integrity/mod.rs.

// Find the first stale lock file (modified more than 1 hour ago) in the given entries.
pub(super) fn find_stale_lock(entries: &[crate::workspace::DirEntry]) -> Option<String> {
    let threshold = std::time::Duration::from_secs(3600);
    entries.iter().find_map(|entry| {
        let name = entry.file_name()?.to_str()?;
        if !name.to_ascii_lowercase().ends_with(".lock") {
            return None;
        }
        let modified = entry.modified()?;
        if modified.elapsed().ok()? > threshold {
            Some(name.to_string())
        } else {
            None
        }
    })
}
