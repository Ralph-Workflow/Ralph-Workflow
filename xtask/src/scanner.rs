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

use aho_corasick::AhoCorasick;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

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
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
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
    // Fails if #[allow(, #![allow(, #[expect(, or #![expect( appears at line
    // start (possibly preceded by whitespace).  Comment lines are skipped.
    NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
        directories: &[
            "ralph-workflow/src",
            "tests",
            "xtask/src",
            "test-helpers/src",
        ],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    },
];

// ── Public entry point ────────────────────────────────────────────────────────

/// Run all native scan checks against the repository root.
///
/// Checks are grouped by their `(sorted-directories, include_glob)` key.
/// Within each group, files are read once and a single Aho-Corasick automaton
/// (built from every pattern in the group) scans each file in a single pass.
/// Matches are demultiplexed back to their originating check; per-check
/// post-filters (comment-line skipping, `\s*:\s*bool` suffix matching,
/// file-exclusion globs) are applied before recording violations.
///
/// Returns one `NativeScanCheckResult` per input check, in the same order.
pub fn run_native_scan_checks(
    repo_root: &Path,
    checks: &[NativeScanCheck],
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

    // Group check indices by (sorted directories + include_glob) key.
    let mut groups: HashMap<String, Vec<usize>> = HashMap::new();
    for (idx, check) in checks.iter().enumerate() {
        groups
            .entry(directory_group_key(check))
            .or_default()
            .push(idx);
    }

    // Collect violations per group in parallel using scoped threads.
    // Each group is independent (different directories or patterns), so no
    // synchronisation is needed during the scan phase.
    let groups_vec: Vec<Vec<usize>> = groups.into_values().collect();
    let mut all_violations: Vec<Vec<(usize, NativeScanViolation)>> =
        Vec::with_capacity(groups_vec.len());

    std::thread::scope(|s| {
        let handles: Vec<_> = groups_vec
            .iter()
            .map(|check_indices| s.spawn(|| scan_group_collect(repo_root, checks, check_indices)))
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

/// Read a slice of files in parallel using scoped threads.
///
/// Returns `Option<Vec<u8>>` per file in the **same order** as `files`, so
/// callers can zip results with the original slice without re-sorting.
/// For small groups (below `PARALLEL_THRESHOLD`) sequential reads are used
/// to avoid thread-spawn overhead.
fn read_scan_files_parallel(files: &[PathBuf]) -> Vec<Option<Vec<u8>>> {
    const PARALLEL_THRESHOLD: usize = 4;
    if files.len() < PARALLEL_THRESHOLD {
        return files.iter().map(|p| std::fs::read(p).ok()).collect();
    }

    let workers = scan_read_worker_count(files.len());
    let mut results: Vec<Option<Vec<u8>>> = vec![None; files.len()];

    std::thread::scope(|s| {
        let handles: Vec<_> = (0..workers)
            .map(|worker_id| {
                s.spawn(move || {
                    let mut out: Vec<(usize, Option<Vec<u8>>)> = Vec::new();
                    for i in (worker_id..files.len()).step_by(workers) {
                        out.push((i, std::fs::read(&files[i]).ok()));
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
    format!("{}:{}", dirs.join(","), check.include_glob)
}

/// Scan one directory group: collect files once, build a single Aho-Corasick
/// automaton from all patterns in the group, search each file, demultiplex.
///
/// Returns a flat list of `(check_index, NativeScanViolation)` pairs so the
/// caller can merge results from multiple groups (including parallel groups).
fn scan_group_collect(
    repo_root: &Path,
    all_checks: &[NativeScanCheck],
    check_indices: &[usize],
) -> Vec<(usize, NativeScanViolation)> {
    let mut violations: Vec<(usize, NativeScanViolation)> = Vec::new();

    let first = &all_checks[check_indices[0]];

    // Collect all matching files for the group's directories.
    let mut files: Vec<PathBuf> = Vec::new();
    for dir in first.directories {
        let full_dir = repo_root.join(dir);
        if full_dir.exists() {
            if let Err(e) = collect_files_with_glob(&full_dir, first.include_glob, &mut files) {
                // Directory traversal errors must not be silently treated as "no files".
                // Surface the error as an explicit failure for every check in this group.
                let msg = format!("read_dir error for {}: {e}", full_dir.display());
                return check_indices
                    .iter()
                    .copied()
                    .map(|ci| {
                        (
                            ci,
                            NativeScanViolation {
                                file: full_dir.clone(),
                                line_number: 1,
                                line: msg.clone(),
                            },
                        )
                    })
                    .collect();
            }
        }
    }
    files.sort();

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

    let ac = match AhoCorasick::new(&all_patterns) {
        Ok(ac) => ac,
        Err(_) => return violations,
    };

    // Read all files in parallel, then process in deterministic (sorted) order.
    let contents = read_scan_files_parallel(&files);
    for (file_path, content_opt) in files.iter().zip(contents.into_iter()) {
        let content = match content_opt {
            Some(c) => c,
            None => continue,
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
                            // Boyer-Moore-Horspool O(n/m) average search (TAOCP Vol. 3 §6.3).
                            !bmh_contains(line_bytes, negative_context.as_bytes())
                        }
                    }
                }
            };

            if is_violation {
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
    let entries = std::fs::read_dir(dir)?;
    for entry in entries {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            collect_files_with_glob(&path, include_glob, files)?;
        } else if file_matches_include_glob(&path, include_glob) {
            files.push(path);
        }
    }

    Ok(())
}

fn file_matches_include_glob(path: &Path, glob: &str) -> bool {
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
    if !rest[i..].starts_with(b"bool") {
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
/// Preprocessing: O(|alphabet| + |pattern|) — builds a 256-entry bad-character shift table.
/// Search: O(|text| / |pattern|) average, O(|text| × |pattern|) worst-case.
///
/// Reference: Horspool, R.N. (1980). "Practical Fast Searching in Strings."
/// Software: Practice and Experience 10(6): 501–506.
/// See also: TAOCP Vol. 3, §6.3 (String Searching).
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
const DIAG_PAT_WARNING: usize = 2; // "warning:"
const DIAG_PAT_WARNING_TC: usize = 3; // "Warning:" (defensive; kept for completeness)

static DIAG_PATTERNS: &[&str] = &["error:", "Error:", "warning:", "Warning:"];

/// Scan command output (stdout or stderr) for diagnostic-level prefixes in O(n+m).
///
/// Uses a single Aho-Corasick pass (Aho & Corasick, 1975) over the raw bytes.
/// For each match, verifies the pattern appears at the start of a trimmed line
/// (equivalent to `line.trim_start().starts_with(pattern)`) via a LineIndex O(log L)
/// lookup (TAOCP Vol. 3 §6.2.1 Algorithm B).
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
    let ac = AhoCorasick::new(DIAG_PATTERNS).expect("static patterns are valid");
    let mut level = DiagnosticLevel::Clean;
    for mat in ac.find_iter(bytes) {
        let byte_start = mat.start();
        // Check that the match is at the start of the trimmed line.
        let line_start = line_idx.line_start(byte_start);
        let prefix_bytes = &bytes[line_start..byte_start];
        let all_ws = prefix_bytes.iter().all(|&b| b == b' ' || b == b'\t');
        if !all_ws {
            continue; // match is not at line start after trim
        }
        match mat.pattern().as_usize() {
            DIAG_PAT_ERROR_LC | DIAG_PAT_ERROR_TC => return DiagnosticLevel::Error,
            DIAG_PAT_WARNING | DIAG_PAT_WARNING_TC => {
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
        let base = std::env::temp_dir().join(format!("xtask-scanner-{name}"));
        let _ = fs::remove_dir_all(&base);
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));

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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, &checks);
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

        let results = run_native_scan_checks(&dir, &checks);
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

        let results = run_native_scan_checks(&dir, &checks);
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
        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
        assert!(
            results[0].passed,
            "comment-line #[allow( must be skipped when skip_comment_lines=true"
        );

        let _ = fs::remove_dir_all(&dir);
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, std::slice::from_ref(&check));
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

        let results = run_native_scan_checks(&dir, &checks);
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
        fn test_diagnostic_prefix_detects_indented_error() {
            // Leading whitespace before "error:" must still be detected.
            let level = scan_has_diagnostic_prefix("   error: indented error\n");
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
    }
}
