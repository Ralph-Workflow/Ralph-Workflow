//! Native multi-pattern file scanner using the Aho-Corasick algorithm.
//!
//! Replaces 17 separate `rg` subprocess invocations with a single in-process scan
//! that reads each source directory exactly once and searches all patterns
//! simultaneously in O(n + m + z) time (Aho & Corasick, 1975).
//!
//! The key optimisation: checks that scan the same directories are grouped.
//! Within a group, one Aho-Corasick automaton is built from every pattern,
//! every file is read once, and matches are demultiplexed back to the
//! originating check after any per-check post-filters are applied.

use aho_corasick::{AhoCorasick, AhoCorasickBuilder, AhoCorasickKind};
use std::collections::HashMap;

pub use crate::io::native_scan_checks::NATIVE_SCAN_CHECKS;
pub use crate::io::native_scan_types::{
    LineIndex, MatchMode, NativeScanCheck, NativeScanCheckResult, NativeScanViolation,
};
use crate::io::string_search::critical_factorization;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

pub use crate::io::scanner_diagnostics::{scan_has_diagnostic_prefix, DiagnosticLevel};

use crate::domain::scan_policy::{
    classify_glob_entry, file_is_excluded, handle_match as domain_handle_match, GlobEntryAction,
    MatchContext,
};

// ── Internal type aliases ─────────────────────────────────────────────────────

/// Result type for a single directory group's file collection.
///
/// `Ok(sorted_files)` on success; `Err((failing_dir, error_message))` when a
/// `read_dir` call fails (directory traversal error surfaced as an explicit violation).
type GroupFilesResult = Result<Vec<PathBuf>, (PathBuf, String)>;

/// Map from directory-group key to `(group_label, file_collection_result)`.
type ScanGroupMap = HashMap<String, (String, GroupFilesResult)>;

// ── Public entry point ────────────────────────────────────────────────────────

/// Run all native scan checks against the repository root, emitting per-file scan progress.
///
/// `progress(check_name, info)` is called every 50 files processed, allowing
/// callers to surface scan throughput to the user.  The callback is called from
/// whichever thread first reaches the 50-file boundary, so it must be `Sync`.
pub fn run_native_scan_checks_reporting(
    repo_root: &Path,
    checks: &[NativeScanCheck],
    progress: &(dyn Fn(&str, &str) + Sync),
) -> Vec<NativeScanCheckResult> {
    let mut results = init_scan_results(checks);

    if checks.is_empty() {
        return results;
    }

    // Single-pass directory traversal: collect all files for each group at once.
    // Eliminates the previous double-traversal (count_scan_files + per-group collect
    // in scan_group_collect), halving directory I/O.
    // Reference: TAOCP emphasis on reducing I/O passes to O(1) when possible.
    let group_map = collect_scan_groups(repo_root, checks);

    let (total_files, group_summaries) = summarize_group_map(&group_map);
    report_group_progress(total_files, &group_summaries, progress);
    let progress_threshold = compute_progress_threshold(total_files);

    // Group check indices by (sorted directories + include_glob) key.
    // Sort for deterministic group ordering across runs (HashMap has non-deterministic
    // iteration order; sorting guards against flaky test failures).
    let work_items = build_group_work_items(checks, &group_map);

    let all_violations = execute_scan_work_items(checks, work_items, progress_threshold, progress);

    // Merge violations back into results (check indices are unique across groups).
    merge_scan_results(&mut results, all_violations);

    results
}

fn init_scan_results(checks: &[NativeScanCheck]) -> Vec<NativeScanCheckResult> {
    checks
        .iter()
        .map(|c| NativeScanCheckResult {
            check_name: c.name,
            passed: true,
            violations: Vec::new(),
        })
        .collect()
}

fn summarize_group_map(group_map: &ScanGroupMap) -> (usize, Vec<(String, usize)>) {
    let total_files: usize = group_map
        .values()
        .filter_map(|(_, r)| r.as_ref().ok())
        .map(|v| v.len())
        .sum();
    let mut group_summaries: Vec<(String, usize)> = group_map
        .values()
        .map(|(label, r)| (label.clone(), r.as_ref().map(|v| v.len()).unwrap_or(0)))
        .collect();
    group_summaries.sort_by(|(a, _), (b, _)| a.cmp(b));
    (total_files, group_summaries)
}

fn report_group_progress(
    total_files: usize,
    group_summaries: &[(String, usize)],
    progress: &(dyn Fn(&str, &str) + Sync),
) {
    if total_files == 0 {
        return;
    }

    progress(
        "native-scan",
        &format!(
            "{total_files} files across {} group(s)",
            group_summaries.len()
        ),
    );
    for (label, count) in group_summaries {
        progress("native-scan", &format!("  {label}: {count} files"));
    }
}

fn compute_progress_threshold(total_files: usize) -> usize {
    (total_files / 20).max(5)
}

fn build_group_work_items<'a>(
    checks: &[NativeScanCheck],
    group_map: &'a ScanGroupMap,
) -> Vec<(Vec<usize>, &'a GroupFilesResult)> {
    let mut groups: HashMap<String, Vec<usize>> = HashMap::new();
    for (idx, check) in checks.iter().enumerate() {
        groups
            .entry(directory_group_key(check))
            .or_default()
            .push(idx);
    }
    let mut groups_vec: Vec<(String, Vec<usize>)> = groups.into_iter().collect();
    groups_vec.sort_by(|(a, _), (b, _)| a.cmp(b));

    groups_vec
        .into_iter()
        .map(|(key, indices)| {
            let (_, files_result) = group_map.get(&key).expect("group key must exist in map");
            (indices, files_result)
        })
        .collect()
}

fn execute_scan_work_items(
    checks: &[NativeScanCheck],
    work_items: Vec<(Vec<usize>, &GroupFilesResult)>,
    progress_threshold: usize,
    progress: &(dyn Fn(&str, &str) + Sync),
) -> Vec<Vec<(usize, NativeScanViolation)>> {
    let files_done = AtomicUsize::new(0);
    let mut all_violations: Vec<Vec<(usize, NativeScanViolation)>> =
        Vec::with_capacity(work_items.len());

    std::thread::scope(|s| {
        let fd = &files_done;
        let handles: Vec<_> = work_items
            .into_iter()
            .map(|(check_indices, files_result)| {
                s.spawn(move || match files_result {
                    Ok(files) => scan_group_collect(
                        checks,
                        &check_indices,
                        files,
                        fd,
                        progress_threshold,
                        progress,
                    ),
                    Err((err_dir, err_msg)) => check_indices
                        .iter()
                        .copied()
                        .map(|ci| {
                            (
                                ci,
                                NativeScanViolation {
                                    file: err_dir.clone(),
                                    line_number: 1,
                                    line: err_msg.clone(),
                                },
                            )
                        })
                        .collect(),
                })
            })
            .collect();
        for handle in handles {
            all_violations.push(handle.join().expect("scan group thread panicked"));
        }
    });

    all_violations
}

fn merge_scan_results(
    results: &mut [NativeScanCheckResult],
    all_violations: Vec<Vec<(usize, NativeScanViolation)>>,
) {
    for group_violations in all_violations {
        for (ci, v) in group_violations {
            results[ci].passed = false;
            results[ci].violations.push(v);
        }
    }
}

// ── Internal helpers ──────────────────────────────────────────────────────────

/// Single-pass directory traversal: collect files for all directory groups at once.
///
/// Replaces the previous two-pass approach (`count_scan_files` for counting +
/// per-group traversal inside `scan_group_collect`), halving directory I/O.
///
/// Returns `HashMap<group_key, (label, Ok(sorted_files) | Err((dir, msg)))>`.
/// An `Err` entry means directory traversal failed; callers convert it to
/// explicit violations for every check in that group (same contract as before).
///
/// Groups are deduplicated by `directory_group_key` so each directory set is
/// traversed exactly once even when multiple checks share the same directory.
fn collect_scan_groups(repo_root: &Path, checks: &[NativeScanCheck]) -> ScanGroupMap {
    let mut group_map: ScanGroupMap = HashMap::new();

    for check in checks {
        let key = directory_group_key(check);
        if group_map.contains_key(&key) {
            continue; // already collected this group
        }

        let label = check.directories.join(" + ");
        let result = collect_files_for_check(repo_root, check);
        group_map.insert(key, (label, result));
    }

    group_map
}

fn collect_files_for_check(
    repo_root: &Path,
    check: &NativeScanCheck,
) -> Result<Vec<PathBuf>, (PathBuf, String)> {
    let mut files: Vec<PathBuf> = Vec::new();

    for dir in check.directories {
        collect_files_from_dir(repo_root, dir, check, &mut files)?;
    }

    files.sort();
    Ok(files)
}

fn collect_files_from_dir(
    repo_root: &Path,
    dir: &str,
    check: &NativeScanCheck,
    files: &mut Vec<PathBuf>,
) -> Result<(), (PathBuf, String)> {
    let full_dir = repo_root.join(dir);
    if !full_dir.exists() {
        return Ok(());
    }

    collect_files_with_glob_excluding(&full_dir, check.include_glob, check.exclude_globs, files)
        .map_err(|e| {
            let msg = format!("read_dir error for {}: {e}", full_dir.display());
            (full_dir.clone(), msg)
        })
}

/// Read a slice of files in parallel using scoped threads.
///
/// Returns `Result<Vec<u8>>` per file in the **same order** as `files`, so
/// callers can zip results with the original slice without re-sorting.
/// For small groups (below `PARALLEL_THRESHOLD`) sequential reads are used
/// to avoid thread-spawn overhead.
fn read_scan_files_parallel(files: &[PathBuf]) -> Vec<std::io::Result<Vec<u8>>> {
    const PARALLEL_THRESHOLD: usize = 4;
    if files.len() < PARALLEL_THRESHOLD {
        return files.iter().map(std::fs::read).collect();
    }

    let workers = scan_read_worker_count(files.len());
    let mut results: Vec<std::io::Result<Vec<u8>>> = (0..files.len())
        .map(|_| Err(std::io::Error::other("scan read worker did not fill slot")))
        .collect();

    std::thread::scope(|s| {
        let handles: Vec<_> = (0..workers)
            .map(|worker_id| {
                s.spawn(move || {
                    let mut out: Vec<(usize, std::io::Result<Vec<u8>>)> = Vec::new();
                    for i in (worker_id..files.len()).step_by(workers) {
                        out.push((i, std::fs::read(&files[i])));
                    }
                    out
                })
            })
            .collect();

        for h in handles {
            for (i, r) in h.join().expect("scan read worker panicked") {
                results[i] = r;
            }
        }
    });

    results
}

fn scan_read_worker_count(len: usize) -> usize {
    const MAX_IO_WORKERS: usize = 32;
    if len == 0 {
        return 0;
    }
    let avail = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    std::cmp::min(len, std::cmp::min(avail, MAX_IO_WORKERS)).max(1)
}

fn directory_group_key(check: &NativeScanCheck) -> String {
    let mut dirs: Vec<&str> = check.directories.to_vec();
    dirs.sort_unstable();
    let mut excludes: Vec<&str> = check.exclude_globs.to_vec();
    excludes.sort_unstable();
    format!(
        "{}:{}:{}",
        dirs.join(","),
        check.include_glob,
        excludes.join(",")
    )
}

/// Scan one directory group: search all pre-collected files, demultiplex matches.
///
/// Accepts `files` pre-collected by `collect_scan_groups` (single traversal),
/// eliminating the previous per-group directory walk.
///
/// Returns a flat list of `(check_index, NativeScanViolation)` pairs so the
/// caller can merge results from multiple groups (including parallel groups).
///
/// `files_done` is a shared atomic counter incremented once per file.
/// `progress_threshold` controls how often per-file progress is emitted:
/// `progress("native-scan", "N files scanned")` fires whenever `files_done`
/// is a positive multiple of `progress_threshold`.
fn scan_group_collect(
    all_checks: &[NativeScanCheck],
    check_indices: &[usize],
    files: &[PathBuf],
    files_done: &AtomicUsize,
    progress_threshold: usize,
    progress: &(dyn Fn(&str, &str) + Sync),
) -> Vec<(usize, NativeScanViolation)> {
    let state = match ScanGroupState::try_new(all_checks, check_indices) {
        Some(state) => state,
        None => return Vec::new(),
    };
    let progress_state = ScanProgress {
        files_done,
        progress_threshold,
        progress,
    };
    scan_group_files(files, &state, &progress_state)
}

fn scan_group_files(
    files: &[PathBuf],
    state: &ScanGroupState,
    progress: &ScanProgress,
) -> Vec<(usize, NativeScanViolation)> {
    read_scan_files_parallel(files)
        .into_iter()
        .zip(files)
        .flat_map(|(content_res, file_path)| {
            process_scan_file(file_path, content_res, state, progress)
        })
        .collect()
}

fn build_pattern_vectors<'a>(
    all_checks: &'a [NativeScanCheck],
    check_indices: &[usize],
) -> (Vec<&'a str>, Vec<usize>) {
    let mut all_patterns = Vec::new();
    let mut pattern_to_check = Vec::new();
    for &ci in check_indices {
        for &literal in all_checks[ci].literals {
            all_patterns.push(literal);
            pattern_to_check.push(ci);
        }
    }
    (all_patterns, pattern_to_check)
}

fn build_negative_context_precomputation(
    all_checks: &[NativeScanCheck],
    check_indices: &[usize],
) -> HashMap<usize, (usize, usize)> {
    check_indices
        .iter()
        .filter_map(|&ci| build_negative_context_entry(all_checks, ci))
        .collect()
}

fn build_negative_context_entry(
    all_checks: &[NativeScanCheck],
    ci: usize,
) -> Option<(usize, (usize, usize))> {
    if let MatchMode::NegativeLookahead {
        negative_context, ..
    } = all_checks[ci].mode
    {
        if !negative_context.is_empty() {
            let precomputed = critical_factorization(negative_context.as_bytes());
            return Some((ci, precomputed));
        }
    }
    None
}

fn build_automaton(patterns: &[&str]) -> Option<AhoCorasick> {
    AhoCorasickBuilder::new()
        .kind(Some(AhoCorasickKind::DFA))
        .build(patterns)
        .or_else(|_| AhoCorasick::new(patterns))
        .ok()
}

pub(crate) struct ScanGroupState<'a> {
    pub(crate) all_checks: &'a [NativeScanCheck],
    pub(crate) check_indices: &'a [usize],
    pub(crate) pattern_to_check: Vec<usize>,
    pub(crate) all_patterns: Vec<&'a str>,
    pub(crate) ac: AhoCorasick,
    pub(crate) tw_precomputed: HashMap<usize, (usize, usize)>,
}

impl<'a> ScanGroupState<'a> {
    fn try_new(all_checks: &'a [NativeScanCheck], check_indices: &'a [usize]) -> Option<Self> {
        let (all_patterns, pattern_to_check) = build_pattern_vectors(all_checks, check_indices);
        if all_patterns.is_empty() {
            return None;
        }
        let tw_precomputed = build_negative_context_precomputation(all_checks, check_indices);
        let ac = build_automaton(&all_patterns)?;
        Some(Self {
            all_checks,
            check_indices,
            pattern_to_check,
            all_patterns,
            ac,
            tw_precomputed,
        })
    }
}

struct ScanProgress<'a> {
    files_done: &'a AtomicUsize,
    progress_threshold: usize,
    progress: &'a (dyn Fn(&str, &str) + Sync),
}

impl<'a> ScanProgress<'a> {
    fn tick(&self) {
        let files_scanned = self.files_done.fetch_add(1, Ordering::Relaxed) + 1;
        if files_scanned > 0
            && self.progress_threshold > 0
            && files_scanned.is_multiple_of(self.progress_threshold)
        {
            (self.progress)("native-scan", &format!("{files_scanned} files scanned"));
        }
    }
}

fn process_scan_file(
    file_path: &Path,
    content_res: std::io::Result<Vec<u8>>,
    state: &ScanGroupState,
    progress: &ScanProgress,
) -> Vec<(usize, NativeScanViolation)> {
    progress.tick();

    let content = match content_res {
        Ok(c) => c,
        Err(e) => return collect_file_read_errors(file_path, state, e),
    };

    let line_idx = LineIndex::new(&content);

    scan_matches(file_path, &content, &line_idx, state)
}

fn scan_matches(
    file_path: &Path,
    content: &[u8],
    line_idx: &LineIndex,
    state: &ScanGroupState,
) -> Vec<(usize, NativeScanViolation)> {
    state
        .ac
        .find_iter(content)
        .filter_map(|mat| {
            let check_idx = state.pattern_to_check[mat.pattern().as_usize()];
            let check = &state.all_checks[check_idx];
            let matched_literal = state.all_patterns[mat.pattern().as_usize()];
            let ctx = MatchContext {
                content,
                line_idx,
                check_idx,
                check,
                matched_literal,
                mat_start: mat.start(),
                mat_end: mat.end(),
                tw_precomputed: &state.tw_precomputed,
            };
            domain_handle_match(file_path, &ctx, check.exclude_globs)
        })
        .collect()
}

fn collect_file_read_errors(
    file_path: &Path,
    state: &ScanGroupState,
    error: std::io::Error,
) -> Vec<(usize, NativeScanViolation)> {
    let mut read_violations = Vec::new();
    for &ci in state.check_indices {
        let check = &state.all_checks[ci];
        if file_is_excluded(file_path, check.exclude_globs) {
            continue;
        }
        read_violations.push((
            ci,
            NativeScanViolation {
                file: file_path.to_path_buf(),
                line_number: 1,
                line: format!("read file error for {}: {error}", file_path.display()),
            },
        ));
    }
    read_violations
}

pub(crate) fn collect_files_with_glob(
    dir: &Path,
    include_glob: &str,
    files: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    collect_files_with_glob_excluding(dir, include_glob, &[], files)
}

pub(crate) fn collect_files_with_glob_excluding(
    dir: &Path,
    include_glob: &str,
    exclude_globs: &[&str],
    files: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    for entry in std::fs::read_dir(dir)? {
        process_glob_entry(entry, include_glob, exclude_globs, files)?;
    }
    Ok(())
}

fn process_glob_entry(
    entry: std::io::Result<std::fs::DirEntry>,
    include_glob: &str,
    exclude_globs: &[&str],
    files: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    let entry = entry?;
    let path = entry.path();
    let is_dir = path.is_dir();
    let matches_include = !is_dir && file_matches_include_glob(&path, include_glob);
    match classify_glob_entry(&path, is_dir, exclude_globs, matches_include) {
        GlobEntryAction::Skip => {}
        GlobEntryAction::Recurse(p) => {
            collect_files_with_glob_excluding(&p, include_glob, exclude_globs, files)?;
        }
        GlobEntryAction::Include(p) => files.push(p),
    }
    Ok(())
}

pub(crate) fn file_matches_include_glob(path: &Path, glob: &str) -> bool {
    if glob == "*" {
        return path.is_file();
    }
    if let Some(ext_pattern) = glob.strip_prefix("*.") {
        return path.extension().and_then(|e| e.to_str()) == Some(ext_pattern);
    }
    path.file_name().and_then(|n| n.to_str()) == Some(glob)
}

// ── Tests

#[cfg(test)]
#[path = "scanner_tests.rs"]
mod tests;

#[cfg(test)]
#[path = "scanner_search.rs"]
mod scanner_search;

#[cfg(test)]
pub(crate) use scanner_search::{bmh_contains, tw_contains};
