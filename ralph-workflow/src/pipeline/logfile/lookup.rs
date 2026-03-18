//! Log file lookup utilities.
//!
//! Provides functions to find and read the most recent log files matching
//! a prefix pattern.

use crate::workspace::Workspace;
use std::path::{Path, PathBuf};

/// Find the most recent log file matching a prefix pattern.
///
/// Searches the parent directory for log files that match the prefix pattern
/// and returns the most recently modified one.
///
/// # Arguments
///
/// * `log_prefix` - The prefix path (e.g., ".`agent/logs/planning_1`")
/// * `workspace` - The workspace to search in
///
/// # Returns
///
/// The path to the most recent matching log file, or `None` if no match found.
///
/// # Examples
///
/// ```ignore
/// use std::path::Path;
/// use ralph_workflow::pipeline::logfile::find_most_recent_logfile;
/// use ralph_workflow::workspace::WorkspaceFs;
///
/// let workspace = WorkspaceFs::new("/repo".into());
/// let prefix = Path::new(".agent/logs/planning_1");
/// if let Some(log_file) = find_most_recent_logfile(prefix, &workspace) {
///     println!("Most recent log: {:?}", log_file);
/// }
/// ```
pub fn find_most_recent_logfile(log_prefix: &Path, workspace: &dyn Workspace) -> Option<PathBuf> {
    let parent = log_prefix.parent().unwrap_or_else(|| Path::new("."));
    let prefix_str = log_prefix.file_name().and_then(|s| s.to_str())?;

    workspace
        .read_dir(parent)
        .ok()
        .and_then(|entries: Vec<crate::workspace::DirEntry>| {
            entries
                .into_iter()
                .filter_map(|entry: crate::workspace::DirEntry| {
                    if !entry.is_file() {
                        return None;
                    }
                    let Some(filename) = entry.file_name().and_then(|s| s.to_str()) else {
                        return None;
                    };
                    let has_log_ext = entry
                        .path()
                        .extension()
                        .is_some_and(|ext| ext.eq_ignore_ascii_case("log"));
                    if !filename.starts_with(prefix_str)
                        || filename.len() <= prefix_str.len()
                        || !has_log_ext
                    {
                        return None;
                    }
                    entry
                        .modified()
                        .map(|modified| (entry.path().to_path_buf(), modified))
                })
                .max_by_key(|(_, time)| *time)
                .map(|(path, _)| path)
        })
}

/// Read the content of the most recent log file matching a prefix.
///
/// This is a convenience function that combines `find_most_recent_logfile`
/// with reading the file content.
///
/// # Arguments
///
/// * `log_prefix` - The prefix path (e.g., ".`agent/logs/planning_1`")
/// * `workspace` - The workspace to read from
///
/// # Returns
///
/// The content of the most recent matching log file, or an empty string if not found.
pub fn read_most_recent_logfile(log_prefix: &Path, workspace: &dyn Workspace) -> String {
    find_most_recent_logfile(log_prefix, workspace)
        .and_then(|path| workspace.read(&path).ok())
        .unwrap_or_default()
}
