//! File activity tracking for timeout detection.
//!
//! This module provides infrastructure to detect when an agent is actively
//! writing files, even when there's minimal stdout/stderr output. This prevents
//! false timeout kills when agents are making progress through file updates.

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
    ) -> std::io::Result<bool> {
        check_for_recent_activity(workspace, timeout)
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

#[derive(Clone, Copy)]
struct ScanState {
    found_recent_activity: bool,
    warned_unreadable_dir: bool,
}

fn file_age(now: SystemTime, mtime: SystemTime) -> Duration {
    now.duration_since(mtime).unwrap_or(Duration::ZERO)
}

const MAX_SCAN_DEPTH: usize = 8;

#[inline(never)]
#[expect(
    clippy::print_stderr,
    reason = "diagnostic warning for filesystem issues"
)]
pub(crate) fn scan_dir_recursive(
    workspace: &dyn Workspace,
    dir: &Path,
    now: SystemTime,
    timeout: Duration,
    remaining_depth: usize,
    is_root: bool,
) -> std::io::Result<bool> {
    scan_dir_recursive_with_state(
        workspace,
        dir,
        now,
        timeout,
        remaining_depth,
        is_root,
        false,
    )
    .map(|state| state.found_recent_activity)
}

#[inline(never)]
#[expect(
    clippy::print_stderr,
    reason = "diagnostic warning for filesystem issues"
)]
fn scan_dir_recursive_with_state(
    workspace: &dyn Workspace,
    dir: &Path,
    now: SystemTime,
    timeout: Duration,
    remaining_depth: usize,
    is_root: bool,
    warned_unreadable_dir: bool,
) -> std::io::Result<ScanState> {
    let entries = match workspace.read_dir(dir) {
        Ok(entries) => entries,
        Err(e) => {
            if is_root {
                return Err(e);
            }

            if !warned_unreadable_dir {
                eprintln!(
                    "Warning: workspace scan skipped unreadable directory '{}' ({e}); file-activity detection may be incomplete",
                    dir.display()
                );
            }

            return Ok(ScanState {
                found_recent_activity: false,
                warned_unreadable_dir: true,
            });
        }
    };

    entries.into_iter().try_fold(
        ScanState {
            found_recent_activity: false,
            warned_unreadable_dir,
        },
        |state, entry| {
            if state.found_recent_activity {
                return Ok(state);
            }

            let path = entry.path();
            if entry.is_file() {
                let found_recent_activity = !FileActivityTracker::is_excluded_workspace_file(path)
                    && entry
                        .modified()
                        .is_some_and(|mtime| file_age(now, mtime) <= timeout);
                return Ok(ScanState {
                    found_recent_activity,
                    warned_unreadable_dir: state.warned_unreadable_dir,
                });
            }

            if entry.is_dir() {
                if FileActivityTracker::is_excluded_workspace_dir(path) {
                    return Ok(state);
                }

                return remaining_depth
                    .checked_sub(1)
                    .map_or(Ok(state), |remaining| {
                        scan_dir_recursive_with_state(
                            workspace,
                            entry.path(),
                            now,
                            timeout,
                            remaining,
                            false,
                            state.warned_unreadable_dir,
                        )
                    });
            }

            Ok(state)
        },
    )
}

pub(crate) const MAX_SCAN_DEPTH_CONST: usize = MAX_SCAN_DEPTH;

pub(crate) fn check_for_recent_activity_with_time(
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

    if scan_dir_recursive(
        workspace,
        Path::new(""),
        now,
        timeout,
        MAX_SCAN_DEPTH_CONST,
        true,
    )? {
        return Ok(true);
    }

    Ok(false)
}

pub(crate) fn check_for_recent_activity(
    workspace: &dyn Workspace,
    timeout: Duration,
) -> std::io::Result<bool> {
    check_for_recent_activity_with_time(workspace, timeout, SystemTime::now())
}
