//! File activity tracking runtime implementation.
//!
//! This module contains the boundary code for file activity tracking.

use crate::pipeline::idle_timeout::FileActivityTracker;
use crate::workspace::Workspace;
use std::cell::Cell;
use std::path::Path;
use std::time::{Duration, SystemTime};

thread_local! {
    static WARNED: Cell<bool> = Cell::new(false);
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
    let entries = match workspace.read_dir(dir) {
        Ok(entries) => entries,
        Err(e) => {
            if is_root {
                return Err(e);
            }

            if !WARNED.with(|w| {
                if w.get() {
                    false
                } else {
                    w.set(true);
                    true
                }
            }) {
                eprintln!(
                    "Warning: workspace scan skipped unreadable directory '{}' ({e}); file-activity detection may be incomplete",
                    dir.display()
                );
            }

            return Ok(false);
        }
    };

    for entry in entries {
        let path = entry.path();
        if entry.is_file() {
            if FileActivityTracker::is_excluded_workspace_file(path) {
                continue;
            }
            if let Some(mtime) = entry.modified() {
                let age = file_age(now, mtime);
                if age <= timeout {
                    return Ok(true);
                }
            }
        } else if entry.is_dir() {
            if FileActivityTracker::is_excluded_workspace_dir(path) {
                continue;
            }
            if remaining_depth > 0
                && scan_dir_recursive(
                    workspace,
                    entry.path(),
                    now,
                    timeout,
                    remaining_depth - 1,
                    false,
                )?
            {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

pub(crate) const MAX_SCAN_DEPTH_CONST: usize = MAX_SCAN_DEPTH;
