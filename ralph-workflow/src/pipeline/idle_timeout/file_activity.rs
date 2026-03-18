//! File activity tracking for timeout detection.
//!
//! This module provides infrastructure to detect when an agent is actively
//! writing files, even when there's minimal stdout/stderr output. This prevents
//! false timeout kills when agents are making progress through file updates.

use crate::workspace::Workspace;
use std::path::Path;
use std::time::Duration;

pub struct FileActivityTracker {
    _private: (),
}

impl FileActivityTracker {
    #[must_use]
    pub const fn new() -> Self {
        Self { _private: () }
    }

    pub fn check_for_recent_activity(
        &self,
        workspace: &dyn Workspace,
        timeout: Duration,
    ) -> std::io::Result<bool> {
        super::runtime::file_activity::check_for_recent_activity(workspace, timeout)
    }

    fn is_ai_generated_file(path: &Path) -> bool {
        let Some(file_name) = path.file_name().and_then(|n| n.to_str()) else {
            return false;
        };

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

        matches!(
            file_name,
            "PLAN.md" | "ISSUES.md" | "NOTES.md" | "STATUS.md" | "commit-message.txt"
        )
    }

    pub(super) fn is_excluded_workspace_dir(path: &Path) -> bool {
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            return false;
        };
        matches!(name, ".git" | "target" | "tmp" | "node_modules" | ".agent")
    }

    pub(super) fn is_excluded_workspace_file(path: &Path) -> bool {
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
