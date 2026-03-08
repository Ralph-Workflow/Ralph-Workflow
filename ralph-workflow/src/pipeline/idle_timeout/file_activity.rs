//! File activity tracking for timeout detection.
//!
//! This module provides infrastructure to detect when an agent is actively
//! writing files, even when there's minimal stdout/stderr output. This prevents
//! false timeout kills when agents are making progress through file updates.

use crate::workspace::Workspace;
use std::path::Path;
use std::time::{Duration, SystemTime};

/// Tracks file modification activity for timeout detection.
///
/// This tracker monitors AI-generated files in the `.agent/` directory to detect
/// ongoing work that may not produce stdout/stderr output. It tracks modification
/// times and distinguishes meaningful AI progress from log churn and system artifacts.
pub struct FileActivityTracker {
    _private: (),
}

impl FileActivityTracker {
    /// Create a new file activity tracker.
    #[must_use]
    pub const fn new() -> Self {
        Self { _private: () }
    }

    /// Check if any AI-generated files have been modified within `timeout`.
    ///
    /// This method scans the `.agent/` directory for files that represent meaningful
    /// AI progress (PLAN.md, ISSUES.md, NOTES.md, commit-message.txt, .agent/tmp/*.xml)
    /// and checks if any have been modified recently.
    ///
    /// Returns `Ok(true)` if recent activity is detected, `Ok(false)` if no recent
    /// activity, or `Err` if the directory cannot be read.
    ///
    /// # Arguments
    ///
    /// * `workspace` - The workspace to read files from
    /// * `timeout` - The recency window (typically 300 seconds)
    ///
    /// # Excluded Files
    ///
    /// The following patterns are excluded from activity tracking:
    /// - `*.log` - Log files (append-only, not user-facing progress)
    /// - `checkpoint.json` - Internal state tracking
    /// - `start_commit` - One-time initialization artifact
    /// - `review_baseline.txt` - One-time baseline tracking
    /// - `logs-*/` - Log directories
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn check_for_recent_activity(
        &self,
        workspace: &dyn Workspace,
        timeout: Duration,
    ) -> std::io::Result<bool> {
        let agent_dir = Path::new(".agent");

        // If .agent directory doesn't exist, no activity
        if !workspace.exists(agent_dir) {
            return Ok(false);
        }

        let entries = workspace.read_dir(agent_dir)?;
        let now = SystemTime::now();

        for entry in entries {
            // Only check files, not directories
            if !entry.is_file() {
                continue;
            }

            let path = entry.path();

            // Skip non-AI-generated files
            if !Self::is_ai_generated_file(path) {
                continue;
            }

            // Get modification time
            let Some(mtime) = entry.modified() else {
                continue;
            };

            let age = now.duration_since(mtime).unwrap_or(Duration::MAX);
            // Recent activity detected
            if age < timeout {
                return Ok(true);
            }
        }

        // Also check .agent/tmp/ for XML artifacts
        let tmp_dir = Path::new(".agent/tmp");
        if workspace.exists(tmp_dir) {
            if let Ok(tmp_entries) = workspace.read_dir(tmp_dir) {
                for entry in tmp_entries {
                    if !entry.is_file() {
                        continue;
                    }

                    let path = entry.path();

                    // Only check .xml files in tmp/
                    if path.extension().is_none_or(|ext| ext != "xml") {
                        continue;
                    }

                    let Some(mtime) = entry.modified() else {
                        continue;
                    };

                    let age = now.duration_since(mtime).unwrap_or(Duration::MAX);

                    if age < timeout {
                        return Ok(true);
                    }
                }
            }
        }

        Ok(false)
    }

    /// Check if a path represents an AI-generated file that should be tracked.
    ///
    /// Includes:
    /// - PLAN.md
    /// - ISSUES.md
    /// - NOTES.md
    /// - STATUS.md
    /// - commit-message.txt
    ///
    /// Excludes:
    /// - *.log (log files)
    /// - checkpoint.json (internal state)
    /// - `start_commit` (initialization artifact)
    /// - `review_baseline.txt` (baseline tracking)
    /// - Temporary/editor files (.swp, .tmp, ~, .bak)
    fn is_ai_generated_file(path: &Path) -> bool {
        let Some(file_name) = path.file_name().and_then(|n| n.to_str()) else {
            return false;
        };

        // Exclude patterns
        let has_excluded_ext = path.extension().is_some_and(|ext| {
            ext.eq_ignore_ascii_case("log")
                || ext.eq_ignore_ascii_case("swp")
                || ext.eq_ignore_ascii_case("tmp")
                || ext.eq_ignore_ascii_case("bak")
        });

        if has_excluded_ext
            || file_name == "checkpoint.json"
            || file_name == "start_commit"
            || file_name == "review_baseline.txt"
            || file_name.ends_with('~')
        {
            return false;
        }

        // Include patterns - AI-generated artifacts
        matches!(
            file_name,
            "PLAN.md" | "ISSUES.md" | "NOTES.md" | "STATUS.md" | "commit-message.txt"
        )
    }
}

impl Default for FileActivityTracker {
    fn default() -> Self {
        Self::new()
    }
}
