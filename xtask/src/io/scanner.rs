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
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    OnceLock,
};

const LINT_SCAN_EXCLUDE_GLOBS: &[&str] = &[
    "**/node_modules/**",
    "**/dist/**",
    "**/ui/**",
    "**/target/**",
    // verify.rs contains FORBIDDEN_ALLOW_EXPECT_POLICY which documents the lint policy
    // using literal examples of the forbidden patterns - these are not actual violations
    "verify.rs",
];

/// Matching strategy for a native scan check.
///
/// This is a `const`-constructible enum so `NativeScanCheck` instances can
/// live in `static` arrays.
#[derive(Clone, Copy)]
pub enum MatchMode {
    /// Fail if any of the check's `literals` appear in a scanned file.
    /// When `skip_comment_lines` is true, matches on lines whose non-whitespace
    /// prefix is `//` are ignored (avoids false positives in doc-comments).
    AnyLiteral { skip_comment_lines: bool },
    /// Fail if any of the check's `literals` (stem identifiers) appear in a
    /// scanned file AND the text immediately following the match is
    /// `<optional-whitespace>:<optional-whitespace>bool`.
    ///
    /// Also enforces a word-boundary check at the match start so that
    /// `is_testing_mode` does not trigger on the `is_test` stem.
    StemWithBoolSuffix,
    /// Fail if the literal appears AND only whitespace (or nothing) precedes it
    /// on the same line.  Equivalent to the rg anchor `^\s*literal`.
    ///
    /// When `skip_comment_lines` is true, matches on lines whose non-whitespace
    /// prefix is `//` are ignored (avoids false positives in doc-comments).
    ///
    /// This provides O(n+m+z) Aho-Corasick detection with an O(k) per-match
    /// post-filter for the line-start check (k = length of prefix slice).
    AnyLiteralAtLineStart { skip_comment_lines: bool },
    /// Fail if the literal appears at a word boundary (next char is not
    /// alphanumeric/underscore, when `word_boundary_at_end` is true) AND the
    /// `negative_context` string is absent from the same line.
    ///
    /// Implements a native negative-lookahead: the violation is triggered only
    /// when the literal is found BUT the negative_context is NOT present on the
    /// same line.  This replaces a PCRE2 `(?!.*ctx)` pattern.
    NegativeLookahead {
        /// If present on the same line, the match is NOT a violation.
        negative_context: &'static str,
        /// When true, the character immediately after the literal must not be
        /// an ASCII alphanumeric character or underscore (word boundary).
        word_boundary_at_end: bool,
    },
}

/// A single native scan check definition.
///
/// `NativeScanCheck` values are `const`-constructible for use in static arrays.
pub struct NativeScanCheck {
    pub name: &'static str,
    /// Literal byte-strings to search for with Aho-Corasick.
    pub literals: &'static [&'static str],
    /// Directories to scan, relative to the repo root.
    pub directories: &'static [&'static str],
    /// Glob pattern selecting which files to include (e.g. `"*.rs"`).
    pub include_glob: &'static str,
    /// File-name or path globs to exclude from scanning.
    /// Supports `"<name>.rs"` (exact file-name) and `"**/<seg>/**"` (path component).
    pub exclude_globs: &'static [&'static str],
    pub mode: MatchMode,
}

/// A single line-level violation reported by a native scan check.
#[derive(Debug)]
pub struct NativeScanViolation {
    pub file: PathBuf,
    /// 1-based line number.
    pub line_number: usize,
    /// The full text of the offending line (without trailing newline).
    pub line: String,
}

/// The result of running one `NativeScanCheck`.
pub struct NativeScanCheckResult {
    pub check_name: &'static str,
    /// `true` iff no violations were found.
    pub passed: bool,
    /// Populated when `passed == false`.
    pub violations: Vec<NativeScanViolation>,
}

/// Pre-built line index enabling O(log L) line-context lookups via binary search.
///
/// Construction is a single O(n) pass collecting newline byte-offsets into a
/// sorted Vec.  All subsequent lookups use Knuth's binary search algorithm
/// (TAOCP Vol. 3, §6.2.1 Algorithm B) via `partition_point`, giving O(log L)
/// per query instead of the O(n) linear scan in the original helpers.
///
/// `pub` visibility is intentional: `compliance.rs` shares this index for its
/// Aho-Corasick timeout-wrapper scan, avoiding a duplicate implementation.
pub struct LineIndex {
    /// Byte offsets of every b'\n' in the source, in ascending order.
    pub newlines: Vec<usize>,
    /// Total byte length of the source buffer.
    pub content_len: usize,
}

impl LineIndex {
    /// Build the index from raw file bytes in O(n).
    pub fn new(content: &[u8]) -> Self {
        let newlines: Vec<usize> = content
            .iter()
            .enumerate()
            .filter_map(|(i, &b)| if b == b'\n' { Some(i) } else { None })
            .collect();
        Self {
            newlines,
            content_len: content.len(),
        }
    }

    /// 0-based line number of the line that contains `offset`.
    /// Uses binary search (TAOCP Vol. 3, §6.2.1 Algorithm B): O(log L).
    ///
    /// The number of newlines strictly before `offset` equals the 0-based
    /// line number of the line containing `offset`.
    pub fn line_number(&self, offset: usize) -> usize {
        self.newlines.partition_point(|&nl| nl < offset)
    }

    /// Byte offset of the first byte of the line containing `offset`.
    pub fn line_start(&self, offset: usize) -> usize {
        let idx = self.newlines.partition_point(|&nl| nl < offset);
        if idx == 0 {
            0
        } else {
            self.newlines[idx - 1] + 1
        }
    }

    /// Byte offset of the `\n` terminating the line containing `offset`,
    /// or `content_len` if the line has no trailing newline.
    ///
    /// When `offset` is exactly at a `\n`, that newline is the line terminator,
    /// so its offset is returned (the line ends at the newline itself).
    pub fn line_end(&self, offset: usize) -> usize {
        // Use strict `<` so that when `offset` IS the newline byte,
        // we return that newline offset (not the next one).
        let idx = self.newlines.partition_point(|&nl| nl < offset);
        if idx < self.newlines.len() {
            self.newlines[idx]
        } else {
            self.content_len
        }
    }

    /// Extract the raw bytes of the line containing `offset` (without the
    /// trailing `\n`).
    pub fn extract_line<'a>(&self, content: &'a [u8], offset: usize) -> &'a [u8] {
        let start = self.line_start(offset);
        let end = self.line_end(offset);
        &content[start..end]
    }

    /// Byte offset of the first byte of line number `line_num` (0-based).
    ///
    /// O(1) direct index into the newlines vec, avoiding a binary search.
    /// Returns `content_len` when `line_num` is past the end of the file.
    pub fn start_of_line(&self, line_num: usize) -> usize {
        if line_num == 0 {
            0
        } else if line_num <= self.newlines.len() {
            self.newlines[line_num - 1] + 1
        } else {
            self.content_len
        }
    }
}

// ── Public constants ──────────────────────────────────────────────────────────

/// All native scan checks, replacing 17 `rg` subprocess calls.
///
/// Groups are inferred at runtime by identical `(sorted-directories, include_glob)` keys.
pub const NATIVE_SCAN_CHECKS: &[NativeScanCheck] = &[
    // ── ralph-workflow/src group ──────────────────────────────────────────────
    NativeScanCheck {
        name: "no-test-flags-cfg-test",
        literals: &["cfg!(test)"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    NativeScanCheck {
        name: "no-test-flags-test-mode-params",
        literals: &["test_mode", "is_test", "is_testing", "testing_mode"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    },
    NativeScanCheck {
        name: "no-test-flags-skip-params",
        literals: &[
            "skip_validation",
            "skip_verify",
            "skip_check",
            "skip_auth",
            "skip_api",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    },
    NativeScanCheck {
        name: "no-test-flags-mock-params",
        literals: &[
            "mock_mode",
            "fake_mode",
            "stub_mode",
            "use_mock",
            "use_fake",
            "use_stub",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    },
    NativeScanCheck {
        name: "no-test-flags-testing-feature",
        literals: &["#[cfg(feature = \"testing\")]"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    NativeScanCheck {
        name: "no-test-flags-cfg-not-test",
        literals: &["#[cfg(not(test))]"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    NativeScanCheck {
        name: "audit-no-serial-src",
        literals: &["#[serial]"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-test-helpers-src",
        literals: &[
            "use test_helpers::",
            "init_git_repo",
            "commit_all",
            "git_switch",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        // Test subdirectories (e.g. src/mcp_server/tests/) are allowed to import test_helpers —
        // they are test code co-located with the production module, not production code itself.
        exclude_globs: &["**/tests/**", "tests.rs"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    NativeScanCheck {
        name: "audit-no-tempdir-src",
        literals: &["TempDir"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &["**/git_helpers/**", "main.rs", "**/tests/**", "tests.rs"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    // ── tests/integration_tests group ────────────────────────────────────────
    NativeScanCheck {
        name: "compliance-no-process-spawn",
        literals: &["std::process::Command::new", "assert_cmd::Command::new"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &["_TEMPLATE.rs"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "compliance-no-serial",
        literals: &["#[serial]", "use serial_test"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &["_TEMPLATE.rs"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    NativeScanCheck {
        name: "audit-no-cfg-test-integration",
        literals: &["cfg!(test)"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-real-fs-integration",
        literals: &["std::fs::", "TempDir", "tempfile::"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-real-process-integration",
        literals: &["std::process::Command::new"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-env-mutations-integration",
        literals: &[
            "std::env::set_var",
            "std::env::remove_var",
            "env::set_var",
            "env::remove_var",
        ],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    // ── tests/process_system_tests group ─────────────────────────────────────
    NativeScanCheck {
        name: "audit-no-serial-process-system",
        literals: &["#[serial]"],
        directories: &["tests/process_system_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-git2-process-system",
        literals: &["git2::", "init_git_repo"],
        directories: &["tests/process_system_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    // ── ralph-workflow/src/reducer/handler/ group ─────────────────────────────
    NativeScanCheck {
        name: "no-string-errors-handlers",
        literals: &[
            "anyhow::anyhow!(",
            "anyhow!(",
            "anyhow::bail!(",
            "bail!(",
            "anyhow::ensure!(",
            "ensure!(",
            "anyhow::format_err!(",
            "format_err!(",
            "anyhow::Error::msg(",
        ],
        directories: &["ralph-workflow/src/reducer/handler"],
        include_glob: "*.rs",
        // Exclude test subdirectories (mirrors rg --glob !**/tests/**)
        exclude_globs: &["**/tests/**"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    // ── audit-ignore-has-url: replaces PCRE2 rg negative lookahead ────────────
    // Fails if #[ignore] appears without an https:// URL on the same line.
    // The word_boundary_at_end check prevents #[ignore_reason] from triggering.
    NativeScanCheck {
        name: "audit-ignore-has-url",
        literals: &["#[ignore"],
        directories: &["tests", "ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::NegativeLookahead {
            negative_context: "https://",
            word_boundary_at_end: true,
        },
    },
    // ── forbidden-allow-expect-scan: replaces PCRE2 rg multiline pattern ──────
    // Fails if #[allow(, #![allow(, #[expect(, #![expect(, #[cfg_attr(, or #![cfg_attr(
    // appears at line start (possibly preceded by whitespace). Comment lines are skipped.
    // Note: #[cfg_attr( and #![cfg_attr( are detected but require allow( or expect( on the
    // same line to be flagged as violations (to avoid false positives on regular cfg_attr usage).
    NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &[
            "#[allow(",
            "#![allow(",
            "#[expect(",
            "#![expect(",
            "#[cfg_attr(",
            "#![cfg_attr(",
        ],
        directories: &[
            "ralph-workflow/src",
            "tests",
            "xtask/src",
            "test-helpers/src",
            "ralph-gui",
            "lints",
        ],
        include_glob: "*.rs",
        exclude_globs: LINT_SCAN_EXCLUDE_GLOBS,
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    },
];

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
    let mut results: Vec<NativeScanCheckResult> = checks
        .iter()
        .map(|c| NativeScanCheckResult {
            check_name: c.name,
            passed: true,
            violations: Vec::new(),
        })
        .collect();

    if checks.is_empty() {
        return results;
    }

    // Single-pass directory traversal: collect all files for each group at once.
    // Eliminates the previous double-traversal (count_scan_files + per-group collect
    // in scan_group_collect), halving directory I/O.
    // Reference: TAOCP emphasis on reducing I/O passes to O(1) when possible.
    let group_map = collect_scan_groups(repo_root, checks);

    // Compute total file count and per-group summaries from the collected data.
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

    if total_files > 0 {
        progress(
            "native-scan",
            &format!(
                "{total_files} files across {} group(s)",
                group_summaries.len()
            ),
        );
        for (label, count) in &group_summaries {
            progress("native-scan", &format!("  {label}: {count} files"));
        }
    }

    // Adaptive progress threshold: emit per-file progress at ~5% intervals.
    // max(5, total/20) ensures we always get at least one update per 5 files
    // on tiny codebases and no more than ~20 updates on large ones.
    let progress_threshold = (total_files / 20).max(5);

    // Group check indices by (sorted directories + include_glob) key.
    // Sort for deterministic group ordering across runs (HashMap has non-deterministic
    // iteration order; sorting guards against flaky test failures).
    let mut groups: HashMap<String, Vec<usize>> = HashMap::new();
    for (idx, check) in checks.iter().enumerate() {
        groups
            .entry(directory_group_key(check))
            .or_default()
            .push(idx);
    }
    let mut groups_vec: Vec<(String, Vec<usize>)> = groups.into_iter().collect();
    groups_vec.sort_by(|(a, _), (b, _)| a.cmp(b));

    // Build work items: pair each group's check indices with its pre-collected files.
    // Moving check_indices (Vec<usize>) into each work item allows the scoped thread
    // to own the data without a lifetime dependency on groups_vec.
    let work_items: Vec<(Vec<usize>, &GroupFilesResult)> = groups_vec
        .into_iter()
        .map(|(key, indices)| {
            let (_, files_result) = group_map.get(&key).expect("group key must exist in map");
            (indices, files_result)
        })
        .collect();

    // Shared atomic file counter.  Incremented once per file processed across all
    // scan-group threads.  Uses Relaxed ordering: we only need approximate counts
    // for progress reporting (no ordering guarantees needed between threads).
    let files_done = AtomicUsize::new(0);

    // Collect violations per group in parallel using scoped threads.
    // Each group is independent (different directories or patterns), so no
    // synchronisation is needed during the scan phase.
    let mut all_violations: Vec<Vec<(usize, NativeScanViolation)>> =
        Vec::with_capacity(work_items.len());

    std::thread::scope(|s| {
        // Capture a reference to files_done (references are Copy) so that each
        // spawned closure can share it without moving the AtomicUsize itself.
        let fd = &files_done;
        let handles: Vec<_> = work_items
            .into_iter()
            .map(|(check_indices, files_result)| {
                // progress_threshold is usize (Copy), so the move closure captures it by value.
                // files_result is &Result<...> (a Copy reference into group_map, which outlives
                // the scope).  check_indices: Vec<usize> is moved into the closure.
                s.spawn(move || match files_result {
                    Ok(files) => scan_group_collect(
                        checks,
                        &check_indices,
                        files,
                        fd,
                        progress_threshold,
                        progress,
                    ),
                    Err((err_dir, err_msg)) => {
                        // Directory traversal error: surface as explicit violations for every
                        // check in the group (same contract as the old per-group traversal).
                        check_indices
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
                            .collect()
                    }
                })
            })
            .collect();
        for handle in handles {
            all_violations.push(handle.join().expect("scan group thread panicked"));
        }
    });

    // Merge violations back into results (check indices are unique across groups).
    for group_violations in all_violations {
        for (ci, v) in group_violations {
            results[ci].passed = false;
            results[ci].violations.push(v);
        }
    }

    results
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
        let mut files: Vec<PathBuf> = Vec::new();
        let mut traversal_err: Option<(PathBuf, String)> = None;
        for dir in check.directories {
            let full_dir = repo_root.join(dir);
            if full_dir.exists() {
                match collect_files_with_glob_excluding(
                    &full_dir,
                    check.include_glob,
                    check.exclude_globs,
                    &mut files,
                ) {
                    Ok(()) => {}
                    Err(e) => {
                        let msg = format!("read_dir error for {}: {e}", full_dir.display());
                        traversal_err = Some((full_dir, msg));
                        break;
                    }
                }
            }
        }
        let result = match traversal_err {
            Some(err) => Err(err),
            None => {
                files.sort();
                Ok(files)
            }
        };
        group_map.insert(key, (label, result));
    }

    group_map
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
    let mut violations: Vec<(usize, NativeScanViolation)> = Vec::new();

    // Build a flat pattern list with a mapping from pattern ID → check index.
    let mut all_patterns: Vec<&str> = Vec::new();
    let mut pattern_to_check: Vec<usize> = Vec::new();

    for &ci in check_indices {
        for literal in all_checks[ci].literals {
            all_patterns.push(literal);
            pattern_to_check.push(ci);
        }
    }

    if all_patterns.is_empty() {
        return violations;
    }

    // Pre-compute Two-Way critical factorizations for all NegativeLookahead checks.
    //
    // tw_contains recomputes critical_factorization (O(m)) on every call.  For
    // NegativeLookahead checks the pattern is a static &str, so precomputing (l, p)
    // once per check amortizes the O(m) preprocessing across all match occurrences.
    // For a file with K trigger matches this reduces preprocessing from O(K×m) to O(m).
    //
    // Reference: TAOCP Vol. 3, §6.3 — preprocessing amortization principle.
    let mut tw_precomputed: HashMap<usize, (usize, usize)> = HashMap::new();
    for &ci in check_indices {
        if let MatchMode::NegativeLookahead {
            negative_context, ..
        } = all_checks[ci].mode
        {
            if !negative_context.is_empty() {
                tw_precomputed.insert(ci, critical_factorization(negative_context.as_bytes()));
            }
        }
    }

    // Build the Aho-Corasick automaton with explicit DFA mode for O(1) per-character
    // state transitions (vs. NFA's amortized O(k) cost).  All patterns here are short
    // ASCII literals so the DFA state count stays small and fits in L1 cache.
    //
    // Reference: Aho & Corasick (1975), "Efficient string matching: An aid to
    // bibliographic search."  DFA minimisation: Hopcroft (1971), TAOCP Vol. 3 §6.3.
    //
    // Fallback to default (auto-selected NFA/DFA) if the DFA transition table would
    // exceed the crate's internal state limit, ensuring correctness is never sacrificed.
    let ac = match AhoCorasickBuilder::new()
        .kind(Some(AhoCorasickKind::DFA))
        .build(&all_patterns)
        .or_else(|_| AhoCorasick::new(&all_patterns))
    {
        Ok(ac) => ac,
        Err(_) => return violations,
    };

    // Read all files in parallel, then process in deterministic (sorted) order.
    let contents = read_scan_files_parallel(files);
    for (file_path, content_res) in files.iter().zip(contents.into_iter()) {
        // Increment the shared file counter.  Emit progress every `progress_threshold`
        // files so the user sees scan throughput at ~5% intervals regardless of corpus
        // size.  Guard `n > 0` to prevent spurious firing at n=0.
        // Uses Relaxed ordering: approximate count is sufficient for progress display.
        let n = files_done.fetch_add(1, Ordering::Relaxed) + 1;
        if n > 0 && n.is_multiple_of(progress_threshold) {
            progress("native-scan", &format!("{n} files scanned"));
        }

        let content = match content_res {
            Ok(c) => c,
            Err(e) => {
                for &ci in check_indices {
                    let check = &all_checks[ci];
                    if file_is_excluded(file_path, check.exclude_globs) {
                        continue;
                    }
                    violations.push((
                        ci,
                        NativeScanViolation {
                            file: file_path.clone(),
                            line_number: 1,
                            line: format!("read file error for {}: {e}", file_path.display()),
                        },
                    ));
                }
                continue;
            }
        };

        // Build a LineIndex in a single O(n) pass.  All per-match line-context
        // lookups below use O(log L) binary search (TAOCP Vol. 3, §6.2.1
        // Algorithm B) instead of repeated O(n) linear scans.
        let line_idx = LineIndex::new(&content);

        for mat in ac.find_iter(&content) {
            let check_idx = pattern_to_check[mat.pattern().as_usize()];
            let check = &all_checks[check_idx];

            // Skip files excluded by this check.
            if file_is_excluded(file_path, check.exclude_globs) {
                continue;
            }

            let byte_start = mat.start();
            let byte_end = mat.end();

            // Apply mode-specific post-filter.
            let is_violation = match check.mode {
                MatchMode::AnyLiteral { skip_comment_lines } => {
                    !(skip_comment_lines && line_is_comment(&content, &line_idx, byte_start))
                }
                MatchMode::StemWithBoolSuffix => {
                    word_boundary_at_start(&content, byte_start)
                        && matches_bool_suffix(&content, byte_end)
                }
                MatchMode::AnyLiteralAtLineStart { skip_comment_lines } => {
                    if skip_comment_lines && line_is_comment(&content, &line_idx, byte_start) {
                        false
                    } else {
                        only_whitespace_before_on_line(&content, &line_idx, byte_start)
                    }
                }
                MatchMode::NegativeLookahead {
                    negative_context,
                    word_boundary_at_end,
                } => {
                    // Word-boundary check: char at byte_end must not be identifier char.
                    let boundary_ok = if word_boundary_at_end {
                        is_word_boundary_at_end(&content, byte_end)
                    } else {
                        true
                    };
                    if !boundary_ok {
                        false
                    } else {
                        // Violation only if negative_context is absent from the line.
                        // Use LineIndex O(log L) lookups for line boundaries.
                        let line_bytes = line_idx.extract_line(&content, byte_start);
                        if negative_context.is_empty() {
                            false // empty negative_context always suppresses (degenerate)
                        } else {
                            // Use the precomputed critical factorization to avoid O(m)
                            // preprocessing on every match occurrence.
                            // Reference: TAOCP Vol. 3, §6.3 — preprocessing amortization.
                            let precomputed = tw_precomputed[&check_idx];
                            !tw_contains_precomputed(
                                line_bytes,
                                negative_context.as_bytes(),
                                precomputed,
                            )
                        }
                    }
                }
            };

            if is_violation {
                // Check for #[expect(..., reason = "...")] at item scope.
                let matched_literal = all_patterns[mat.pattern().as_usize()];
                if check.name == "forbidden-allow-expect-scan"
                    && is_allowed_expect_with_reason(
                        &content,
                        &line_idx,
                        byte_start,
                        matched_literal,
                    )
                {
                    continue;
                }

                // For cfg_attr patterns, we need an additional check: the line must
                // contain allow( or expect( to be a violation. This avoids false positives
                // on regular cfg_attr usage like #[cfg_attr(test, derive(Debug))].
                if check.name == "forbidden-allow-expect-scan"
                    && is_cfg_attr_literal(matched_literal)
                {
                    let line_bytes = line_idx.extract_line(&content, byte_start);
                    // cfg_attr wrapping expect-with-reason at item scope is allowed
                    if is_allowed_cfg_attr_expect_with_reason(
                        &content,
                        &line_idx,
                        byte_start,
                        matched_literal,
                    ) {
                        continue;
                    }
                    // cfg_attr without allow or expect is not a violation
                    if !line_contains_allow_or_expect(line_bytes) {
                        continue;
                    }
                }

                let line_number = line_idx.line_number(byte_start) + 1;
                let line = String::from_utf8_lossy(line_idx.extract_line(&content, byte_start))
                    .to_string();
                violations.push((
                    check_idx,
                    NativeScanViolation {
                        file: file_path.clone(),
                        line_number,
                        line,
                    },
                ));
            }
        }
    }

    violations
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

// ── Boyer-Moore-Horspool single-pattern search ────────────────────────────────

/// Boyer-Moore-Horspool single-pattern byte search.
///
/// Retained for test cross-validation against `tw_contains` (the production
/// algorithm).  Production code now uses `tw_contains` for O(n) worst-case
/// guarantees; BMH degenerates to O(n×m) on adversarial inputs.
///
/// Preprocessing: O(|alphabet| + |pattern|) — builds a 256-entry bad-character shift table.
/// Search: O(|text| / |pattern|) average, O(|text| × |pattern|) worst-case.
///
/// Reference: Horspool, R.N. (1980). "Practical Fast Searching in Strings."
/// Software: Practice and Experience 10(6): 501–506.
/// See also: TAOCP Vol. 3, §6.3 (String Searching).
#[cfg(test)]
pub(crate) fn bmh_contains(text: &[u8], pattern: &[u8]) -> bool {
    let m = pattern.len();
    if m == 0 {
        return true;
    }
    let n = text.len();
    if m > n {
        return false;
    }
    // Build bad-character shift table.
    // shift[c] = how far to advance when text[i + m - 1] == c and the rightmost
    // occurrence of c in pattern[0..m-1] (excluding the last position) is at index j.
    // Default shift is m (pattern length) when c does not appear in pattern[0..m-1].
    let mut shift = [m; 256];
    for i in 0..m - 1 {
        shift[pattern[i] as usize] = m - 1 - i;
    }
    let mut i = m - 1;
    while i < n {
        let mut j = m - 1;
        let mut k = i;
        while j > 0 && text[k] == pattern[j] {
            k -= 1;
            j -= 1;
        }
        // j == 0 means the inner loop ran to completion (all m chars matched).
        // Must check j == 0 first: if j > 0, the inner loop exited due to mismatch,
        // and text[k] == pattern[0] is a coincidence that must NOT trigger a match.
        if j == 0 && text[k] == pattern[0] {
            return true;
        }
        i += shift[text[i] as usize];
    }
    false
}

// ── Knuth-Morris-Pratt single-pattern search ──────────────────────────────────

/// Knuth-Morris-Pratt single-pattern search.
///
/// Guaranteed O(n + m) worst-case time, O(m) auxiliary space for the failure table.
/// Prefer over BMH when worst-case performance guarantees are required (BMH is
/// O(n × m) worst-case for degenerate inputs like repeated characters).
///
/// # Algorithm
///
/// Implements Algorithm D (failure function construction) and Algorithm E (search)
/// from TAOCP Vol. 3, §6.4 (Knuth, Morris, Pratt, 1977).
///
/// The failure function `fail[i]` gives the length of the longest proper prefix of
/// `needle[0..=i]` that is also a suffix.  During search, on mismatch at position `q`
/// we fall back to `fail[q-1]` rather than restarting from the beginning, ensuring
/// each character in `haystack` is examined at most once.
///
/// Returns `Some(byte_offset)` of the first occurrence, or `None` if not found.
/// An empty needle always matches at position 0 (`Some(0)`).
pub(crate) fn kmp_search(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    let m = needle.len();

    // Build failure (partial-match) table — Algorithm D.
    // fail[i] = length of the longest proper prefix of needle[0..=i] that is
    // also a suffix.  Computed in O(m) time.
    let mut fail = vec![0usize; m];
    let mut k = 0usize;
    for i in 1..m {
        while k > 0 && needle[k] != needle[i] {
            k = fail[k - 1];
        }
        if needle[k] == needle[i] {
            k += 1;
        }
        fail[i] = k;
    }

    // Search — Algorithm E.
    // q = number of characters matched so far.  Each character in haystack is
    // examined at most twice (once forward, once on a failure fallback), giving
    // O(n) search time.
    let mut q = 0usize;
    for (i, &c) in haystack.iter().enumerate() {
        while q > 0 && needle[q] != c {
            q = fail[q - 1];
        }
        if needle[q] == c {
            q += 1;
        }
        if q == m {
            return Some(i + 1 - m);
        }
    }
    None
}

// ── Two-Way single-pattern search ────────────────────────────────────────────

/// Two-Way single-pattern byte search (Crochemore and Lecroq, 1991).
///
/// Implements the canonical two-case formulation from Crochemore & Lecroq,
/// "Handbook of Exact String-Matching Algorithms" (2004), Chapter 26.
///
/// ## Preprocessing — O(m)
/// Computes the critical factorization (l, p) via the lex-maximal suffix
/// algorithm.  No auxiliary arrays beyond two `usize` values.
///
/// ## Search — O(n) worst-case
/// **Case 1 (pattern is p-periodic globally):** uses memory optimisation;
/// O(n) total comparisons.
/// **Case 2 (pattern is NOT p-periodic globally):** slides by max(l,m-l-1)+1
/// on full-mismatch; still O(n) total.
///
/// Reference: Crochemore, M. and Lecroq, P. (1991). "Tight bounds on the
/// complexity of the Two-Way string-matching algorithm." Information Processing
/// Letters, 46(1):1–8. Also TAOCP Vol. 3, §6.3.
///
/// Preferred over BMH for NegativeLookahead because BMH degenerates to O(n×m)
/// on adversarial inputs (e.g. 1000×'a' + 'b' with pattern "aaab"), while
/// Two-Way guarantees O(n) in all cases.
#[cfg(test)]
pub(crate) fn tw_contains(text: &[u8], pattern: &[u8]) -> bool {
    let m = pattern.len();
    if m == 0 {
        return true;
    }
    let n = text.len();
    if m > n {
        return false;
    }

    let (l, p) = critical_factorization(pattern);

    // Does the pattern have (global) period p?
    // Condition: pattern[0..m-p] == pattern[p..m] (shift-by-p self-overlap).
    // Only checkable when m > p; when m <= p the "left part" is the whole pattern
    // and period-p doesn't hold globally — fall through to Case 2.
    let is_periodic = m > p && pattern[..m - p] == pattern[p..];

    if is_periodic {
        // ── Case 1: global period p — use the memory optimisation ──────────────
        // After the suffix scan succeeds and the prefix mismatches, we slide by p
        // and remember `m-p-1` as the last right-half position that is still valid
        // in the new window (due to the period property).  is_periodic guarantees
        // m > p so m-p-1 is a valid usize.
        //
        // Suffix = pattern[l+1..m-1]; Prefix = pattern[0..l].
        let mut j = 0usize;
        let mut memory = usize::MAX; // usize::MAX = sentinel "no memory yet"
        while j + m <= n {
            // Phase 1: scan suffix part (pattern[l+1..m-1]) forward, starting
            // after the already-verified part from the previous window.
            let mut i = l
                .max(if memory == usize::MAX { 0 } else { memory })
                .saturating_add(1);
            while i < m && pattern[i] == text[j + i] {
                i += 1;
            }
            if i < m {
                // Suffix mismatch: shift past the bad position.
                j += i.saturating_sub(l).max(1);
                memory = usize::MAX;
            } else {
                // Phase 2: scan prefix part (pattern[0..=l]) forward.
                // Skip positions known to match from the previous window.
                let start_k = if memory == usize::MAX { 0 } else { memory + 1 };
                let mut k = start_k;
                while k <= l && pattern[k] == text[j + k] {
                    k += 1;
                }
                if k > l {
                    return true; // full match
                }
                j += p;
                memory = m - p - 1; // valid because is_periodic ⟹ m > p ⟹ m-p-1 ≥ 0
            }
        }
    } else {
        // ── Case 2: not globally p-periodic — no memory, larger slide ──────────
        //
        // Scan starts at the critical position l (covering both halves in order).
        // On suffix mismatch at i: slide by (i-l)+1.
        // On full prefix mismatch after suffix match: slide by max(l,m-l-1)+1.
        // Both bounds guarantee O(n) total comparisons.
        let slide = l.max(m.saturating_sub(l + 1)) + 1;
        let mut j = 0usize;
        while j + m <= n {
            // Scan right part of pattern (positions l..m-1) forward from l.
            let mut i = l;
            while i < m && pattern[i] == text[j + i] {
                i += 1;
            }
            if i < m {
                // Right-half mismatch: shift past the bad position.
                j += (i - l) + 1;
            } else {
                // Right half matched; now scan left part (positions 0..l) backward.
                // Using (0..=l).rev() avoids signed arithmetic.
                let full_match = (0..=l).rev().all(|k| pattern[k] == text[j + k]);
                if full_match {
                    return true;
                }
                j += slide;
            }
        }
    }
    false
}

/// Two-Way search with pre-computed critical factorization.
///
/// Identical to `tw_contains` but accepts `(l, p)` from `critical_factorization`,
/// skipping the O(m) preprocessing step.  Use when the same pattern is searched
/// many times (e.g., NegativeLookahead with a static pattern): precompute once per
/// check and call `tw_contains_precomputed` for each match occurrence.
///
/// Reference: TAOCP Vol. 3, §6.3 — preprocessing amortization principle.
/// Crochemore, M. and Lecroq, P. (1991). Two-Way string-matching algorithm.
pub(crate) fn tw_contains_precomputed(
    text: &[u8],
    pattern: &[u8],
    precomputed: (usize, usize),
) -> bool {
    let m = pattern.len();
    if m == 0 {
        return true;
    }
    let n = text.len();
    if m > n {
        return false;
    }
    let (l, p) = precomputed;
    let is_periodic = m > p && pattern[..m - p] == pattern[p..];

    if is_periodic {
        // ── Case 1: global period p — use the memory optimisation ──────────────
        let mut j = 0usize;
        let mut memory = usize::MAX;
        while j + m <= n {
            let mut i = l
                .max(if memory == usize::MAX { 0 } else { memory })
                .saturating_add(1);
            while i < m && pattern[i] == text[j + i] {
                i += 1;
            }
            if i < m {
                j += i.saturating_sub(l).max(1);
                memory = usize::MAX;
            } else {
                let start_k = if memory == usize::MAX { 0 } else { memory + 1 };
                let mut k = start_k;
                while k <= l && pattern[k] == text[j + k] {
                    k += 1;
                }
                if k > l {
                    return true;
                }
                j += p;
                memory = m - p - 1;
            }
        }
    } else {
        // ── Case 2: not globally p-periodic — no memory, larger slide ──────────
        let slide = l.max(m.saturating_sub(l + 1)) + 1;
        let mut j = 0usize;
        while j + m <= n {
            let mut i = l;
            while i < m && pattern[i] == text[j + i] {
                i += 1;
            }
            if i < m {
                j += (i - l) + 1;
            } else {
                let full_match = (0..=l).rev().all(|k| pattern[k] == text[j + k]);
                if full_match {
                    return true;
                }
                j += slide;
            }
        }
    }
    false
}

/// Compute the critical factorization of `pattern` as (split_pos, period).
///
/// Returns the split position `l` (0-indexed, inclusive end of left part) and
/// the period `p` of the right part, such that pattern = pattern[0..=l] ++ pattern[l+1..].
/// The critical position is the later of the two lex-maximal suffix splits.
fn critical_factorization(pattern: &[u8]) -> (usize, usize) {
    let (l1, p1) = max_suffix(pattern, false);
    let (l2, p2) = max_suffix(pattern, true);
    if l1 >= l2 {
        (l1, p1)
    } else {
        (l2, p2)
    }
}

/// Crochemore-Lecroq lex-maximal suffix algorithm.
///
/// Computes the split position and period of the lexicographically largest (or
/// smallest when `rev=true`) suffix of `pattern` in O(m) time with O(1) space.
fn max_suffix(pattern: &[u8], rev: bool) -> (usize, usize) {
    let m = pattern.len();
    let mut ms = usize::MAX; // sentinel: no maximal-suffix candidate yet
    let mut j = 0usize;
    let mut k = 1usize;
    let mut p = 1usize;
    while j + k < m {
        let cmp_j = pattern[j + k];
        let cmp_ms = if ms == usize::MAX {
            pattern[k - 1]
        } else {
            pattern[ms + k]
        };
        let gt = if rev { cmp_j < cmp_ms } else { cmp_j > cmp_ms };
        let lt = if rev { cmp_j > cmp_ms } else { cmp_j < cmp_ms };
        if gt {
            j += k;
            k = 1;
            p = j.wrapping_sub(if ms == usize::MAX { usize::MAX } else { ms });
        } else if lt {
            ms = if ms == usize::MAX { j } else { ms.max(j) };
            j = ms.wrapping_add(1);
            k = 1;
            p = 1;
        } else if k == p {
            j += p;
            k = 1;
        } else {
            k += 1;
        }
    }
    let final_ms = if ms == usize::MAX { 0 } else { ms + 1 };
    (final_ms, p)
}

// ── Diagnostic-level classifier (Aho-Corasick single pass) ───────────────────

/// Result of scanning command output for Cargo/compiler diagnostic prefixes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiagnosticLevel {
    /// No error or warning diagnostic found.
    Clean,
    /// At least one "warning:" prefix found (and no error).
    Warning,
    /// At least one "error:" or "Error:" prefix found.
    Error,
}

impl DiagnosticLevel {
    /// Returns the more severe of two diagnostic levels.
    /// Error > Warning > Clean.
    pub(crate) fn max_level(self, other: DiagnosticLevel) -> DiagnosticLevel {
        match (self, other) {
            (DiagnosticLevel::Error, _) | (_, DiagnosticLevel::Error) => DiagnosticLevel::Error,
            (DiagnosticLevel::Warning, _) | (_, DiagnosticLevel::Warning) => {
                DiagnosticLevel::Warning
            }
            _ => DiagnosticLevel::Clean,
        }
    }
}

// Pattern IDs for the diagnostic Aho-Corasick automaton.
const DIAG_PAT_ERROR_LC: usize = 0; // "error:"
const DIAG_PAT_ERROR_TC: usize = 1; // "Error:"
const DIAG_PAT_ERROR_BRACKET: usize = 2; // "error[" (rustc / clippy: error[E0XXX])
const DIAG_PAT_WARNING_LC: usize = 3; // "warning:"
const DIAG_PAT_WARNING_TC: usize = 4; // "Warning:" (defensive; kept for completeness)
const DIAG_PAT_WARNING_BRACKET: usize = 5; // "warning[" (rustc warnings can use this form)

static DIAG_PATTERNS: &[&str] = &[
    "error:", "Error:", "error[", "warning:", "Warning:", "warning[",
];

/// Process-global cached Aho-Corasick automaton for diagnostic-prefix detection.
///
/// Built exactly once via `OnceLock` (Knuth, TAOCP Vol. 2 §4.3: avoid redundant
/// computation by memoising results that are guaranteed identical on every call).
/// The automaton is constructed from the static `DIAG_PATTERNS` slice which is
/// never mutated, so the cached value is correct for the entire process lifetime.
static DIAG_AC: OnceLock<AhoCorasick> = OnceLock::new();

fn diag_ac() -> &'static AhoCorasick {
    // Explicit DFA mode: 6 short ASCII patterns → tiny state count, O(1) per-character
    // transitions (vs. NFA's amortised O(k) cost). The DFA fits comfortably in L1 cache.
    // Reference: Hopcroft (1971) DFA minimisation; TAOCP Vol. 3 §6.3.
    // Fallback to Auto (NFA/hybrid) if the crate rejects the DFA build (never expected
    // for 6 small ASCII patterns, but correctness must not be sacrificed for speed).
    DIAG_AC.get_or_init(|| {
        AhoCorasickBuilder::new()
            .kind(Some(AhoCorasickKind::DFA))
            .build(DIAG_PATTERNS)
            .unwrap_or_else(|_| {
                AhoCorasick::new(DIAG_PATTERNS).expect("static diagnostic patterns are valid")
            })
    })
}

/// Scan command output (stdout or stderr) for diagnostic-level prefixes in O(n+m).
///
/// Uses a single Aho-Corasick pass (Aho & Corasick, 1975) over the raw bytes.
/// For each match, verifies the pattern appears at the start of a trimmed line
/// (equivalent to `line.trim_start().starts_with(pattern)`) via a LineIndex O(log L)
/// lookup (TAOCP Vol. 3 §6.2.1 Algorithm B).
///
/// The Aho-Corasick automaton is constructed once per process via `OnceLock`
/// and reused on every call, eliminating redundant O(m) construction overhead.
///
/// Returns `DiagnosticLevel::Error` if any error pattern is found at line start,
/// `DiagnosticLevel::Warning` if any warning pattern (but no error) is found,
/// `DiagnosticLevel::Clean` otherwise.
pub fn scan_has_diagnostic_prefix(text: &str) -> DiagnosticLevel {
    if text.is_empty() {
        return DiagnosticLevel::Clean;
    }
    let bytes = text.as_bytes();
    let line_idx = LineIndex::new(bytes);
    let ac = diag_ac();
    let mut level = DiagnosticLevel::Clean;
    for mat in ac.find_iter(bytes) {
        let byte_start = mat.start();
        // Check that the match is at the start of the trimmed line.
        let line_start = line_idx.line_start(byte_start);
        let prefix_bytes = &bytes[line_start..byte_start];
        let all_ws = prefix_bytes.iter().all(|&b| b.is_ascii_whitespace());
        if !all_ws {
            continue; // match is not at line start after trim
        }
        match mat.pattern().as_usize() {
            DIAG_PAT_ERROR_LC | DIAG_PAT_ERROR_TC | DIAG_PAT_ERROR_BRACKET => {
                return DiagnosticLevel::Error
            }
            DIAG_PAT_WARNING_LC | DIAG_PAT_WARNING_TC | DIAG_PAT_WARNING_BRACKET => {
                level = DiagnosticLevel::Warning;
            }
            _ => {}
        }
    }
    level
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn make_temp_dir(name: &str) -> PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let base = std::env::temp_dir().join(format!("xtask-scanner-{name}-{unique}"));
        fs::create_dir_all(&base).unwrap();
        base
    }

    fn write_file(dir: &Path, rel_path: &str, content: &str) {
        let full = dir.join(rel_path);
        if let Some(parent) = full.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(full, content).unwrap();
    }

    // ── LineIndex unit tests ──────────────────────────────────────────────────

    mod line_index_tests {
        use super::super::LineIndex;

        #[test]
        fn test_empty_content_line_number() {
            let idx = LineIndex::new(&[]);
            assert_eq!(idx.line_number(0), 0);
        }

        #[test]
        fn test_empty_content_line_start() {
            let idx = LineIndex::new(&[]);
            assert_eq!(idx.line_start(0), 0);
        }

        #[test]
        fn test_empty_content_line_end() {
            let idx = LineIndex::new(&[]);
            assert_eq!(idx.line_end(0), 0);
        }

        #[test]
        fn test_single_line_no_trailing_newline_line_number() {
            let content = b"hello";
            let idx = LineIndex::new(content);
            assert_eq!(idx.line_number(0), 0);
            assert_eq!(idx.line_number(4), 0);
        }

        #[test]
        fn test_single_line_no_trailing_newline_line_start() {
            let content = b"hello";
            let idx = LineIndex::new(content);
            assert_eq!(idx.line_start(0), 0);
            assert_eq!(idx.line_start(4), 0);
        }

        #[test]
        fn test_single_line_no_trailing_newline_line_end() {
            let content = b"hello";
            let idx = LineIndex::new(content);
            assert_eq!(idx.line_end(0), 5);
            assert_eq!(idx.line_end(4), 5);
        }

        #[test]
        fn test_single_newline_only() {
            // b"\n": offset 0 is before the newline (line 0), offset 1 is after (line 1).
            let content = b"\n";
            let idx = LineIndex::new(content);
            assert_eq!(idx.line_number(0), 0, "offset 0 must be on line 0");
            assert_eq!(
                idx.line_number(1),
                1,
                "offset 1 (after newline) must be on line 1"
            );
        }

        #[test]
        fn test_two_lines_line_number() {
            // b"abc\ndef": newline at offset 3.
            let content = b"abc\ndef";
            let idx = LineIndex::new(content);
            assert_eq!(idx.line_number(0), 0);
            assert_eq!(idx.line_number(2), 0);
            assert_eq!(idx.line_number(3), 0, "newline itself is on line 0");
            assert_eq!(idx.line_number(4), 1, "char after newline is on line 1");
            assert_eq!(idx.line_number(6), 1);
        }

        #[test]
        fn test_two_lines_line_start() {
            let content = b"abc\ndef";
            let idx = LineIndex::new(content);
            assert_eq!(idx.line_start(0), 0);
            assert_eq!(idx.line_start(5), 4, "line 'def' starts at offset 4");
        }

        #[test]
        fn test_two_lines_line_end() {
            let content = b"abc\ndef";
            let idx = LineIndex::new(content);
            assert_eq!(idx.line_end(0), 3, "line 'abc' ends at newline offset 3");
            assert_eq!(idx.line_end(5), 7, "line 'def' ends at content_len 7");
        }

        #[test]
        fn test_offset_exactly_at_newline() {
            // Newline is at offset 3; line_start(3) should be 0 (still on line 0).
            let content = b"abc\ndef";
            let idx = LineIndex::new(content);
            assert_eq!(idx.line_start(3), 0);
            assert_eq!(idx.line_end(3), 3);
        }

        #[test]
        fn test_last_line_no_trailing_newline_line_end() {
            let content = b"first\nsecond";
            let idx = LineIndex::new(content);
            // "second" starts at offset 6, content_len == 12.
            assert_eq!(idx.line_end(6), 12);
            assert_eq!(idx.line_end(11), 12);
        }

        #[test]
        fn test_extract_line_returns_correct_bytes() {
            let content = b"line one\nline two\nline three";
            let idx = LineIndex::new(content);
            assert_eq!(idx.extract_line(content, 0), b"line one");
            assert_eq!(idx.extract_line(content, 9), b"line two");
            assert_eq!(idx.extract_line(content, 18), b"line three");
        }

        #[test]
        fn test_line_number_monotonically_non_decreasing() {
            let content = b"a\nb\nc\nd";
            let idx = LineIndex::new(content);
            let mut prev = 0usize;
            for i in 0..content.len() {
                let ln = idx.line_number(i);
                assert!(
                    ln >= prev,
                    "line_number must be non-decreasing: offset {i}, got {ln}, prev {prev}"
                );
                prev = ln;
            }
        }

        #[test]
        fn test_multiline_with_trailing_newline() {
            let content = b"alpha\nbeta\n";
            let idx = LineIndex::new(content);
            // Trailing newline at offset 10; offset 11 is on an empty line 2.
            assert_eq!(
                idx.line_number(10),
                1,
                "newline at offset 10 is still on line 1"
            );
            assert_eq!(idx.line_number(11), 2, "after trailing newline is line 2");
            assert_eq!(idx.line_start(11), 11);
            assert_eq!(idx.line_end(11), 11); // empty last line, content_len == 11
        }
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    fn simple_check(name: &'static str, literals: &'static [&'static str]) -> NativeScanCheck {
        NativeScanCheck {
            name,
            literals,
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        }
    }

    // ── basic literal matching ────────────────────────────────────────────────

    #[test]
    fn test_literal_match_found_returns_failure() {
        let dir = make_temp_dir("literal-match");
        write_file(&dir, "src/lib.rs", "let forbidden_pattern = true;\n");

        let check = NativeScanCheck {
            name: "test-simple",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(!results[0].passed, "should fail when pattern found");
        assert!(!results[0].violations.is_empty());
        assert_eq!(results[0].violations[0].line_number, 1);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_literal_match_not_found_returns_pass() {
        let dir = make_temp_dir("literal-no-match");
        write_file(&dir, "src/lib.rs", "let safe_code = true;\n");

        let check = NativeScanCheck {
            name: "test-simple",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(results[0].passed);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_empty_directory_returns_pass() {
        let dir = make_temp_dir("empty-dir");
        fs::create_dir_all(dir.join("src")).unwrap();

        let check = NativeScanCheck {
            name: "test-empty",
            literals: &["anything"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(results[0].passed);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_missing_directory_returns_pass() {
        let dir = make_temp_dir("missing-dir");
        // Do NOT create src/ subdirectory.

        let check = NativeScanCheck {
            name: "test-missing",
            literals: &["anything"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(results[0].passed);

        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn test_unreadable_directory_causes_failure_not_silent_pass() {
        use std::os::unix::fs::PermissionsExt;

        let dir = make_temp_dir("unreadable-dir");
        write_file(&dir, "src/lib.rs", "forbidden_pattern\n");

        // Make the directory unreadable so directory walking hits a read_dir error.
        let src_dir = dir.join("src");
        let mut perms = fs::metadata(&src_dir).unwrap().permissions();
        perms.set_mode(0o000);
        fs::set_permissions(&src_dir, perms).unwrap();

        let check = NativeScanCheck {
            name: "test-unreadable",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});

        // Restore permissions so cleanup works.
        let mut perms_restore = fs::metadata(&src_dir).unwrap().permissions();
        perms_restore.set_mode(0o755);
        let _ = fs::set_permissions(&src_dir, perms_restore);

        assert!(
            !results[0].passed,
            "unreadable directories must fail the scan (not silently pass)"
        );
        assert!(
            results[0]
                .violations
                .iter()
                .any(|v| v.line.contains("read_dir") || v.line.contains("Permission")),
            "expected an explicit read_dir error violation, got: {}",
            results[0]
                .violations
                .iter()
                .map(|v| v.line.as_str())
                .collect::<Vec<_>>()
                .join(" | ")
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn test_unreadable_file_causes_failure_not_silent_pass() {
        use std::os::unix::fs::PermissionsExt;

        let dir = make_temp_dir("unreadable-file");
        write_file(&dir, "src/lib.rs", "forbidden_pattern\n");

        // Make the file unreadable so the scan must surface a read error.
        let src_file = dir.join("src/lib.rs");
        let mut perms = fs::metadata(&src_file).unwrap().permissions();
        perms.set_mode(0o000);
        fs::set_permissions(&src_file, perms).unwrap();

        let check = NativeScanCheck {
            name: "test-unreadable-file",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});

        // Restore permissions so cleanup works.
        let mut perms_restore = fs::metadata(&src_file).unwrap().permissions();
        perms_restore.set_mode(0o644);
        let _ = fs::set_permissions(&src_file, perms_restore);

        assert!(
            !results[0].passed,
            "unreadable files must fail the scan (not silently pass)"
        );
        assert!(
            results[0]
                .violations
                .iter()
                .any(|v| v.line.contains("read") || v.line.contains("Permission")),
            "expected an explicit read-file error violation, got: {}",
            results[0]
                .violations
                .iter()
                .map(|v| v.line.as_str())
                .collect::<Vec<_>>()
                .join(" | ")
        );

        let _ = fs::remove_dir_all(&dir);
    }

    // ── comment-line exclusion ────────────────────────────────────────────────

    #[test]
    fn test_comment_line_skipped_when_flag_set() {
        let dir = make_temp_dir("comment-skip");
        write_file(&dir, "src/lib.rs", "// forbidden_pattern\n");

        let check = NativeScanCheck {
            name: "test-comment-skip",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "comment-line match must not trigger failure"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_comment_line_not_skipped_when_flag_unset() {
        let dir = make_temp_dir("comment-noskip");
        write_file(&dir, "src/lib.rs", "// forbidden_pattern\n");

        let check = NativeScanCheck {
            name: "test-comment-noskip",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "comment-line match must trigger failure"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_indented_comment_line_skipped() {
        let dir = make_temp_dir("indented-comment");
        write_file(&dir, "src/lib.rs", "    // forbidden_pattern\n");

        let check = NativeScanCheck {
            name: "test-indented-comment",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "indented comment-line match must not trigger failure"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    // ── StemWithBoolSuffix ────────────────────────────────────────────────────

    #[test]
    fn test_stem_with_bool_suffix_matches() {
        let dir = make_temp_dir("stem-bool-match");
        write_file(&dir, "src/lib.rs", "fn foo(test_mode: bool) {}\n");

        let check = NativeScanCheck {
            name: "test-stem",
            literals: &["test_mode"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::StemWithBoolSuffix,
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(!results[0].passed, "test_mode: bool must trigger failure");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_stem_with_bool_suffix_no_match_without_bool() {
        let dir = make_temp_dir("stem-no-bool");
        write_file(&dir, "src/lib.rs", "let test_mode = false;\n");

        let check = NativeScanCheck {
            name: "test-stem-nobool",
            literals: &["test_mode"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::StemWithBoolSuffix,
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "test_mode without ': bool' must not trigger failure"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_stem_with_bool_suffix_with_spaces() {
        let dir = make_temp_dir("stem-bool-spaces");
        write_file(&dir, "src/lib.rs", "fn foo(test_mode : bool) {}\n");

        let check = NativeScanCheck {
            name: "test-stem-spaces",
            literals: &["test_mode"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::StemWithBoolSuffix,
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "test_mode : bool (spaces) must trigger failure"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_stem_with_bool_suffix_with_newline_whitespace() {
        // Regression: formatting can insert newlines between the stem and `: bool`.
        // This should still match the intended `\s*:\s*bool` suffix.
        let dir = make_temp_dir("stem-bool-newline");
        write_file(&dir, "src/lib.rs", "fn foo(test_mode\n: bool) {}\n");

        let check = NativeScanCheck {
            name: "test-stem-newline",
            literals: &["test_mode"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::StemWithBoolSuffix,
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "test_mode\\n: bool must trigger failure (whitespace includes newlines)"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_stem_word_boundary_prevents_false_positive() {
        // "is_testing_mode: bool" should NOT trigger the "is_test" stem check
        // because the char before "testing_mode" is "is_", forming a longer identifier.
        let dir = make_temp_dir("stem-word-boundary");
        write_file(&dir, "src/lib.rs", "fn foo(is_testing_mode: bool) {}\n");

        let check = NativeScanCheck {
            name: "test-boundary",
            literals: &["is_test"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::StemWithBoolSuffix,
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "is_testing_mode: bool must NOT trigger is_test stem check"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    // ── file exclusion globs ──────────────────────────────────────────────────

    #[test]
    fn test_excluded_file_not_scanned() {
        let dir = make_temp_dir("exclude-file");
        write_file(&dir, "src/_TEMPLATE.rs", "forbidden_pattern\n");
        write_file(&dir, "src/lib.rs", "safe\n");

        let check = NativeScanCheck {
            name: "test-exclude",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &["_TEMPLATE.rs"],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(results[0].passed, "_TEMPLATE.rs must be excluded from scan");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_path_component_exclude_glob() {
        let dir = make_temp_dir("exclude-path-component");
        write_file(&dir, "src/tests/test_foo.rs", "forbidden_pattern\n");
        write_file(&dir, "src/lib.rs", "safe\n");

        let check = NativeScanCheck {
            name: "test-path-exclude",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &["**/tests/**"],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "files under tests/ must be excluded from scan"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    // ── multi-check isolation ─────────────────────────────────────────────────

    #[test]
    fn test_two_checks_independent_results() {
        let dir = make_temp_dir("multi-check");
        write_file(&dir, "src/lib.rs", "pattern_a\n");

        let checks = [
            simple_check("check-a", &["pattern_a"]),
            simple_check("check-b", &["pattern_b"]),
        ];

        let results = run_native_scan_checks_reporting(&dir, &checks, &|_, _| {});
        assert!(!results[0].passed, "check-a must fail (pattern_a present)");
        assert!(results[1].passed, "check-b must pass (pattern_b absent)");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_result_order_matches_input_order() {
        let dir = make_temp_dir("result-order");
        write_file(&dir, "src/lib.rs", "alpha beta\n");

        let checks = [
            simple_check("first", &["alpha"]),
            simple_check("second", &["beta"]),
            simple_check("third", &["gamma"]),
        ];

        let results = run_native_scan_checks_reporting(&dir, &checks, &|_, _| {});
        assert_eq!(results[0].check_name, "first");
        assert_eq!(results[1].check_name, "second");
        assert_eq!(results[2].check_name, "third");
        assert!(!results[0].passed);
        assert!(!results[1].passed);
        assert!(results[2].passed);

        let _ = fs::remove_dir_all(&dir);
    }

    // ── directory grouping ────────────────────────────────────────────────────

    #[test]
    fn test_two_checks_same_directory_both_detected() {
        // Two checks with the same directory are grouped: files read once,
        // but both patterns are detected.
        let dir = make_temp_dir("same-dir-group");
        write_file(&dir, "src/lib.rs", "pattern_x\npattern_y\n");

        let checks = [
            simple_check("check-x", &["pattern_x"]),
            simple_check("check-y", &["pattern_y"]),
        ];

        let results = run_native_scan_checks_reporting(&dir, &checks, &|_, _| {});
        assert!(!results[0].passed);
        assert!(!results[1].passed);

        let _ = fs::remove_dir_all(&dir);
    }

    // ── alternation patterns ──────────────────────────────────────────────────

    #[test]
    fn test_any_of_multiple_literals_triggers_failure() {
        let dir = make_temp_dir("alternation");
        write_file(&dir, "src/lib.rs", "let skip_auth = true;\n");

        let check = NativeScanCheck {
            name: "test-alternation",
            literals: &[
                "skip_validation",
                "skip_verify",
                "skip_check",
                "skip_auth",
                "skip_api",
            ],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::StemWithBoolSuffix,
        };

        // skip_auth = true (not `: bool`) → must NOT match
        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(results[0].passed, "skip_auth without ': bool' must pass");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_any_of_multiple_literals_with_bool_triggers_failure() {
        let dir = make_temp_dir("alternation-bool");
        write_file(&dir, "src/lib.rs", "fn f(skip_auth: bool) {}\n");

        let check = NativeScanCheck {
            name: "test-alternation-bool",
            literals: &[
                "skip_validation",
                "skip_verify",
                "skip_check",
                "skip_auth",
                "skip_api",
            ],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::StemWithBoolSuffix,
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(!results[0].passed, "skip_auth: bool must fail");

        let _ = fs::remove_dir_all(&dir);
    }

    // ── violation details ─────────────────────────────────────────────────────

    #[test]
    fn test_violation_contains_correct_line_number() {
        let dir = make_temp_dir("line-number");
        write_file(
            &dir,
            "src/lib.rs",
            "fn foo() {}\nfn bar() {}\nforbidden_here\nfn baz() {}\n",
        );

        let check = NativeScanCheck {
            name: "test-lineno",
            literals: &["forbidden_here"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(!results[0].passed);
        assert_eq!(results[0].violations[0].line_number, 3);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_violation_contains_line_text() {
        let dir = make_temp_dir("line-text");
        write_file(&dir, "src/lib.rs", "    let forbidden_pattern = 42;\n");

        let check = NativeScanCheck {
            name: "test-linetext",
            literals: &["forbidden_pattern"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(!results[0].passed);
        assert!(results[0].violations[0].line.contains("forbidden_pattern"));

        let _ = fs::remove_dir_all(&dir);
    }

    // ── AnyLiteralAtLineStart ─────────────────────────────────────────────────

    #[test]
    fn test_line_start_literal_at_column_zero_is_violation() {
        let dir = make_temp_dir("line-start-col0");
        write_file(&dir, "src/lib.rs", "#[allow(clippy::foo)]\nfn foo() {}\n");

        let check = NativeScanCheck {
            name: "test-line-start-col0",
            literals: &["#[allow("],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "#[allow( at column 0 must trigger violation"
        );
        assert_eq!(results[0].violations[0].line_number, 1);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_line_start_literal_with_leading_spaces_is_violation() {
        let dir = make_temp_dir("line-start-spaces");
        write_file(&dir, "src/lib.rs", "    #[allow(clippy::foo)]\n");

        let check = NativeScanCheck {
            name: "test-line-start-spaces",
            literals: &["#[allow("],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "#[allow( after leading spaces must trigger violation"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_line_start_literal_inline_not_violation() {
        let dir = make_temp_dir("line-start-inline");
        // Inline usage: non-whitespace before #[allow( — not a line-start attribute.
        write_file(&dir, "src/lib.rs", "foo(#[allow(clippy::foo)] bar)\n");

        let check = NativeScanCheck {
            name: "test-line-start-inline",
            literals: &["#[allow("],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "#[allow( inline (after non-whitespace) must NOT trigger violation"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_line_start_comment_line_skipped_when_flag() {
        let dir = make_temp_dir("line-start-comment");
        write_file(&dir, "src/lib.rs", "// #[allow(clippy::foo)]\n");

        let check = NativeScanCheck {
            name: "test-line-start-comment",
            literals: &["#[allow("],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "comment-line #[allow( must be skipped when skip_comment_lines=true"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_rejects_allow_even_with_cfg_test() {
        let dir = make_temp_dir("line-start-large-stack-test-allow");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#[cfg(test)]\n#[allow(clippy::large_stack_frames)]\nmod tests {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "#[allow(clippy::large_stack_frames)] must be rejected even when preceded by #[cfg(test)] — use #[expect(..., reason=...)] instead"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_rejects_file_scope_large_stack_frames_in_tests_tree() {
        let dir = make_temp_dir("line-start-large-stack-tests-tree-inner-allow");
        write_file(
            &dir,
            "tests/integration_tests/sample.rs",
            "#![allow(clippy::large_stack_frames)]\nfn sample() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
            directories: &["tests"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "file-scope large_stack_frames allow should not be exempt just because the file is under tests/"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_catches_cfg_attr_allow() {
        let dir = make_temp_dir("cfg-attr-allow");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#[cfg_attr(test, allow(clippy::large_stack_frames))]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &[
                "#[allow(",
                "#![allow(",
                "#[expect(",
                "#![expect(",
                "#[cfg_attr(",
                "#![cfg_attr(",
            ],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "cfg_attr wrapping allow(...) must be detected as a violation"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_catches_crate_level_cfg_attr_allow() {
        let dir = make_temp_dir("crate-cfg-attr-allow");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#![cfg_attr(test, allow(clippy::large_stack_frames))]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &[
                "#[allow(",
                "#![allow(",
                "#[expect(",
                "#![expect(",
                "#[cfg_attr(",
                "#![cfg_attr(",
            ],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "crate-level #![cfg_attr(..., allow(...))] must be detected as a violation"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_skips_cfg_attr_without_allow_or_expect() {
        let dir = make_temp_dir("cfg-attr-no-allow");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#[cfg_attr(test, derive(Debug))]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &[
                "#[allow(",
                "#![allow(",
                "#[expect(",
                "#![expect(",
                "#[cfg_attr(",
                "#![cfg_attr(",
            ],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "cfg_attr without allow/expect must NOT be flagged as a violation (false positive)"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_catches_cfg_attr_expect() {
        let dir = make_temp_dir("cfg-attr-expect");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#[cfg_attr(test, expect(clippy::large_stack_frames))]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &[
                "#[allow(",
                "#![allow(",
                "#[expect(",
                "#![expect(",
                "#[cfg_attr(",
                "#![cfg_attr(",
            ],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "cfg_attr wrapping expect(...) must be detected as a violation"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_allows_expect_with_reason() {
        let dir = make_temp_dir("expect-with-reason-allowed");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#[expect(clippy::some_lint, reason = \"proc-macro output from derive_more\")]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "#[expect(..., reason = \"...\")] at item scope should be allowed"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_blocks_expect_without_reason() {
        let dir = make_temp_dir("expect-without-reason-blocked");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#[expect(clippy::some_lint)]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "#[expect(...)] without reason should be blocked"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_blocks_inner_expect_with_reason() {
        let dir = make_temp_dir("inner-expect-blocked");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#![expect(clippy::some_lint, reason = \"external\")]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "#![expect(...)] (inner attribute) should always be blocked regardless of reason"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_blocks_expect_with_empty_reason() {
        let dir = make_temp_dir("empty-reason-blocked");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#[expect(clippy::some_lint, reason = \"\")]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "#[expect(..., reason = \"\")] with empty reason should be blocked"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_allows_cfg_attr_expect_with_reason() {
        let dir = make_temp_dir("cfg-attr-expect-reason-allowed");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#[cfg_attr(test, expect(clippy::some_lint, reason = \"proc-macro\"))]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &[
                "#[allow(",
                "#![allow(",
                "#[expect(",
                "#![expect(",
                "#[cfg_attr(",
                "#![cfg_attr(",
            ],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "#[cfg_attr(..., expect(..., reason = \"...\"))] should be allowed"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_forbidden_allow_expect_scan_blocks_cfg_attr_expect_without_reason() {
        let dir = make_temp_dir("cfg-attr-expect-no-reason-blocked");
        write_file(
            &dir,
            "ralph-workflow/src/lib.rs",
            "#[cfg_attr(test, expect(clippy::some_lint))]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &[
                "#[allow(",
                "#![allow(",
                "#[expect(",
                "#![expect(",
                "#[cfg_attr(",
                "#![cfg_attr(",
            ],
            directories: &["ralph-workflow/src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "#[cfg_attr(..., expect(...))] without reason should be blocked"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_line_has_nonempty_reason() {
        // Test cases for the line_has_nonempty_reason helper
        use super::line_has_nonempty_reason;

        // Valid cases with non-empty reason
        assert!(
            line_has_nonempty_reason(b"#[expect(clippy::foo, reason = \"proc-macro\")]"),
            "reason = \"proc-macro\" should be valid"
        );
        assert!(
            line_has_nonempty_reason(b"#[expect(clippy::foo, reason=\"proc-macro\")]"),
            "reason=\"proc-macro\" should be valid (no space)"
        );
        assert!(
            line_has_nonempty_reason(b"#[expect(clippy::foo, reason =  \"proc-macro\")]"),
            "reason =  \"proc-macro\" should be valid (extra space)"
        );

        // Invalid cases - no reason
        assert!(
            !line_has_nonempty_reason(b"#[expect(clippy::foo)]"),
            "no reason should be invalid"
        );

        // Invalid cases - empty reason
        assert!(
            !line_has_nonempty_reason(b"#[expect(clippy::foo, reason = \"\")]"),
            "empty reason should be invalid"
        );

        // Invalid cases - no quote
        assert!(
            !line_has_nonempty_reason(b"#[expect(clippy::foo, reason = )]"),
            "no quote should be invalid"
        );

        // Valid even with #[allow] prefix (helper doesn't filter)
        assert!(
            line_has_nonempty_reason(b"#[allow(clippy::foo, reason = \"test\")]"),
            "reason in #[allow] should be detected by helper"
        );
    }

    #[test]
    fn test_line_start_violation_contains_correct_line_number() {
        let dir = make_temp_dir("line-start-lineno");
        write_file(
            &dir,
            "src/lib.rs",
            "fn foo() {}\nfn bar() {}\n#[allow(dead_code)]\nfn baz() {}\n",
        );

        let check = NativeScanCheck {
            name: "test-line-start-lineno",
            literals: &["#[allow("],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(!results[0].passed);
        assert_eq!(
            results[0].violations[0].line_number, 3,
            "violation must be on line 3"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    // ── NegativeLookahead ─────────────────────────────────────────────────────

    #[test]
    fn test_negative_lookahead_no_context_is_violation() {
        let dir = make_temp_dir("neg-lookahead-no-ctx");
        write_file(&dir, "src/lib.rs", "#[ignore]\nfn slow_test() {}\n");

        let check = NativeScanCheck {
            name: "test-neg-no-ctx",
            literals: &["#[ignore"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::NegativeLookahead {
                negative_context: "https://",
                word_boundary_at_end: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "#[ignore] without URL must trigger violation"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_negative_lookahead_with_context_is_pass() {
        let dir = make_temp_dir("neg-lookahead-with-ctx");
        write_file(
            &dir,
            "src/lib.rs",
            "#[ignore] // https://example.com/issue/123\nfn slow_test() {}\n",
        );

        let check = NativeScanCheck {
            name: "test-neg-with-ctx",
            literals: &["#[ignore"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::NegativeLookahead {
                negative_context: "https://",
                word_boundary_at_end: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "#[ignore] with URL on same line must NOT trigger violation"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_negative_lookahead_word_boundary_prevents_match() {
        let dir = make_temp_dir("neg-lookahead-boundary");
        // #[ignore_slow] — the char after "ignore" is '_', an identifier char.
        write_file(&dir, "src/lib.rs", "#[ignore_slow]\nfn test() {}\n");

        let check = NativeScanCheck {
            name: "test-neg-boundary",
            literals: &["#[ignore"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::NegativeLookahead {
                negative_context: "https://",
                word_boundary_at_end: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "#[ignore_slow] must NOT trigger when word_boundary_at_end=true"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_negative_lookahead_no_boundary_matches_any() {
        let dir = make_temp_dir("neg-lookahead-no-boundary");
        // Without word-boundary check, #[ignore_slow] DOES trigger.
        write_file(&dir, "src/lib.rs", "#[ignore_slow]\nfn test() {}\n");

        let check = NativeScanCheck {
            name: "test-neg-no-boundary",
            literals: &["#[ignore"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::NegativeLookahead {
                negative_context: "https://",
                word_boundary_at_end: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "#[ignore_slow] MUST trigger when word_boundary_at_end=false"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_negative_lookahead_context_anywhere_on_line_is_pass() {
        let dir = make_temp_dir("neg-lookahead-ctx-anywhere");
        // URL appears before the #[ignore] on the same line.
        write_file(
            &dir,
            "src/lib.rs",
            "// see https://example.com #[ignore]\nfn test() {}\n",
        );

        let check = NativeScanCheck {
            name: "test-neg-ctx-anywhere",
            literals: &["#[ignore"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::NegativeLookahead {
                negative_context: "https://",
                word_boundary_at_end: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "https:// before #[ignore] on same line must NOT trigger violation"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    // ── parallel groups ───────────────────────────────────────────────────────

    #[test]
    fn test_parallel_groups_return_same_results_as_single_group() {
        // Two checks in DIFFERENT directories → two separate scan groups.
        // Verifies that parallel group scanning produces the same violations as
        // running each check individually.
        let dir = make_temp_dir("parallel-groups");
        write_file(&dir, "src/lib.rs", "pattern_alpha\n");
        write_file(&dir, "other/lib.rs", "pattern_beta\n");

        let checks = [
            NativeScanCheck {
                name: "check-alpha",
                literals: &["pattern_alpha"],
                directories: &["src"],
                include_glob: "*.rs",
                exclude_globs: &[],
                mode: MatchMode::AnyLiteral {
                    skip_comment_lines: false,
                },
            },
            NativeScanCheck {
                name: "check-beta",
                literals: &["pattern_beta"],
                directories: &["other"],
                include_glob: "*.rs",
                exclude_globs: &[],
                mode: MatchMode::AnyLiteral {
                    skip_comment_lines: false,
                },
            },
        ];

        let results = run_native_scan_checks_reporting(&dir, &checks, &|_, _| {});
        assert!(!results[0].passed, "check-alpha must find pattern_alpha");
        assert!(!results[1].passed, "check-beta must find pattern_beta");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_audit_ignore_has_url_is_in_native_scan_checks() {
        assert!(
            NATIVE_SCAN_CHECKS
                .iter()
                .any(|c| c.name == "audit-ignore-has-url"),
            "NATIVE_SCAN_CHECKS must include audit-ignore-has-url"
        );
    }

    #[test]
    fn test_forbidden_allow_expect_is_in_native_scan_checks() {
        assert!(
            NATIVE_SCAN_CHECKS
                .iter()
                .any(|c| c.name == "forbidden-allow-expect-scan"),
            "NATIVE_SCAN_CHECKS must include forbidden-allow-expect-scan"
        );
    }

    #[test]
    fn test_forbidden_allow_expect_scan_covers_gui_rust() {
        let check = NATIVE_SCAN_CHECKS
            .iter()
            .find(|check| check.name == "forbidden-allow-expect-scan")
            .expect("forbidden-allow-expect-scan must be present");

        assert!(
            check.directories.contains(&"test-helpers/src"),
            "forbidden-allow-expect-scan must cover test-helpers/src"
        );
        assert!(
            check.directories.contains(&"ralph-gui"),
            "forbidden-allow-expect-scan must cover ralph-gui so GUI Rust files are scanned"
        );
        assert!(
            check.exclude_globs.contains(&"**/node_modules/**"),
            "forbidden-allow-expect-scan must exclude transient node_modules trees"
        );
        assert!(
            check.exclude_globs.contains(&"**/dist/**"),
            "forbidden-allow-expect-scan must exclude transient frontend build outputs"
        );
    }

    #[test]
    fn test_forbidden_allow_expect_scan_covers_lints_directory() {
        let check = NATIVE_SCAN_CHECKS
            .iter()
            .find(|check| check.name == "forbidden-allow-expect-scan")
            .expect("forbidden-allow-expect-scan must be present");

        assert!(
            check.directories.contains(&"lints"),
            "forbidden-allow-expect-scan must cover lints/ directory"
        );
        assert!(
            check.exclude_globs.contains(&"**/ui/**"),
            "forbidden-allow-expect-scan must exclude lints/*/ui/ test fixtures"
        );
    }

    #[test]
    fn test_forbidden_allow_expect_scan_excludes_lints_ui_directory() {
        let dir = make_temp_dir("lints-ui-exclusion");
        write_file(
            &dir,
            "lints/fake_lint/ui/violating.rs",
            "#![allow(clippy::large_stack_frames)]\nfn foo() {}\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
            directories: &["lints"],
            include_glob: "*.rs",
            exclude_globs: &["**/ui/**"],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "forbidden-allow-expect-scan should NOT flag violations in lints/*/ui/ test fixtures"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    // ── NATIVE_SCAN_CHECKS sanity ─────────────────────────────────────────────

    #[test]
    fn test_native_scan_checks_all_have_non_empty_literals() {
        for check in NATIVE_SCAN_CHECKS {
            assert!(
                !check.literals.is_empty(),
                "check '{}' must have at least one literal",
                check.name
            );
        }
    }

    #[test]
    fn test_native_scan_checks_all_have_non_empty_directories() {
        for check in NATIVE_SCAN_CHECKS {
            assert!(
                !check.directories.is_empty(),
                "check '{}' must specify at least one directory",
                check.name
            );
        }
    }

    #[test]
    fn test_no_string_errors_handlers_check_is_in_native_scan_checks() {
        assert!(
            NATIVE_SCAN_CHECKS
                .iter()
                .any(|c| c.name == "no-string-errors-handlers"),
            "NATIVE_SCAN_CHECKS must include no-string-errors-handlers"
        );
    }

    #[test]
    fn test_no_string_errors_handlers_scans_handler_directory() {
        let check = NATIVE_SCAN_CHECKS
            .iter()
            .find(|c| c.name == "no-string-errors-handlers")
            .expect("no-string-errors-handlers must be in NATIVE_SCAN_CHECKS");
        assert!(
            check
                .directories
                .iter()
                .any(|d| d.contains("reducer/handler")),
            "no-string-errors-handlers must scan the handler directory, got: {:?}",
            check.directories
        );
    }

    #[test]
    fn test_scan_read_worker_count_is_bounded() {
        // Regression guard: never spawn one OS thread per file.
        // The exact bound is an implementation detail, but it must be far smaller
        // than the number of files to avoid resource exhaustion.
        let workers = super::scan_read_worker_count(10_000);
        assert!(workers > 0);
        assert!(workers <= 32, "workers must be capped, got {workers}");
    }

    // ── Boyer-Moore-Horspool tests ─────────────────────────────────────────────

    mod bmh_tests {
        use super::super::bmh_contains;

        #[test]
        fn test_bmh_contains_empty_pattern_always_true() {
            assert!(bmh_contains(b"abc", b""), "empty pattern must always match");
            assert!(
                bmh_contains(b"", b""),
                "empty pattern in empty text must match"
            );
        }

        #[test]
        fn test_bmh_contains_empty_text_returns_false() {
            assert!(
                !bmh_contains(b"", b"abc"),
                "non-empty pattern in empty text must not match"
            );
        }

        #[test]
        fn test_bmh_contains_pattern_longer_than_text() {
            assert!(
                !bmh_contains(b"hi", b"hello"),
                "pattern longer than text must not match"
            );
        }

        #[test]
        fn test_bmh_contains_exact_match() {
            assert!(
                bmh_contains(b"https://", b"https://"),
                "exact match must return true"
            );
        }

        #[test]
        fn test_bmh_contains_found_at_start() {
            assert!(
                bmh_contains(b"https://example.com", b"https://"),
                "pattern at start of text must match"
            );
        }

        #[test]
        fn test_bmh_contains_found_at_end() {
            assert!(
                bmh_contains(b"see https://", b"https://"),
                "pattern at end of text must match"
            );
        }

        #[test]
        fn test_bmh_contains_not_found() {
            assert!(
                !bmh_contains(b"http://example.com", b"https://"),
                "https:// must not match in http:// URL"
            );
        }

        #[test]
        fn test_bmh_contains_single_char_pattern_found() {
            assert!(bmh_contains(b"abc", b"b"), "single char pattern must match");
        }

        #[test]
        fn test_bmh_contains_single_char_pattern_not_found() {
            assert!(
                !bmh_contains(b"abc", b"z"),
                "absent single char must not match"
            );
        }

        #[test]
        fn test_bmh_contains_repeated_chars() {
            // Degenerate case: repeated chars exercise the shift table carefully.
            assert!(
                bmh_contains(b"aaaaab", b"aaab"),
                "repeated char pattern must match when present"
            );
            assert!(
                !bmh_contains(b"aaaaa", b"aaab"),
                "repeated char pattern must not match when absent"
            );
        }

        #[test]
        fn test_bmh_contains_pattern_equals_text_length() {
            assert!(
                bmh_contains(b"hello", b"hello"),
                "pattern same length as text must match"
            );
            assert!(
                !bmh_contains(b"hello", b"world"),
                "pattern same length as text but different must not match"
            );
        }
    }

    // ── KMP (Knuth-Morris-Pratt) tests ───────────────────────────────────────

    mod kmp_tests {
        use super::super::kmp_search;

        #[test]
        fn test_kmp_empty_needle_returns_some_zero() {
            // Empty needle matches at position 0 in any haystack.
            assert_eq!(kmp_search(b"abc", b""), Some(0));
            assert_eq!(kmp_search(b"", b""), Some(0));
        }

        #[test]
        fn test_kmp_needle_longer_than_haystack_returns_none() {
            assert_eq!(kmp_search(b"ab", b"abc"), None);
        }

        #[test]
        fn test_kmp_finds_at_start() {
            assert_eq!(kmp_search(b"bool is_flag", b"bool"), Some(0));
        }

        #[test]
        fn test_kmp_finds_at_end() {
            assert_eq!(kmp_search(b"is: bool", b"bool"), Some(4));
        }

        #[test]
        fn test_kmp_finds_in_middle() {
            assert_eq!(kmp_search(b"foo bool bar", b"bool"), Some(4));
        }

        #[test]
        fn test_kmp_not_found_returns_none() {
            assert_eq!(kmp_search(b"is: boo", b"bool"), None);
        }

        #[test]
        fn test_kmp_exact_match() {
            assert_eq!(kmp_search(b"bool", b"bool"), Some(0));
        }

        #[test]
        fn test_kmp_repeated_chars_worst_case() {
            // needle = "aaab", haystack = "aaaaaaaaab"
            // Naive search would scan O(n*m) positions; KMP uses O(n+m).
            // This validates correctness on the worst-case input for naive search.
            assert_eq!(kmp_search(b"aaaaaaaaab", b"aaab"), Some(6));
        }

        #[test]
        fn test_kmp_repeated_chars_not_found() {
            // "aaab" not present in "aaaaaaa" (no 'b').
            assert_eq!(kmp_search(b"aaaaaaa", b"aaab"), None);
        }

        #[test]
        fn test_kmp_single_char_found() {
            assert_eq!(kmp_search(b"xyz", b"y"), Some(1));
        }

        #[test]
        fn test_kmp_single_char_not_found() {
            assert_eq!(kmp_search(b"xyz", b"w"), None);
        }

        #[test]
        fn test_kmp_agrees_with_starts_with_for_bool_suffix() {
            // Verify kmp_search at position 0 agrees with starts_with for
            // all common bool-suffix patterns that StemWithBoolSuffix encounters.
            let cases: &[&[u8]] = &[
                b"bool)", b"bool,", b"bool ", b"bool\n", b"boolean", b"boo", b"bo", b"b", b"",
            ];
            for &haystack in cases {
                // kmp_search finds needle at pos 0 iff starts_with matches.
                let starts = haystack.starts_with(b"bool");
                let kmp_at_zero = kmp_search(haystack, b"bool") == Some(0);
                assert_eq!(
                    starts, kmp_at_zero,
                    "kmp vs starts_with disagreement for {haystack:?}"
                );
            }
        }
    }

    // ── Two-Way string search tests ───────────────────────────────────────────

    mod tw_tests {
        use super::super::{bmh_contains, tw_contains};

        #[test]
        fn test_tw_contains_basic_match() {
            assert!(tw_contains(b"hello world", b"world"));
            assert!(tw_contains(b"https://example.com", b"https://"));
        }

        #[test]
        fn test_tw_contains_no_match() {
            assert!(!tw_contains(b"hello world", b"https://"));
            assert!(!tw_contains(b"http://example.com", b"https://"));
        }

        #[test]
        fn test_tw_contains_empty_pattern() {
            assert!(tw_contains(b"anything", b""));
            assert!(tw_contains(b"", b""));
        }

        #[test]
        fn test_tw_contains_pattern_equals_text() {
            assert!(tw_contains(b"https://", b"https://"));
        }

        #[test]
        fn test_tw_contains_pattern_longer_than_text() {
            assert!(!tw_contains(b"hi", b"hello"));
        }

        #[test]
        fn test_tw_contains_repetitive_text_worst_case_bmh() {
            // "aaaa...aaab" (n=1001) with pattern "aaab".
            // BMH degenerates to O(n×m) on this input; Two-Way runs in O(n).
            let n = 1000usize;
            let mut text: Vec<u8> = (0..n).map(|_| b'a').collect();
            text.push(b'b');
            assert!(tw_contains(&text, b"aaab"), "must find 'aaab' in aaa...ab");
            let text_no_match: Vec<u8> = (0..n).map(|_| b'a').collect();
            assert!(
                !tw_contains(&text_no_match, b"aaab"),
                "must not find 'aaab' in all-a text"
            );
        }

        #[test]
        fn test_tw_contains_agrees_with_bmh() {
            // Property test: tw_contains and bmh_contains must agree on all inputs,
            // including the actual negative_context strings used in NegativeLookahead checks.
            let cases: &[(&[u8], &[u8])] = &[
                (b"foo bar baz", b"bar"),
                (
                    b"#[ignore] // https://github.com/foo/bar/issues/1",
                    b"https://",
                ),
                (b"#[ignore]", b"https://"),
                (b"", b"x"),
                (b"x", b""),
                (b"https://", b"https://"),
                (b"http://example.com", b"https://"),
                (b"aaaaab", b"aaab"),
                (b"aaaaa", b"aaab"),
            ];
            for (text, pat) in cases {
                assert_eq!(
                    tw_contains(text, pat),
                    bmh_contains(text, pat),
                    "tw_contains and bmh_contains disagree: text={:?} pat={:?}",
                    text,
                    pat
                );
            }
        }

        #[test]
        fn test_tw_contains_match_at_start() {
            assert!(tw_contains(b"world foo", b"world"));
        }

        #[test]
        fn test_tw_contains_match_at_end() {
            assert!(tw_contains(b"foo world", b"world"));
        }

        #[test]
        fn test_tw_contains_single_char_pattern() {
            assert!(tw_contains(b"abc", b"b"));
            assert!(!tw_contains(b"abc", b"z"));
        }
    }

    // ── adaptive progress threshold tests ─────────────────────────────────────

    mod adaptive_threshold_tests {
        #[test]
        fn test_adaptive_threshold_small_codebase() {
            // 20 total files: threshold = max(5, 20/20) = max(5, 1) = 5
            let total = 20usize;
            let threshold = (total / 20).max(5);
            assert_eq!(threshold, 5);
        }

        #[test]
        fn test_adaptive_threshold_medium_codebase() {
            // 200 total files: threshold = max(5, 200/20) = max(5, 10) = 10
            let total = 200usize;
            let threshold = (total / 20).max(5);
            assert_eq!(threshold, 10);
        }

        #[test]
        fn test_adaptive_threshold_large_codebase() {
            // 2000 total files: threshold = max(5, 2000/20) = max(5, 100) = 100
            let total = 2000usize;
            let threshold = (total / 20).max(5);
            assert_eq!(threshold, 100);
        }

        #[test]
        fn test_adaptive_threshold_zero_files() {
            // 0 total files: threshold = max(5, 0) = 5 (no divide-by-zero risk)
            let total = 0usize;
            let threshold = (total / 20).max(5);
            assert_eq!(threshold, 5);
        }

        #[test]
        fn test_adaptive_threshold_10_files() {
            // 10 files: threshold = max(5, 10/20) = max(5, 0) = 5
            let total = 10usize;
            let threshold = (total / 20).max(5);
            assert_eq!(threshold, 5);
        }

        #[test]
        fn test_adaptive_threshold_150_files() {
            // 150 files: threshold = max(5, 150/20) = max(5, 7) = 7
            let total = 150usize;
            let threshold = (total / 20).max(5);
            assert_eq!(threshold, 7);
        }
    }

    // ── per-file scan progress tests ──────────────────────────────────────────

    mod scan_progress_tests {
        use super::super::{run_native_scan_checks_reporting, MatchMode, NativeScanCheck};
        use std::fs;
        use std::path::PathBuf;
        use std::sync::Mutex;

        fn make_temp_dir(name: &str) -> PathBuf {
            let base = std::env::temp_dir().join(format!("xtask-progress-{name}"));
            let _ = fs::remove_dir_all(&base);
            fs::create_dir_all(&base).unwrap();
            base
        }

        fn write_file(dir: &std::path::Path, rel_path: &str, content: &str) {
            let full = dir.join(rel_path);
            if let Some(parent) = full.parent() {
                fs::create_dir_all(parent).unwrap();
            }
            fs::write(full, content).unwrap();
        }

        #[test]
        fn test_scan_progress_emitted_every_50_files() {
            // Create 150 files so the 50-file progress callback fires at least 3 times.
            let dir = make_temp_dir("progress-150");
            for i in 0..150 {
                write_file(&dir, &format!("src/file_{i:04}.rs"), "let x = 1;\n");
            }

            let events: Mutex<Vec<String>> = Mutex::new(Vec::new());
            let check = NativeScanCheck {
                name: "test-progress",
                literals: &["forbidden"],
                directories: &["src"],
                include_glob: "*.rs",
                exclude_globs: &[],
                mode: MatchMode::AnyLiteral {
                    skip_comment_lines: false,
                },
            };

            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|name, info| {
                events.lock().unwrap().push(format!("{name}:{info}"));
            });

            let captured = events.into_inner().unwrap();
            // With 150 files and a boundary every 50, expect at least 3 progress events.
            assert!(
                captured.len() >= 3,
                "expected ≥3 progress events for 150 files, got: {captured:?}"
            );
            // Every event must be prefixed with "native-scan:".
            for event in &captured {
                assert!(
                    event.starts_with("native-scan:"),
                    "unexpected progress event: {event}"
                );
            }

            let _ = fs::remove_dir_all(&dir);
        }

        #[test]
        fn test_scan_progress_emits_pre_scan_count_for_small_file_count() {
            // For 10 files, pre-scan count events ARE emitted even though the corpus is small.
            // Adaptive threshold = max(5, 10/20) = 5, so per-file events fire at n=5 and n=10.
            let dir = make_temp_dir("progress-small");
            for i in 0..10 {
                write_file(&dir, &format!("src/file_{i}.rs"), "let x = 1;\n");
            }

            let events: Mutex<Vec<String>> = Mutex::new(Vec::new());
            let check = NativeScanCheck {
                name: "test-progress-small",
                literals: &["forbidden"],
                directories: &["src"],
                include_glob: "*.rs",
                exclude_globs: &[],
                mode: MatchMode::AnyLiteral {
                    skip_comment_lines: false,
                },
            };

            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|name, info| {
                events.lock().unwrap().push(format!("{name}:{info}"));
            });

            let captured = events.into_inner().unwrap();
            // At minimum: "10 files across 1 group(s)" + "  src: 10 files" = 2 pre-scan events.
            assert!(
                captured.len() >= 2,
                "expected at least 2 pre-scan count events for 10 files, got: {captured:?}"
            );
            // All events must use the "native-scan:" prefix.
            for event in &captured {
                assert!(
                    event.starts_with("native-scan:"),
                    "unexpected progress event: {event}"
                );
            }
            // First event must mention the file count.
            assert!(
                captured[0].contains("10 files"),
                "first event must contain total file count, got: {}",
                captured[0]
            );

            let _ = fs::remove_dir_all(&dir);
        }
    }

    // ── DiagnosticLevel / scan_has_diagnostic_prefix tests ────────────────────

    mod classify_tests {
        use super::super::{scan_has_diagnostic_prefix, DiagnosticLevel};

        #[test]
        fn test_diagnostic_prefix_detects_error_lowercase() {
            let level = scan_has_diagnostic_prefix("error: something went wrong\n");
            assert_eq!(level, DiagnosticLevel::Error);
        }

        #[test]
        fn test_diagnostic_prefix_detects_error_titlecase() {
            let level = scan_has_diagnostic_prefix("Error: something went wrong\n");
            assert_eq!(level, DiagnosticLevel::Error);
        }

        #[test]
        fn test_diagnostic_prefix_detects_rustc_error_bracket_form() {
            // rustc/clippy commonly emit: `error[E0XXX]: ...`
            let level = scan_has_diagnostic_prefix("error[E0425]: cannot find value\n");
            assert_eq!(level, DiagnosticLevel::Error);
        }

        #[test]
        fn test_diagnostic_prefix_detects_warning() {
            let level = scan_has_diagnostic_prefix("warning: unused variable\n");
            assert_eq!(level, DiagnosticLevel::Warning);
        }

        #[test]
        fn test_diagnostic_prefix_detects_warning_tc() {
            // "Warning:" (title-case) is included for completeness.
            let level = scan_has_diagnostic_prefix("Warning: deprecated usage\n");
            assert_eq!(level, DiagnosticLevel::Warning);
        }

        #[test]
        fn test_diagnostic_prefix_detects_warning_bracket_form() {
            // Some toolchains can emit warnings in the bracketed form.
            let level = scan_has_diagnostic_prefix("warning[dead_code]: function is never used\n");
            assert_eq!(level, DiagnosticLevel::Warning);
        }

        #[test]
        fn test_diagnostic_prefix_detects_indented_error() {
            // Leading whitespace before "error:" must still be detected.
            let level = scan_has_diagnostic_prefix("   error: indented error\n");
            assert_eq!(level, DiagnosticLevel::Error);
        }

        #[test]
        fn test_diagnostic_prefix_treats_cr_as_whitespace() {
            // trim_start() treats '\r' as whitespace; our prefix check must match.
            let level = scan_has_diagnostic_prefix("\rerror: windows line ending artifact\n");
            assert_eq!(level, DiagnosticLevel::Error);
        }

        #[test]
        fn test_diagnostic_prefix_clean_output() {
            let level = scan_has_diagnostic_prefix("Compiling foo v0.1.0\nFinished\n");
            assert_eq!(level, DiagnosticLevel::Clean);
        }

        #[test]
        fn test_diagnostic_prefix_error_wins_over_warning() {
            let text = "warning: something\nerror: fatal\n";
            let level = scan_has_diagnostic_prefix(text);
            assert_eq!(level, DiagnosticLevel::Error);
        }

        #[test]
        fn test_diagnostic_prefix_empty_string() {
            let level = scan_has_diagnostic_prefix("");
            assert_eq!(level, DiagnosticLevel::Clean);
        }

        #[test]
        fn test_diagnostic_prefix_mid_line_not_counted() {
            // "error:" that appears after non-whitespace must NOT trigger.
            let level = scan_has_diagnostic_prefix("foo error: bar\n");
            assert_eq!(
                level,
                DiagnosticLevel::Clean,
                "mid-line error: must not trigger"
            );
        }

        #[test]
        fn test_diagnostic_prefix_multiline_only_warning() {
            let text = "Compiling\nwarning: unused\nFinished\n";
            let level = scan_has_diagnostic_prefix(text);
            assert_eq!(level, DiagnosticLevel::Warning);
        }

        #[test]
        fn test_diagnostic_prefix_react_act_warning_is_warning() {
            let text =
                "Warning: An update to Configuration inside a test was not wrapped in act(...)\n";
            let level = scan_has_diagnostic_prefix(text);
            assert_eq!(level, DiagnosticLevel::Warning);
        }

        #[test]
        fn test_diagnostic_level_max_level() {
            assert_eq!(
                DiagnosticLevel::Error.max_level(DiagnosticLevel::Clean),
                DiagnosticLevel::Error
            );
            assert_eq!(
                DiagnosticLevel::Clean.max_level(DiagnosticLevel::Error),
                DiagnosticLevel::Error
            );
            assert_eq!(
                DiagnosticLevel::Warning.max_level(DiagnosticLevel::Clean),
                DiagnosticLevel::Warning
            );
            assert_eq!(
                DiagnosticLevel::Clean.max_level(DiagnosticLevel::Clean),
                DiagnosticLevel::Clean
            );
            assert_eq!(
                DiagnosticLevel::Warning.max_level(DiagnosticLevel::Error),
                DiagnosticLevel::Error
            );
        }

        #[test]
        fn test_scan_has_diagnostic_prefix_consistent_across_repeated_calls() {
            // Verify that the cached OnceLock automaton produces consistent results
            // across many calls (regression guard for OnceLock correctness).
            // Calling 1000 times exercises the cached path after the first construction.
            let inputs = [
                ("error: something bad", DiagnosticLevel::Error),
                ("warning: something mild", DiagnosticLevel::Warning),
                ("  info: no prefix match", DiagnosticLevel::Clean),
                ("", DiagnosticLevel::Clean),
            ];
            for _ in 0..1_000 {
                for (text, expected) in &inputs {
                    assert_eq!(
                        scan_has_diagnostic_prefix(text),
                        *expected,
                        "inconsistent result for: {text:?}"
                    );
                }
            }
        }
    }

    // ── DFA builder equivalence tests ─────────────────────────────────────────
    //
    // Verify that AhoCorasickBuilder with DFA mode produces identical match results
    // to the default AhoCorasick::new() construction for all MatchMode variants.

    mod dfa_builder_tests {
        use super::super::{run_native_scan_checks_reporting, MatchMode, NativeScanCheck};
        use std::fs;
        use std::path::PathBuf;

        fn make_temp_dir(name: &str) -> PathBuf {
            let base = std::env::temp_dir().join(format!("xtask-dfa-{name}"));
            let _ = fs::remove_dir_all(&base);
            fs::create_dir_all(&base).unwrap();
            base
        }

        fn write_file(dir: &std::path::Path, rel_path: &str, content: &str) {
            let full = dir.join(rel_path);
            if let Some(parent) = full.parent() {
                fs::create_dir_all(parent).unwrap();
            }
            fs::write(full, content).unwrap();
        }

        /// DFA builder must produce the same violation count as default for AnyLiteral.
        ///
        /// Verifies that the optimised AhoCorasickBuilder::new().kind(DFA) path in
        /// scan_group_collect returns results byte-for-byte identical to what the
        /// default AhoCorasick::new() heuristic produced before the optimisation.
        #[test]
        fn test_dfa_builder_produces_same_results_as_default_for_any_literal() {
            let dir = make_temp_dir("dfa-any-literal");
            write_file(
                &dir,
                "src/lib.rs",
                "let x = forbidden_alpha;\nlet y = safe_code;\nlet z = forbidden_beta;\n",
            );

            let check = NativeScanCheck {
                name: "dfa-any-literal",
                literals: &["forbidden_alpha", "forbidden_beta"],
                directories: &["src"],
                include_glob: "*.rs",
                exclude_globs: &[],
                mode: MatchMode::AnyLiteral {
                    skip_comment_lines: false,
                },
            };

            let results =
                run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
            // Both patterns must be found: 2 violations.
            assert!(
                !results[0].passed,
                "DFA scanner must find forbidden literals"
            );
            assert_eq!(
                results[0].violations.len(),
                2,
                "DFA scanner must find exactly 2 violations, got: {:?}",
                results[0].violations
            );
            assert_eq!(results[0].violations[0].line_number, 1);
            assert_eq!(results[0].violations[1].line_number, 3);

            let _ = fs::remove_dir_all(&dir);
        }

        /// DFA builder must produce the same (absent) results for NegativeLookahead.
        ///
        /// When the negative context is present on the same line, no violation must be
        /// emitted.  This verifies the DFA transition table does not interfere with the
        /// per-match post-filter that checks for the negative context string.
        #[test]
        fn test_dfa_builder_produces_same_results_for_negative_lookahead() {
            let dir = make_temp_dir("dfa-negative-lookahead");
            // Line 1: pattern with negative context → no violation
            // Line 2: pattern without negative context → violation
            write_file(
                &dir,
                "src/lib.rs",
                "is_testing = true // allow-in-test\nis_testing = true\n",
            );

            let check = NativeScanCheck {
                name: "dfa-negative-lookahead",
                literals: &["is_testing"],
                directories: &["src"],
                include_glob: "*.rs",
                exclude_globs: &[],
                mode: MatchMode::NegativeLookahead {
                    negative_context: "allow-in-test",
                    word_boundary_at_end: false,
                },
            };

            let results =
                run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
            assert!(
                !results[0].passed,
                "DFA scanner must find the negative-lookahead violation on line 2"
            );
            assert_eq!(
                results[0].violations.len(),
                1,
                "exactly one violation expected (line 2), got: {:?}",
                results[0].violations
            );
            assert_eq!(results[0].violations[0].line_number, 2);

            let _ = fs::remove_dir_all(&dir);
        }

        /// DFA builder must not panic and must return no violations for an empty pattern list.
        ///
        /// scan_group_collect returns early when all_patterns is empty (before even calling
        /// the builder); this test confirms that code path is reached without panic.
        #[test]
        fn test_dfa_builder_fallback_on_empty_pattern_list() {
            let dir = make_temp_dir("dfa-empty-patterns");
            write_file(&dir, "src/lib.rs", "any content here\n");

            let check = NativeScanCheck {
                name: "dfa-empty-patterns",
                literals: &[], // intentionally empty
                directories: &["src"],
                include_glob: "*.rs",
                exclude_globs: &[],
                mode: MatchMode::AnyLiteral {
                    skip_comment_lines: false,
                },
            };

            // Must not panic; an empty pattern list returns Pass immediately.
            let results =
                run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
            assert!(
                results[0].passed,
                "empty pattern list must produce no violations"
            );

            let _ = fs::remove_dir_all(&dir);
        }
    }

    // ── collect_scan_groups tests ─────────────────────────────────────────────

    #[test]
    fn test_collect_scan_groups_returns_sorted_files() {
        // Verify that collect_scan_groups returns files in sorted order for a
        // single group.  Files are created in reverse alphabetical order to
        // confirm the sort is applied by the function, not by the OS.
        let dir = make_temp_dir("collect-groups-sorted");
        write_file(&dir, "src/c.rs", "// c");
        write_file(&dir, "src/a.rs", "// a");
        write_file(&dir, "src/b.rs", "// b");

        let check = NativeScanCheck {
            name: "test",
            literals: &["x"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let groups = collect_scan_groups(&dir, &[check]);
        assert_eq!(groups.len(), 1, "one group expected");
        let (_, (_, result)) = groups.into_iter().next().unwrap();
        let files = result.expect("traversal must succeed");
        assert_eq!(files.len(), 3, "three files expected");
        // Verify ascending sort order.
        for i in 1..files.len() {
            assert!(
                files[i - 1] <= files[i],
                "files must be in sorted order, got: {files:?}"
            );
        }

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_collect_scan_groups_deduplicates_groups() {
        // Two checks sharing the same directory → collect_scan_groups must produce
        // exactly one group entry (single traversal, not two).
        let dir = make_temp_dir("collect-groups-dedup");
        write_file(&dir, "src/a.rs", "// a");

        let check1 = NativeScanCheck {
            name: "check1",
            literals: &["x"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };
        let check2 = NativeScanCheck {
            name: "check2",
            literals: &["y"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let groups = collect_scan_groups(&dir, &[check1, check2]);
        assert_eq!(
            groups.len(),
            1,
            "two checks sharing the same directory must produce exactly one group"
        );
        let (_, (_, result)) = groups.into_iter().next().unwrap();
        let files = result.expect("traversal must succeed");
        assert_eq!(files.len(), 1, "one file expected");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_collect_scan_groups_separate_directories_produce_separate_groups() {
        // Two checks with different directories → two group entries.
        let dir = make_temp_dir("collect-groups-two");
        write_file(&dir, "src/a.rs", "// a");
        write_file(&dir, "tests/b.rs", "// b");

        let check1 = NativeScanCheck {
            name: "check1",
            literals: &["x"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };
        let check2 = NativeScanCheck {
            name: "check2",
            literals: &["y"],
            directories: &["tests"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let groups = collect_scan_groups(&dir, &[check1, check2]);
        assert_eq!(
            groups.len(),
            2,
            "two checks with different directories must produce two groups"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_collect_scan_groups_skips_excluded_transient_frontend_directories() {
        let dir = make_temp_dir("collect-groups-skip-transient-frontend");
        write_file(&dir, "ralph-gui/build.rs", "fn main() {}\n");
        write_file(&dir, "ralph-gui/src/lib.rs", "pub fn gui() {}\n");
        write_file(
            &dir,
            "ralph-gui/ui/node_modules/pkg/index.rs",
            "#[allow(clippy::all)]\n",
        );
        write_file(
            &dir,
            "ralph-gui/ui/dist/generated.rs",
            "#[allow(clippy::all)]\n",
        );

        let check = NativeScanCheck {
            name: "forbidden-allow-expect-scan",
            literals: &["#[allow("],
            directories: &["ralph-gui"],
            include_glob: "*.rs",
            exclude_globs: &["**/node_modules/**", "**/dist/**"],
            mode: MatchMode::AnyLiteralAtLineStart {
                skip_comment_lines: true,
            },
        };

        let groups = collect_scan_groups(&dir, &[check]);
        let (_, (_, result)) = groups.into_iter().next().unwrap();
        let files = result.expect("traversal must succeed");

        assert!(
            files
                .iter()
                .all(|path| !path.to_string_lossy().contains("node_modules")),
            "excluded node_modules files must not be traversed: {files:?}"
        );
        assert!(
            files
                .iter()
                .all(|path| !path.to_string_lossy().contains("/dist/")),
            "excluded dist files must not be traversed: {files:?}"
        );
        assert!(
            files
                .iter()
                .any(|path| path.ends_with(Path::new("ralph-gui/build.rs"))),
            "stable Rust-owned build.rs must still be scanned"
        );
        assert!(
            files
                .iter()
                .any(|path| path.ends_with(Path::new("ralph-gui/src/lib.rs"))),
            "stable GUI Rust sources must still be scanned"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    // ── tw_contains_precomputed tests ─────────────────────────────────────────

    /// Cross-validate `tw_contains_precomputed` against the `tw_contains` baseline.
    ///
    /// Tests a broad set of inputs including adversarial periodic patterns that trigger
    /// the Case 1 (memory-based) branch of the Two-Way algorithm, ensuring the
    /// precomputed variant is bit-for-bit identical to the full preprocessing version.
    ///
    /// Reference: TAOCP Vol. 3, §6.3 — preprocessing amortization principle.
    #[test]
    fn test_tw_contains_precomputed_matches_tw_contains() {
        let fixed_cases: &[(&[u8], &[u8])] = &[
            // Basic matching
            (b"hello", b"hello"),
            (b"hello world", b"world"),
            (b"hello world", b"hello"),
            (b"hello world", b"xyz"),
            // Exact same as text
            (b"abc", b"abc"),
            // Pattern at start / end
            (b"abc", b"ab"),
            (b"abc", b"bc"),
            // Single-char patterns
            (b"a", b"a"),
            (b"a", b"b"),
            (b"b", b"b"),
            // Pattern longer than text
            (b"ab", b"abcdef"),
            // Realistic NegativeLookahead inputs
            (b"#[ignore] // https://example.com", b"https://"),
            (b"#[ignore]", b"https://"),
            (b"#[ignore(reason = \"https://foo.com\")]", b"https://"),
            // Non-ASCII safe (treat as raw bytes)
            (b"caf\xc3\xa9", b"caf\xc3"),
        ];

        // Adversarial periodic patterns: trigger Case 1 in Two-Way.
        // BMH degenerates to O(n×m) on these; Two-Way guarantees O(n).
        let long_a: Vec<u8> = vec![b'a'; 100];
        let mut aaab_99 = vec![b'a'; 99];
        aaab_99.push(b'b');
        let mut aaab_4 = vec![b'a'; 3];
        aaab_4.push(b'b');

        let adversarial: Vec<(Vec<u8>, Vec<u8>)> = vec![
            // text = "aaa...ab" (100 a's + b), pattern = "aaab" → matches at the end
            (long_a.iter().chain(b"b").copied().collect(), aaab_4.clone()),
            // text = "aaa...ab" (99 a's + b), pattern = "aaa...ab" (99 a's + b) → exact match
            (aaab_99.clone(), aaab_99.clone()),
            // text = "aaa...ab" (99 a's + b), pattern = "aaa...ac" (99 a's + c) → no match
            (aaab_99.clone(), {
                let mut p = vec![b'a'; 99];
                p.push(b'c');
                p
            }),
            // Periodic pattern "abab" in "ababababab"
            (b"ababababab".to_vec(), b"abab".to_vec()),
            (b"ababababab".to_vec(), b"abcd".to_vec()),
            // Single repeated char
            (vec![b'a'; 50], vec![b'a'; 10]),
            (vec![b'a'; 50], vec![b'a'; 51]),
        ];

        // Run fixed cases.
        for &(text, pattern) in fixed_cases {
            let expected = tw_contains(text, pattern);
            let precomputed = critical_factorization(pattern);
            let got = tw_contains_precomputed(text, pattern, precomputed);
            assert_eq!(
                got, expected,
                "tw_contains_precomputed mismatch: text={:?} pattern={:?}",
                text, pattern
            );
        }

        // Run adversarial cases.
        for (text, pattern) in &adversarial {
            let expected = tw_contains(text, pattern);
            let precomputed = critical_factorization(pattern);
            let got = tw_contains_precomputed(text, pattern, precomputed);
            assert_eq!(
                got,
                expected,
                "tw_contains_precomputed mismatch (adversarial): text_len={} pattern_len={}",
                text.len(),
                pattern.len()
            );
        }
    }

    /// Empty pattern must return true (same contract as tw_contains).
    #[test]
    fn test_tw_contains_precomputed_empty_pattern_returns_true() {
        // tw_contains_precomputed skips the precomputed path for empty patterns.
        // Use critical_factorization on a non-empty dummy pattern for the call.
        let precomputed = critical_factorization(b"x");
        assert!(
            tw_contains_precomputed(b"anything", &[], precomputed),
            "empty pattern must always match"
        );
        assert!(
            tw_contains_precomputed(&[], &[], precomputed),
            "empty pattern in empty text must match"
        );
    }

    /// Pattern longer than text must return false.
    #[test]
    fn test_tw_contains_precomputed_pattern_longer_than_text_returns_false() {
        let pattern = b"longer";
        let text = b"short";
        let precomputed = critical_factorization(pattern);
        assert!(
            !tw_contains_precomputed(text, pattern, precomputed),
            "pattern longer than text must not match"
        );
    }
}
