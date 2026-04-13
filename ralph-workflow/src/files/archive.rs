//! JSON artifact archiving utilities.
//!
//! Provides functions for archiving processed JSON artifact files
//! by renaming them to `.processed` suffixes.

use crate::workspace::Workspace;
use std::path::Path;

/// Archive JSON artifact files by renaming them to `.json.processed`.
///
/// Archives both the main artifact file (`{type}.json`) and the partial
/// artifact file (`{type}.partial.json`) if they exist.
pub fn archive_json_artifact_with_workspace(workspace: &dyn Workspace, artifact_type: &str) {
    let json_path = Path::new(".agent/tmp").join(format!("{artifact_type}.json"));
    if workspace.exists(&json_path) {
        let processed = json_path.with_extension("json.processed");
        let _ = workspace.rename(&json_path, &processed);
    }
    let partial_path = Path::new(".agent/tmp").join(format!("{artifact_type}.partial.json"));
    if workspace.exists(&partial_path) {
        let processed = partial_path.with_extension("partial.json.processed");
        let _ = workspace.rename(&partial_path, &processed);
    }
}
