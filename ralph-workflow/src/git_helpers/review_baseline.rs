//! Per-review-cycle baseline tracking.
//!
//! This module manages the baseline commit for each review cycle, ensuring that
//! reviewers only see changes from the current cycle rather than cumulative changes
//! from previous fix commits.
//!
//! # Overview
//!
//! During the review-fix phase, each cycle should:
//! 1. Capture baseline before review (current HEAD)
//! 2. Review sees diff from that baseline
//! 3. Fixer makes changes (reviewer agent by default)
//! 4. Baseline is updated after fix pass
//! 5. Next review cycle sees only new changes
//!
//! This prevents "diff scope creep" where previous fix commits pollute
//! subsequent review passes.

use std::path::Path;

use crate::workspace::{Workspace, WorkspaceFs};

mod iot {
    pub(crate) type Result<T> = std::io::Result<T>;
    pub(crate) type Error = std::io::Error;
    pub(crate) type ErrorKind = std::io::ErrorKind;
}

// Boundary module for libgit2 revwalk operations.
include!("review_baseline/io.rs");

use super::start_commit::get_current_head_oid;

// =============================================================================
// Baseline Persistence (from review_baseline/baseline_persistence.rs)
// =============================================================================

pub const REVIEW_BASELINE_FILE: &str = ".agent/review_baseline.txt";
pub const BASELINE_NOT_SET: &str = "__BASELINE_NOT_SET__";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReviewBaseline {
    Commit(git2::Oid),
    NotSet,
}

pub fn load_review_baseline() -> iot::Result<ReviewBaseline> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    let repo_root = repo
        .workdir()
        .ok_or_else(|| iot::Error::new(iot::ErrorKind::NotFound, "No workdir for repository"))?;
    let workspace = WorkspaceFs::new(repo_root.to_path_buf());
    load_review_baseline_with_workspace(&workspace)
}

pub fn load_review_baseline_with_workspace(
    workspace: &dyn Workspace,
) -> iot::Result<ReviewBaseline> {
    let path = Path::new(REVIEW_BASELINE_FILE);
    if !workspace.exists(path) {
        return Ok(ReviewBaseline::NotSet);
    }

    let content = workspace.read(path)?;
    let raw = content.trim();

    if raw.is_empty() || raw == BASELINE_NOT_SET {
        return Ok(ReviewBaseline::NotSet);
    }

    let oid = git2::Oid::from_str(raw).map_err(|_| {
        iot::Error::new(
            iot::ErrorKind::InvalidData,
            format!("Invalid baseline OID in {REVIEW_BASELINE_FILE}: '{raw}'"),
        )
    })?;

    Ok(ReviewBaseline::Commit(oid))
}

pub fn update_review_baseline() -> iot::Result<()> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    let repo_root = repo
        .workdir()
        .ok_or_else(|| iot::Error::new(iot::ErrorKind::NotFound, "No workdir for repository"))?;
    let workspace = WorkspaceFs::new(repo_root.to_path_buf());
    update_review_baseline_with_workspace(&workspace)
}

pub fn update_review_baseline_with_workspace(workspace: &dyn Workspace) -> iot::Result<()> {
    let path = Path::new(REVIEW_BASELINE_FILE);
    match get_current_head_oid() {
        Ok(oid) => workspace.write(path, oid.trim()),
        Err(e) if e.kind() == iot::ErrorKind::NotFound => workspace.write(path, BASELINE_NOT_SET),
        Err(e) => Err(e),
    }
}

pub fn get_review_baseline_info() -> iot::Result<(Option<String>, usize, bool)> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    match load_review_baseline()? {
        ReviewBaseline::Commit(oid) => {
            let oid_str = oid.to_string();
            let commits_since = count_commits_since(&repo, &oid_str)?;
            let is_stale = commits_since > 10;
            Ok((Some(oid_str), commits_since, is_stale))
        }
        ReviewBaseline::NotSet => Ok((None, 0, false)),
    }
}

fn count_commits_since(repo: &git2::Repository, baseline_oid: &str) -> iot::Result<usize> {
    let baseline = git2::Oid::from_str(baseline_oid).map_err(|_| {
        iot::Error::new(
            iot::ErrorKind::InvalidInput,
            format!("Invalid baseline OID: {baseline_oid}"),
        )
    })?;

    let head_oid = match repo.head() {
        Ok(head) => head.peel_to_commit().map_err(|e| to_io_error(&e))?.id(),
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => return Ok(0),
        Err(e) => return Err(to_io_error(&e)),
    };

    if let Ok((ahead, _behind)) = repo.graph_ahead_behind(head_oid, baseline) {
        return Ok(ahead);
    }

    revwalk_count_commits(repo, head_oid, baseline)
}

fn to_io_error(err: &git2::Error) -> iot::Error {
    iot::Error::other(err.to_string())
}

// =============================================================================
// Diff Stats (from review_baseline/diff_stats.rs)
// =============================================================================

#[derive(Debug, Clone, Default)]
pub struct DiffStats {
    pub files_changed: usize,
    pub lines_added: usize,
    pub lines_deleted: usize,
    pub changed_files: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct BaselineSummary {
    pub baseline_oid: Option<String>,
    pub commits_since: usize,
    pub is_stale: bool,
    pub diff_stats: DiffStats,
}

impl BaselineSummary {
    pub fn format_compact(&self) -> String {
        self.baseline_oid.as_ref().map_or_else(
            || {
                format!(
                    "Baseline: start_commit ({} files: +{}/-{} lines)",
                    self.diff_stats.files_changed,
                    self.diff_stats.lines_added,
                    self.diff_stats.lines_deleted
                )
            },
            |oid| {
                let short_oid = &oid[..8.min(oid.len())];
                if self.is_stale {
                    format!(
                        "Baseline: {} (+{} commits since, {} files changed)",
                        short_oid, self.commits_since, self.diff_stats.files_changed
                    )
                } else if self.commits_since > 0 {
                    format!(
                        "Baseline: {} ({} commits since, {} files changed)",
                        short_oid, self.commits_since, self.diff_stats.files_changed
                    )
                } else {
                    format!(
                        "Baseline: {} ({} files: +{}/-{} lines)",
                        short_oid,
                        self.diff_stats.files_changed,
                        self.diff_stats.lines_added,
                        self.diff_stats.lines_deleted
                    )
                }
            },
        )
    }

    pub fn format_detailed(&self) -> String {
        let baseline_info: Vec<String> = match &self.baseline_oid {
            Some(oid) => {
                let short_oid = &oid[..8.min(oid.len())];
                let lines = vec![format!("  Commit: {short_oid}")];
                if self.commits_since > 0 {
                    lines
                        .into_iter()
                        .chain(std::iter::once(format!(
                            "  Commits since baseline: {}",
                            self.commits_since
                        )))
                        .collect()
                } else {
                    lines
                }
            }
            None => vec!["  Commit: start_commit (initial baseline)".to_string()],
        };

        let file_info: Vec<String> = if !self.diff_stats.changed_files.is_empty() {
            let file_lines: Vec<String> = self
                .diff_stats
                .changed_files
                .iter()
                .map(|file| format!("    - {file}"))
                .collect();
            let remaining = self.diff_stats.files_changed - self.diff_stats.changed_files.len();
            let remaining_line = (remaining > 0).then(|| format!("    ... and {remaining} more"));
            std::iter::once(String::new())
                .chain(std::iter::once("  Changed files:".to_string()))
                .chain(file_lines)
                .chain(remaining_line)
                .collect()
        } else {
            Vec::new()
        };

        let stale_warning: Vec<String> = if self.is_stale {
            vec![
                String::new(),
                "  WARNING: Baseline is stale. Consider updating with --reset-start-commit."
                    .to_string(),
            ]
        } else {
            Vec::new()
        };

        let lines: Vec<String> = std::iter::once("Review Baseline Summary:".to_string())
            .chain(std::iter::once("".to_string()))
            .chain(baseline_info)
            .chain(std::iter::once(format!(
                "  Files changed: {}",
                self.diff_stats.files_changed
            )))
            .chain(std::iter::once(format!(
                "  Lines added: {}",
                self.diff_stats.lines_added
            )))
            .chain(std::iter::once(format!(
                "  Lines deleted: {}",
                self.diff_stats.lines_deleted
            )))
            .chain(file_info)
            .chain(stale_warning)
            .collect();

        lines.join("\n")
    }
}

pub fn get_baseline_summary() -> iot::Result<BaselineSummary> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    get_baseline_summary_impl(&repo, load_review_baseline()?)
}

fn get_baseline_summary_impl(
    repo: &git2::Repository,
    baseline: ReviewBaseline,
) -> iot::Result<BaselineSummary> {
    let baseline_oid = match baseline {
        ReviewBaseline::Commit(oid) => Some(oid.to_string()),
        ReviewBaseline::NotSet => None,
    };

    let commits_since = if let Some(ref oid) = baseline_oid {
        count_commits_since(repo, oid)?
    } else {
        0
    };

    let is_stale = commits_since > 10;

    let diff_stats = get_diff_stats(repo, baseline_oid.as_ref())?;

    Ok(BaselineSummary {
        baseline_oid,
        commits_since,
        is_stale,
        diff_stats,
    })
}

fn count_lines_in_blob(content: &[u8]) -> usize {
    if content.is_empty() {
        return 0;
    }
    content.iter().copied().filter(|&c| c == b'\n').count() + 1
}

fn get_diff_stats(
    repo: &git2::Repository,
    baseline_oid: Option<&String>,
) -> iot::Result<DiffStats> {
    let baseline_tree = match baseline_oid {
        Some(oid) => {
            let oid = git2::Oid::from_str(oid).map_err(|_| {
                iot::Error::new(
                    iot::ErrorKind::InvalidInput,
                    format!("Invalid baseline OID: {oid}"),
                )
            })?;
            let commit = repo.find_commit(oid).map_err(|e| to_io_error(&e))?;
            commit.tree().map_err(|e| to_io_error(&e))?
        }
        None => repo
            .find_tree(git2::Oid::zero())
            .map_err(|e| to_io_error(&e))?,
    };

    let head_tree = match repo.head() {
        Ok(head) => {
            let commit = head.peel_to_commit().map_err(|e| to_io_error(&e))?;
            commit.tree().map_err(|e| to_io_error(&e))?
        }
        Err(_) => repo
            .find_tree(git2::Oid::zero())
            .map_err(|e| to_io_error(&e))?,
    };

    let diff = repo
        .diff_tree_to_tree(Some(&baseline_tree), Some(&head_tree), None)
        .map_err(|e| to_io_error(&e))?;

    #[derive(Debug, Clone)]
    struct DeltaInfo {
        path: Option<String>,
        is_added_or_modified: bool,
        blob_id: git2::Oid,
    }

    let deltas: Vec<DeltaInfo> = diff
        .deltas()
        .filter_map(|delta| {
            use git2::Delta;

            let path = delta
                .new_file()
                .path()
                .or(delta.old_file().path())
                .map(|p: &std::path::Path| p.to_string_lossy().to_string());

            let (is_new_or_modified, blob_id) = match delta.status() {
                Delta::Added | Delta::Modified => (true, delta.new_file().id()),
                Delta::Deleted => (false, delta.old_file().id()),
                _ => return None,
            };

            Some(DeltaInfo {
                path,
                is_added_or_modified: is_new_or_modified,
                blob_id,
            })
        })
        .collect();

    let files_changed = deltas.len();
    let changed_files: Vec<String> = deltas
        .iter()
        .filter_map(|d| d.path.clone())
        .take(10)
        .collect();

    let (lines_added, lines_deleted) = deltas
        .iter()
        .filter_map(|d| {
            repo.find_blob(d.blob_id)
                .ok()
                .map(|blob| (d.is_added_or_modified, count_lines_in_blob(blob.content())))
        })
        .fold((0usize, 0usize), |(add, del), (is_new, count)| {
            if is_new {
                (add.saturating_add(count), del)
            } else {
                (add, del.saturating_add(count))
            }
        });

    let stats = DiffStats {
        files_changed,
        lines_added,
        lines_deleted,
        changed_files,
    };

    Ok(stats)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_review_baseline_file_path_defined() {
        assert_eq!(REVIEW_BASELINE_FILE, ".agent/review_baseline.txt");
    }

    #[test]
    fn test_load_review_baseline_with_workspace_not_set() {
        use crate::workspace::MemoryWorkspace;

        let workspace = MemoryWorkspace::new_test();

        let result = load_review_baseline_with_workspace(&workspace).unwrap();
        assert_eq!(result, ReviewBaseline::NotSet);
    }

    #[test]
    fn test_load_review_baseline_with_workspace_sentinel() {
        use crate::workspace::MemoryWorkspace;

        let workspace =
            MemoryWorkspace::new_test().with_file(".agent/review_baseline.txt", BASELINE_NOT_SET);

        let result = load_review_baseline_with_workspace(&workspace).unwrap();
        assert_eq!(result, ReviewBaseline::NotSet);
    }

    #[test]
    fn test_load_review_baseline_with_workspace_empty() {
        use crate::workspace::MemoryWorkspace;

        let workspace = MemoryWorkspace::new_test().with_file(".agent/review_baseline.txt", "");

        let result = load_review_baseline_with_workspace(&workspace).unwrap();
        assert_eq!(result, ReviewBaseline::NotSet);
    }

    #[test]
    fn test_load_review_baseline_with_workspace_valid_oid() {
        use crate::workspace::MemoryWorkspace;

        let workspace = MemoryWorkspace::new_test().with_file(
            ".agent/review_baseline.txt",
            "abcd1234abcd1234abcd1234abcd1234abcd1234",
        );

        let result = load_review_baseline_with_workspace(&workspace).unwrap();
        let expected_oid = git2::Oid::from_str("abcd1234abcd1234abcd1234abcd1234abcd1234").unwrap();
        assert_eq!(result, ReviewBaseline::Commit(expected_oid));
    }

    #[test]
    fn test_load_review_baseline_with_workspace_invalid_oid() {
        use crate::workspace::MemoryWorkspace;

        let workspace =
            MemoryWorkspace::new_test().with_file(".agent/review_baseline.txt", "invalid");

        let result = load_review_baseline_with_workspace(&workspace);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().kind(), iot::ErrorKind::InvalidData);
    }
}
