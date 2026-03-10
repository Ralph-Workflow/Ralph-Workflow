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
    /// This method scans two areas for evidence of recent agent work:
    ///
    /// 1. **`.agent/` whitelist** – files representing meaningful AI progress
    ///    (PLAN.md, ISSUES.md, NOTES.md, STATUS.md, commit-message.txt,
    ///    `.agent/tmp/*.xml`).
    /// 2. **Workspace root scan (1 level deep)** – any file outside excluded
    ///    noise directories (`.git/`, `target/`, `tmp/`, `node_modules/`,
    ///    `.agent/`) and excluded extensions (`*.log`, `*.swp`, `*.tmp`,
    ///    `*.bak`, `*~`). This detects coding work (source edits, test writes,
    ///    `Cargo.toml` changes) that produces no stdout/stderr output.
    ///
    /// Returns `Ok(true)` if recent activity is detected, `Ok(false)` if no
    /// recent activity, or `Err` if a required directory read fails.
    ///
    /// # Arguments
    ///
    /// * `workspace` - The workspace to read files from
    /// * `timeout` - The recency window (typically 300 seconds)
    ///
    /// # Errors
    ///
    /// Returns error if the `.agent/` directory exists but cannot be read.
    pub fn check_for_recent_activity(
        &self,
        workspace: &dyn Workspace,
        timeout: Duration,
    ) -> std::io::Result<bool> {
        let now = SystemTime::now();
        let agent_dir = Path::new(".agent");

        // Check .agent/ whitelist if the directory exists.
        if workspace.exists(agent_dir) {
            let entries = workspace.read_dir(agent_dir)?;

            for entry in &entries {
                if !entry.is_file() {
                    continue;
                }
                let path = entry.path();
                if !Self::is_ai_generated_file(path) {
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

        // Also check .agent/tmp/ for XML artifacts.
        let tmp_dir = Path::new(".agent/tmp");
        if workspace.exists(tmp_dir) {
            if let Ok(tmp_entries) = workspace.read_dir(tmp_dir) {
                for entry in tmp_entries {
                    if !entry.is_file() {
                        continue;
                    }
                    let path = entry.path();
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

        // Scan workspace root (1 level deep) for recently modified source files.
        // This catches genuine coding work (src/ edits, test writes, Cargo.toml
        // changes) that produces no stdout/stderr output. Errors are soft-failed:
        // if a directory cannot be read, simply skip it.
        if let Ok(root_entries) = workspace.read_dir(Path::new("")) {
            for entry in root_entries {
                if entry.is_file() {
                    let path = entry.path();
                    if Self::is_excluded_workspace_file(path) {
                        continue;
                    }
                    let Some(mtime) = entry.modified() else {
                        continue;
                    };
                    let age = now.duration_since(mtime).unwrap_or(Duration::MAX);
                    if age < timeout {
                        return Ok(true);
                    }
                } else if entry.is_dir() {
                    let path = entry.path();
                    if Self::is_excluded_workspace_dir(path) {
                        continue;
                    }
                    if let Ok(sub_entries) = workspace.read_dir(path) {
                        for sub_entry in sub_entries {
                            if !sub_entry.is_file() {
                                continue;
                            }
                            let sub_path = sub_entry.path();
                            if Self::is_excluded_workspace_file(sub_path) {
                                continue;
                            }
                            let Some(mtime) = sub_entry.modified() else {
                                continue;
                            };
                            let age = now.duration_since(mtime).unwrap_or(Duration::MAX);
                            if age < timeout {
                                return Ok(true);
                            }
                        }
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

    /// Check if a workspace-root directory should be excluded from the activity scan.
    ///
    /// Excludes directories that contain noise or are handled elsewhere:
    /// - `.git/` – version-control metadata
    /// - `target/` – Cargo build artifacts
    /// - `tmp/` – temporary files
    /// - `node_modules/` – npm dependencies
    /// - `.agent/` – already handled by the dedicated whitelist scan above
    fn is_excluded_workspace_dir(path: &Path) -> bool {
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            return false;
        };
        matches!(name, ".git" | "target" | "tmp" | "node_modules" | ".agent")
    }

    /// Check if a workspace file should be excluded from the activity scan.
    ///
    /// Excludes file types that represent noise rather than productive work:
    /// - `*.log` – log output, append-only
    /// - `*.swp` – Vim swap files
    /// - `*.tmp` – generic temporaries
    /// - `*.bak` – backup copies
    /// - `*~` – editor backup suffix
    fn is_excluded_workspace_file(path: &Path) -> bool {
        let has_excluded_ext = path.extension().is_some_and(|ext| {
            ext.eq_ignore_ascii_case("log")
                || ext.eq_ignore_ascii_case("swp")
                || ext.eq_ignore_ascii_case("tmp")
                || ext.eq_ignore_ascii_case("bak")
        });
        let file_name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        has_excluded_ext || file_name.ends_with('~')
    }
}

impl Default for FileActivityTracker {
    fn default() -> Self {
        Self::new()
    }
}
