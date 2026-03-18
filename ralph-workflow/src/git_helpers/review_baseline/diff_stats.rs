// Diff statistics and baseline summary

/// Diff statistics for the changes since baseline.
#[derive(Debug, Clone, Default)]
pub struct DiffStats {
    /// Number of files changed.
    pub files_changed: usize,
    /// Number of lines added.
    pub lines_added: usize,
    /// Number of lines deleted.
    pub lines_deleted: usize,
    /// List of changed file paths (up to 10 for display).
    pub changed_files: Vec<String>,
}

/// Baseline summary information for display.
#[derive(Debug, Clone)]
pub struct BaselineSummary {
    /// The baseline OID (short form).
    pub baseline_oid: Option<String>,
    /// Number of commits since baseline.
    pub commits_since: usize,
    /// Whether the baseline is stale (>10 commits behind).
    pub is_stale: bool,
    /// Diff statistics for changes since baseline.
    pub diff_stats: DiffStats,
}

impl BaselineSummary {
    /// Format a compact version for inline display.
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

    /// Format a detailed version for verbose display.
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
                "  ⚠ WARNING: Baseline is stale. Consider updating with --reset-start-commit."
                    .to_string(),
            ]
        } else {
            Vec::new()
        };

        let lines: Vec<String> = std::iter::once("Review Baseline Summary:".to_string())
            .chain(std::iter::once("─".repeat(40)))
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

/// Get a summary of the baseline state for display.
///
/// Returns a `BaselineSummary` containing information about the current
/// baseline, commits since baseline, staleness, and diff statistics.
///
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_baseline_summary() -> io::Result<BaselineSummary> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    get_baseline_summary_impl(&repo, load_review_baseline()?)
}

/// Implementation of `get_baseline_summary`.
fn get_baseline_summary_impl(
    repo: &git2::Repository,
    baseline: ReviewBaseline,
) -> io::Result<BaselineSummary> {
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

    // Get diff statistics
    let diff_stats = get_diff_stats(repo, baseline_oid.as_ref())?;

    Ok(BaselineSummary {
        baseline_oid,
        commits_since,
        is_stale,
        diff_stats,
    })
}

/// Count lines in a blob content.
///
/// Returns the number of lines, matching the behavior of counting
/// newlines and adding 1 (so empty content returns 0, but any content
/// returns at least 1).
fn count_lines_in_blob(content: &[u8]) -> usize {
    if content.is_empty() {
        return 0;
    }
    // Count newlines and add 1 to get the line count
    // This matches the previous behavior and ensures that even files
    // without trailing newlines are counted correctly
    content.iter().copied().filter(|&c| c == b'\n').count() + 1
}

/// Get diff statistics for changes since the baseline.
fn get_diff_stats(repo: &git2::Repository, baseline_oid: Option<&String>) -> io::Result<DiffStats> {
    let baseline_tree = match baseline_oid {
        Some(oid) => {
            let oid = git2::Oid::from_str(oid).map_err(|_| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!("Invalid baseline OID: {oid}"),
                )
            })?;
            let commit = repo.find_commit(oid).map_err(|e| to_io_error(&e))?;
            commit.tree().map_err(|e| to_io_error(&e))?
        }
        None => {
            // No baseline set, use empty tree
            repo.find_tree(git2::Oid::zero())
                .map_err(|e| to_io_error(&e))?
        }
    };

    // Get the current HEAD tree
    let head_tree = match repo.head() {
        Ok(head) => {
            let commit = head.peel_to_commit().map_err(|e| to_io_error(&e))?;
            commit.tree().map_err(|e| to_io_error(&e))?
        }
        Err(_) => {
            // No HEAD yet, use empty tree
            repo.find_tree(git2::Oid::zero())
                .map_err(|e| to_io_error(&e))?
        }
    };

    // Generate diff
    let diff = repo
        .diff_tree_to_tree(Some(&baseline_tree), Some(&head_tree), None)
        .map_err(|e| to_io_error(&e))?;

    // First collect deltas and their info into a collection
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

    // Now compute stats from the collected deltas
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
