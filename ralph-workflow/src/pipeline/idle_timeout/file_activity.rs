//! File activity tracking for timeout detection.
//!
//! This module provides infrastructure to detect when an agent is actively
//! writing files, even when there's minimal stdout/stderr output. This prevents
//! false timeout kills when agents are making progress through file updates.

use crate::workspace::Workspace;
use std::path::Path;
use std::time::{Duration, SystemTime};

/// Maximum depth for recursive workspace scan.
///
/// Depth 0 = workspace root files only (no recursion).
/// Depth 1 = workspace root subdirectory files (previous behaviour).
/// Depth 8 = covers standard Rust workspace layouts (crate/src/module/submod/…).
const MAX_SCAN_DEPTH: usize = 8;

/// Recursively scan a directory for recently modified, non-noise files.
///
/// Returns `Ok(true)` as soon as a file younger than `timeout` is found.
/// Excluded directories and extensions are skipped at every level.
/// `remaining_depth` bounds worst-case traversal to prevent hangs on deep trees.
///
/// `#[inline(never)]` prevents this function from being merged into its caller's
/// stack frame, keeping each frame independently bounded.
#[inline(never)]
fn scan_dir_recursive(
    workspace: &dyn Workspace,
    dir: &Path,
    now: SystemTime,
    timeout: Duration,
    remaining_depth: usize,
) -> std::io::Result<bool> {
    if remaining_depth == 0 {
        return Ok(false);
    }
    let Ok(entries) = workspace.read_dir(dir) else {
        return Ok(false); // soft-fail: unreadable dirs are skipped
    };
    for entry in entries {
        let path = entry.path();
        if entry.is_file() {
            if FileActivityTracker::is_excluded_workspace_file(path) {
                continue;
            }
            if let Some(mtime) = entry.modified() {
                let age = now.duration_since(mtime).unwrap_or(Duration::MAX);
                if age <= timeout {
                    return Ok(true);
                }
            }
        } else if entry.is_dir() {
            if FileActivityTracker::is_excluded_workspace_dir(path) {
                continue;
            }
            if scan_dir_recursive(workspace, entry.path(), now, timeout, remaining_depth - 1)? {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

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
    /// 2. **Workspace recursive scan (max depth 8)** – any file outside excluded
    ///    noise directories (`.git/`, `target/`, `tmp/`, `node_modules/`,
    ///    `.agent/`) and excluded extensions (`*.log`, `*.swp`, `*.tmp`,
    ///    `*.bak`, `*~`). This detects coding work (source edits, test writes,
    ///    `Cargo.toml` changes) that produces no stdout/stderr output, including
    ///    files nested deeply inside workspace crates (e.g. `crate/src/mod/file.rs`).
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
                if age <= timeout {
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
                    if age <= timeout {
                        return Ok(true);
                    }
                }
            }
        }

        // Recursively scan workspace for recently modified source files.
        // Excludes noise directories (.git, target, tmp, node_modules, .agent)
        // and noise extensions (*.log, *.swp, *.tmp, *.bak, *~).
        // Short-circuits on first match for performance.
        // The .agent/ directory is excluded here; it is handled above.
        if scan_dir_recursive(workspace, Path::new(""), now, timeout, MAX_SCAN_DEPTH)? {
            return Ok(true);
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
