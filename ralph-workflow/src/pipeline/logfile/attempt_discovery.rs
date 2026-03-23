//! Attempt index discovery for log files.
//!
//! Provides functions to scan directories and determine the next available
//! attempt index for log files, preventing filename collisions.

use crate::workspace::Workspace;
use std::path::Path;

use super::naming::sanitize_agent_name;

/// Determine the next attempt index for a given `(prefix, agent, model_index)` logfile family.
///
/// This scans the parent directory for existing log files matching:
///
/// `{prefix_filename}_{agent}_{model_index}_a{attempt}.log`
///
/// and returns `max(attempt)+1`, or `0` if no matching files exist.
///
/// This avoids collisions when attempt numbers are computed from multiple counters
/// (retry cycles, continuation attempts, XSD retry count) that may exceed assumed bounds.
pub fn next_logfile_attempt_index(
    log_prefix: &Path,
    agent_name: &str,
    model_index: usize,
    workspace: &dyn Workspace,
) -> u32 {
    let parent = log_prefix.parent().unwrap_or_else(|| Path::new("."));
    let prefix_filename = match log_prefix.file_name().and_then(|s| s.to_str()) {
        Some(s) if !s.is_empty() => s,
        _ => return 0,
    };

    let safe_agent = sanitize_agent_name(&agent_name.to_lowercase());
    let start = format!("{prefix_filename}_{safe_agent}_{model_index}_a");

    let max_attempt =
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
                        let filename = entry.file_name().and_then(|s| s.to_str())?;
                        let has_log_ext = entry
                            .path()
                            .extension()
                            .is_some_and(|ext| ext.eq_ignore_ascii_case("log"));
                        if !filename.starts_with(&start) || !has_log_ext {
                            return None;
                        }

                        filename
                            .strip_suffix(".log")
                            .and_then(|without_ext| without_ext.strip_prefix(&start))
                            .and_then(|digits| {
                                if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_digit())
                                {
                                    None
                                } else {
                                    digits.parse::<u32>().ok()
                                }
                            })
                    })
                    .max()
            });

    max_attempt.map_or(0, |n| n.saturating_add(1))
}

/// Determine the next attempt index for simplified per-run agent logs.
///
/// This scans the agents/ subdirectory for existing log files matching:
///
/// - `{base_filename}.log` (the base file, first attempt)
/// - `{base_filename}_a{attempt}.log` (retry attempts)
///
/// and returns the next available attempt index. If the base file exists,
/// it returns 1 or greater; otherwise it returns 0.
///
/// This supports the per-run log directory structure where agent identity
/// is recorded in log file headers rather than filenames.
pub fn next_simplified_logfile_attempt_index(
    base_log_path: &Path,
    workspace: &dyn Workspace,
) -> u32 {
    let parent = base_log_path.parent().unwrap_or_else(|| Path::new("."));
    let base_filename = match base_log_path.file_stem().and_then(|s| s.to_str()) {
        Some(s) if !s.is_empty() => s,
        _ => return 0,
    };

    let start = format!("{base_filename}_a");
    let base_log_name = format!("{base_filename}.log");

    let (max_attempt, base_file_exists) = workspace
        .read_dir(parent)
        .ok()
        .map(|entries: Vec<crate::workspace::DirEntry>| {
            entries.into_iter().fold(
                (None, false),
                |(max_attempt, base_file_exists): (Option<u32>, bool),
                 entry: crate::workspace::DirEntry| {
                    if !entry.is_file() {
                        return (max_attempt, base_file_exists);
                    }
                    let Some(filename) = entry.file_name().and_then(|s| s.to_str()) else {
                        return (max_attempt, base_file_exists);
                    };

                    let is_base_file = filename == base_log_name;
                    let has_log_ext = entry
                        .path()
                        .extension()
                        .is_some_and(|ext| ext.eq_ignore_ascii_case("log"));
                    let attempt_digits_opt = if has_log_ext {
                        filename
                            .strip_suffix(".log")
                            .and_then(|without_ext| without_ext.strip_prefix(&start))
                    } else {
                        None
                    };

                    if is_base_file {
                        return (max_attempt, true);
                    }

                    let attempt_digits = match attempt_digits_opt {
                        Some(digits) => digits,
                        None => return (max_attempt, base_file_exists),
                    };

                    if attempt_digits.is_empty()
                        || !attempt_digits.chars().all(|c| c.is_ascii_digit())
                    {
                        return (max_attempt, base_file_exists);
                    }

                    attempt_digits.parse::<u32>().ok().map_or(
                        (max_attempt, base_file_exists),
                        |n| {
                            (
                                max_attempt.map_or(Some(n), |prev| Some(prev.max(n))),
                                base_file_exists,
                            )
                        },
                    )
                },
            )
        })
        .unwrap_or((None, false));

    max_attempt.map_or_else(|| u32::from(base_file_exists), |max| max.saturating_add(1))
}
