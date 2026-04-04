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
use crate::io::string_search::{critical_factorization, kmp_search, tw_contains_precomputed};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

pub use crate::io::scanner_diagnostics::{scan_has_diagnostic_prefix, DiagnosticLevel};

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

struct ScanGroupState<'a> {
    all_checks: &'a [NativeScanCheck],
    check_indices: &'a [usize],
    pattern_to_check: Vec<usize>,
    all_patterns: Vec<&'a str>,
    ac: AhoCorasick,
    tw_precomputed: HashMap<usize, (usize, usize)>,
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
    let mut violations = Vec::new();
    for mat in state.ac.find_iter(content) {
        if let Some(entry) = handle_match(file_path, content, line_idx, mat, state) {
            violations.push(entry);
        }
    }
    violations
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

fn handle_match(
    file_path: &Path,
    content: &[u8],
    line_idx: &LineIndex,
    mat: aho_corasick::Match,
    state: &ScanGroupState,
) -> Option<(usize, NativeScanViolation)> {
    let check_idx = state.pattern_to_check[mat.pattern().as_usize()];
    let check = &state.all_checks[check_idx];
    if file_is_excluded(file_path, check.exclude_globs) {
        return None;
    }
    let byte_start = mat.start();
    let byte_end = mat.end();
    if !is_match_mode_violation(
        check,
        check_idx,
        content,
        line_idx,
        byte_start,
        byte_end,
        &state.tw_precomputed,
    ) {
        return None;
    }

    let matched_literal = state.all_patterns[mat.pattern().as_usize()];
    if should_skip_forbidden_allow_expect(
        check.name,
        matched_literal,
        content,
        line_idx,
        byte_start,
    ) {
        return None;
    }

    let line_number = line_idx.line_number(byte_start) + 1;
    let line = String::from_utf8_lossy(line_idx.extract_line(content, byte_start)).to_string();
    Some((
        check_idx,
        NativeScanViolation {
            file: file_path.to_path_buf(),
            line_number,
            line,
        },
    ))
}

fn should_skip_forbidden_allow_expect(
    check_name: &str,
    matched_literal: &str,
    content: &[u8],
    line_idx: &LineIndex,
    byte_offset: usize,
) -> bool {
    if check_name != "forbidden-allow-expect-scan" {
        return false;
    }

    if is_allowed_expect_with_reason(content, line_idx, byte_offset, matched_literal) {
        return true;
    }

    if is_cfg_attr_literal(matched_literal) {
        let line_bytes = line_idx.extract_line(content, byte_offset);
        if is_allowed_cfg_attr_expect_with_reason(content, line_idx, byte_offset, matched_literal) {
            return true;
        }
        if !line_contains_allow_or_expect(line_bytes) {
            return true;
        }
    }

    false
}

fn is_match_mode_violation(
    check: &NativeScanCheck,
    check_idx: usize,
    content: &[u8],
    line_idx: &LineIndex,
    byte_start: usize,
    byte_end: usize,
    tw_precomputed: &HashMap<usize, (usize, usize)>,
) -> bool {
    match check.mode {
        MatchMode::AnyLiteral { skip_comment_lines } => {
            !(skip_comment_lines && line_is_comment(content, line_idx, byte_start))
        }
        MatchMode::StemWithBoolSuffix => {
            word_boundary_at_start(content, byte_start) && matches_bool_suffix(content, byte_end)
        }
        MatchMode::AnyLiteralAtLineStart { skip_comment_lines } => {
            if skip_comment_lines && line_is_comment(content, line_idx, byte_start) {
                false
            } else {
                only_whitespace_before_on_line(content, line_idx, byte_start)
            }
        }
        MatchMode::NegativeLookahead {
            negative_context,
            word_boundary_at_end,
        } => {
            if word_boundary_at_end && !is_word_boundary_at_end(content, byte_end) {
                return false;
            }
            if negative_context.is_empty() {
                return false;
            }
            let line_bytes = line_idx.extract_line(content, byte_start);
            if let Some(precomputed) = tw_precomputed.get(&check_idx) {
                !tw_contains_precomputed(line_bytes, negative_context.as_bytes(), *precomputed)
            } else {
                true
            }
        }
    }
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
    let entries = std::fs::read_dir(dir)?;
    for entry in entries {
        let entry = entry?;
        let path = entry.path();
        if file_is_excluded(&path, exclude_globs) {
            continue;
        }
        if path.is_dir() {
            collect_files_with_glob_excluding(&path, include_glob, exclude_globs, files)?;
        } else if file_matches_include_glob(&path, include_glob) {
            files.push(path);
        }
    }

    Ok(())
}

fn file_matches_include_glob(path: &Path, glob: &str) -> bool {
    if glob == "*" {
        return path.is_file();
    }
    if let Some(ext_pattern) = glob.strip_prefix("*.") {
        return path.extension().and_then(|e| e.to_str()) == Some(ext_pattern);
    }
    path.file_name().and_then(|n| n.to_str()) == Some(glob)
}

fn file_is_excluded(path: &Path, exclude_globs: &[&str]) -> bool {
    exclude_globs
        .iter()
        .any(|g| file_matches_exclude_glob(path, g))
}

fn file_matches_exclude_glob(path: &Path, glob: &str) -> bool {
    // Exact file-name match (no path separators or wildcards): "_TEMPLATE.rs"
    if !glob.contains('/') && !glob.contains('*') {
        return path.file_name().and_then(|n| n.to_str()) == Some(glob);
    }
    // "**/<segment>/**" — any path component equals `segment`.
    if glob.starts_with("**/") && glob.ends_with("/**") {
        let segment = &glob[3..glob.len() - 3];
        return path
            .components()
            .any(|c| c.as_os_str().to_str() == Some(segment));
    }
    // Fallback: exact file-name match.
    path.file_name().and_then(|n| n.to_str()) == Some(glob)
}

/// Return true when the line containing `byte_offset` starts with `//`
/// (ignoring leading whitespace).
///
/// Uses `line_idx` for O(log L) line-start lookup instead of O(n) linear scan.
fn line_is_comment(content: &[u8], line_idx: &LineIndex, byte_offset: usize) -> bool {
    let line_start = line_idx.line_start(byte_offset);
    let trimmed = &content[line_start..];
    let non_ws = trimmed.iter().position(|&b| b != b' ' && b != b'\t');
    match non_ws {
        Some(i) => trimmed[i..].starts_with(b"//"),
        None => false,
    }
}

/// Return true when the character immediately before `start` is NOT an
/// ASCII alphanumeric character or underscore (word-boundary at start).
fn word_boundary_at_start(content: &[u8], start: usize) -> bool {
    if start == 0 {
        return true;
    }
    let prev = content[start - 1];
    !prev.is_ascii_alphanumeric() && prev != b'_'
}

/// Return true when the bytes at `end` match `\s*:\s*bool` followed by a
/// non-identifier character (or end of input).
fn matches_bool_suffix(content: &[u8], end: usize) -> bool {
    let rest = &content[end..];
    let mut i = 0;
    // Skip optional whitespace.
    while i < rest.len() && rest[i].is_ascii_whitespace() {
        i += 1;
    }
    if i >= rest.len() || rest[i] != b':' {
        return false;
    }
    i += 1;
    // Skip optional whitespace.
    while i < rest.len() && rest[i].is_ascii_whitespace() {
        i += 1;
    }
    // Use KMP (TAOCP Vol. 3, §6.4) for guaranteed O(n+m) suffix search.
    if kmp_search(&rest[i..], b"bool") != Some(0) {
        return false;
    }
    let after = i + 4;
    // Must be followed by a non-identifier character or end of input.
    if after < rest.len() {
        let next = rest[after];
        if next.is_ascii_alphanumeric() || next == b'_' {
            return false;
        }
    }
    true
}

/// Return true when only whitespace (spaces or tabs) precedes `byte_offset`
/// on the same line.  An empty prefix (match at column 0) also returns true.
///
/// Uses `line_idx` for O(log L) line-start lookup instead of O(n) linear scan.
fn only_whitespace_before_on_line(
    content: &[u8],
    line_idx: &LineIndex,
    byte_offset: usize,
) -> bool {
    let line_start = line_idx.line_start(byte_offset);
    content[line_start..byte_offset]
        .iter()
        .all(|&b| b == b' ' || b == b'\t')
}

/// Return true when the line contains `reason = "..."` with a non-empty reason string.
///
/// Accepts flexible whitespace around `=`: `reason = "..."`, `reason="..."`, `reason =  "..."`.
/// The reason must contain at least one character between the quotes.
fn line_has_nonempty_reason(line: &[u8]) -> bool {
    let Some(pos) = kmp_search(line, b"reason") else {
        return false;
    };
    let rest = &line[pos + 6..];
    let mut i = 0;
    // skip whitespace
    while i < rest.len() && (rest[i] == b' ' || rest[i] == b'\t') {
        i += 1;
    }
    // expect '='
    if i >= rest.len() || rest[i] != b'=' {
        return false;
    }
    i += 1;
    // skip whitespace
    while i < rest.len() && (rest[i] == b' ' || rest[i] == b'\t') {
        i += 1;
    }
    // expect opening '"'
    if i >= rest.len() || rest[i] != b'"' {
        return false;
    }
    i += 1;
    // next char must not be closing '"' (non-empty reason)
    i < rest.len() && rest[i] != b'"'
}

/// Return true when an `#[expect(...)]` match is permitted because it has a
/// non-empty `reason = "..."` and is an outer (item-scope) attribute.
///
/// Inner attributes (`#![expect(...)]`) are always violations regardless of reason,
/// because item scope (not module or crate) is required.
fn is_allowed_expect_with_reason(
    content: &[u8],
    line_idx: &LineIndex,
    byte_offset: usize,
    matched_literal: &str,
) -> bool {
    // Only outer #[expect( is conditionally allowed; #![expect( is always blocked
    if matched_literal != "#[expect(" {
        return false;
    }

    // Check current and subsequent lines for reason (handles multi-line attributes)
    let current_line_number = line_idx.line_number(byte_offset);
    let max_lines_to_check = 10; // Reasonable limit for attribute continuation

    for line_offset in 0..max_lines_to_check {
        let check_line = current_line_number + line_offset;
        if check_line >= line_idx.newlines.len() {
            break;
        }
        let line_start = line_idx.start_of_line(check_line);
        let line_bytes = line_idx.extract_line(content, line_start);
        if line_has_nonempty_reason(line_bytes) {
            return true;
        }
        // Stop if we've passed the attribute (no more continuation)
        if line_offset > 0 && !line_bytes.iter().any(|&b| b == b')' || b == b']') {
            // Line doesn't have closing, might be continuation - keep checking
        }
        // If line has closing without reason, stop checking
        if line_offset > 0 && line_bytes.contains(&b')') && !line_has_nonempty_reason(line_bytes) {
            break;
        }
    }

    false
}

/// Return true when a `#[cfg_attr(..., expect(...))]` match is permitted because
/// it wraps an expect with a non-empty `reason = "..."` and is an outer attribute.
fn is_allowed_cfg_attr_expect_with_reason(
    content: &[u8],
    line_idx: &LineIndex,
    byte_offset: usize,
    matched_literal: &str,
) -> bool {
    // Only outer #[cfg_attr( is conditionally allowed
    if matched_literal != "#[cfg_attr(" {
        return false;
    }

    // Check current and subsequent lines for the expect pattern and reason
    let current_line_number = line_idx.line_number(byte_offset);
    let max_lines_to_check = 10;
    let mut has_expect = false;
    let mut has_reason = false;

    for line_offset in 0..max_lines_to_check {
        let check_line = current_line_number + line_offset;
        if check_line >= line_idx.newlines.len() {
            break;
        }
        let line_start = line_idx.start_of_line(check_line);
        let line_bytes = line_idx.extract_line(content, line_start);

        // Check for expect( pattern (but not allow)
        if line_bytes.windows(7).any(|w| w == b"expect(") {
            has_expect = true;
        }
        if line_bytes.windows(6).any(|w| w == b"allow(") {
            // If we find allow( without expect(, it's not valid
            if !has_expect {
                return false;
            }
        }

        // Check for reason
        if line_has_nonempty_reason(line_bytes) {
            has_reason = true;
        }

        // If we have both expect and reason, we're good
        if has_expect && has_reason {
            return true;
        }

        // Stop if line has closing without both
        if line_offset > 0 && line_bytes.contains(&b')') {
            break;
        }
    }

    has_expect && has_reason
}

/// Return true if the literal is a cfg_attr variant.
fn is_cfg_attr_literal(lit: &str) -> bool {
    lit == "#[cfg_attr(" || lit == "#![cfg_attr("
}

/// Return true if the line contains allow( or expect( (used to filter
/// cfg_attr matches - only those with allow/expect are violations).
fn line_contains_allow_or_expect(line: &[u8]) -> bool {
    line.windows(6).any(|w| w == b"allow(") || line.windows(7).any(|w| w == b"expect(")
}

/// Return true when the character immediately after `end` is NOT an ASCII
/// alphanumeric character or underscore (word boundary at end of literal).
fn is_word_boundary_at_end(content: &[u8], end: usize) -> bool {
    if end >= content.len() {
        return true;
    }
    let next = content[end];
    !next.is_ascii_alphanumeric() && next != b'_'
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
