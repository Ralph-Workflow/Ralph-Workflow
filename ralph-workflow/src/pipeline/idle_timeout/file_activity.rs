//! File activity tracking for timeout detection.
//!
//! This module provides infrastructure to detect when an agent is actively
//! writing files, even when there's minimal stdout/stderr output. This prevents
//! false timeout kills when agents are making progress through file updates.
//!
//! Only `.agent/` directory files (PLAN.md, ISSUES.md, NOTES.md, STATUS.md,
//! commit-message.txt) and `.agent/tmp/*.xml` are checked. The workspace-wide
//! recursive scan was removed because it caused false positives: cargo build
//! artifacts, editor temp files, and other unrelated file modifications would
//! suppress the idle timeout even though they are not evidence of agent progress.

use crate::workspace::Workspace;
use std::path::Path;
use std::time::{Duration, SystemTime};

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
        now: SystemTime,
    ) -> std::io::Result<bool> {
        check_for_recent_activity_with_time(workspace, timeout, now)
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
}

impl Default for FileActivityTracker {
    fn default() -> Self {
        Self::new()
    }
}

fn file_age(now: SystemTime, mtime: SystemTime) -> Duration {
    now.duration_since(mtime).unwrap_or(Duration::ZERO)
}

fn check_for_recent_activity_with_time(
    workspace: &dyn Workspace,
    timeout: Duration,
    now: SystemTime,
) -> std::io::Result<bool> {
    let agent_dir = Path::new(".agent");

    if workspace.exists(agent_dir) {
        let entries = workspace.read_dir(agent_dir)?;

        let has_recent_activity = entries
            .into_iter()
            .filter(|entry| entry.is_file())
            .filter_map(|entry| {
                let path = entry.path();
                if !FileActivityTracker::is_ai_generated_file(path) {
                    return None;
                }
                entry.modified().map(|mtime| (path.to_path_buf(), mtime))
            })
            .any(|(_, mtime)| file_age(now, mtime) <= timeout);

        if has_recent_activity {
            return Ok(true);
        }
    }

    let tmp_dir = Path::new(".agent/tmp");
    if workspace.exists(tmp_dir) {
        if let Ok(tmp_entries) = workspace.read_dir(tmp_dir) {
            let has_recent_xml = tmp_entries
                .into_iter()
                .filter(|entry| entry.is_file())
                .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "xml"))
                .filter_map(|entry| entry.modified())
                .any(|mtime| file_age(now, mtime) <= timeout);

            if has_recent_xml {
                return Ok(true);
            }
        }
    }

    Ok(false)
}
