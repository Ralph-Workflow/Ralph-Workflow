use std::collections::HashSet;
use std::path::{Path, PathBuf};

use aho_corasick::AhoCorasick;

use crate::domain::compliance::{
    shell_script_scan_result, tailwind_scan_result, timeout_wrapper_scan_result, ComplianceSummary,
};
use crate::domain::tailwind_policy::{
    extract_tailwind_token, normalize_tailwind_candidate, tailwind_candidate_matches_rule,
    REMOVED_TAILWIND4_ANGULAR_CLASSES,
};
use crate::io::scanner::LineIndex;
use crate::runtime::verify::{CheckStatus, NativeCheckResult};

// Pattern IDs for the timeout-wrapper Aho-Corasick automaton (O(n+m+z) scan).
const PAT_TEST_ATTR: usize = 0; // "#[test]"
const PAT_DEFAULT_TIMEOUT: usize = 1; // "with_default_timeout"
const PAT_TIMEOUT: usize = 2; // "with_timeout"
const TIMEOUT_PATTERNS: &[&str] = &["#[test]", "with_default_timeout", "with_timeout"];
const CHECK_STATUS_MAPPING: [CheckStatus; 3] =
    [CheckStatus::Pass, CheckStatus::Warning, CheckStatus::Error];

/// Scans `scripts/` and `tests/integration_tests/` for `.sh` files.
///
/// Shell scripts were migrated to Rust xtask commands; their presence after
/// migration is a regression.  Returns `Error` if any `.sh` file is found,
/// listing the offending paths.  Returns `Pass` when the directories do not
/// exist (e.g. in unit-test environments with fake repo paths).
pub fn check_no_shell_scripts(repo_root: &Path) -> NativeCheckResult {
    let scan_dirs = ["scripts", "tests/integration_tests"];
    let scan_paths: Vec<PathBuf> = scan_dirs.iter().map(|rel| repo_root.join(rel)).collect();
    let (found, walk_errors) = crate::io::shell_scripts::scan_for_shell_scripts(&scan_paths);

    compliance_summary_to_native(shell_script_scan_result(&found, &walk_errors))
}

/// Scans integration test files for `#[test]` functions that do not call
/// `with_default_timeout` or `with_timeout` in their body.
///
/// Uses a single Aho-Corasick O(n+m+z) pass over each file to locate all
/// `#[test]`, `with_default_timeout`, and `with_timeout` byte-positions,
/// then uses O(log L) binary-search (TAOCP Vol.3 §6.2.1 Algorithm B) via
/// `LineIndex` to map positions to lines and byte ranges.
///
/// A test function body is the region from the opening brace `{` of the fn
/// to the first unmatched closing brace `}`, scanning up to 40 lines.
///
/// Returns `Pass` when no violations are found or when the test directory
/// does not exist (e.g. in unit-test environments with fake repo paths).
pub fn check_timeout_wrappers(repo_root: &Path) -> NativeCheckResult {
    let test_dir = repo_root.join("tests/integration_tests");

    if !test_dir.exists() {
        return NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        };
    }

    run_timeout_wrapper_scan(&test_dir)
}

fn run_timeout_wrapper_scan(test_dir: &Path) -> NativeCheckResult {
    let files = match collect_rs_files(test_dir) {
        Ok(f) => f,
        Err(e) => {
            return NativeCheckResult {
                status: CheckStatus::Error,
                message: format!(
                    "Failed to walk integration test directory {}: {e}",
                    test_dir.display()
                ),
            };
        }
    };

    let ac = AhoCorasick::new(TIMEOUT_PATTERNS).expect("valid patterns");
    let (violations, read_errors) = scan_timeout_wrapper_files(&files, &ac);

    compliance_summary_to_native(timeout_wrapper_scan_result(&violations, &read_errors))
}

pub fn check_tailwind4_removed_angular_classes(repo_root: &Path) -> NativeCheckResult {
    match tailwind_removed_class_summary(repo_root) {
        Ok(summary) => compliance_summary_to_native(summary),
        Err(error) => NativeCheckResult {
            status: CheckStatus::Error,
            message: error,
        },
    }
}

fn tailwind_removed_class_summary(repo_root: &Path) -> Result<ComplianceSummary, String> {
    let template_dir = repo_root.join("ralph-gui/ui/src");

    if !template_dir.exists() {
        return Ok(tailwind_scan_result(&[], &[]));
    }

    let ac = AhoCorasick::new(
        REMOVED_TAILWIND4_ANGULAR_CLASSES
            .iter()
            .map(|pattern| pattern.literal),
    )
    .expect("valid Tailwind migration patterns");

    let (violations, read_errors) =
        collect_tailwind_template_violations(&template_dir, repo_root, &ac)?;
    Ok(tailwind_scan_result(&violations, &read_errors))
}

fn collect_rs_files(dir: &Path) -> std::io::Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    crate::io::scanner::collect_files_with_glob(dir, "*.rs", &mut files)?;
    files.retain(|p| !should_skip_file(p));
    files.sort();
    Ok(files)
}

fn collect_tailwind_template_violations(
    template_dir: &Path,
    repo_root: &Path,
    ac: &AhoCorasick,
) -> Result<(Vec<String>, Vec<String>), String> {
    let mut files = Vec::new();
    crate::io::scanner::collect_files_with_glob(template_dir, "*.html", &mut files).map_err(
        |error| {
            format!(
                "Failed to walk Angular template directory {}: {error}",
                template_dir.display()
            )
        },
    )?;
    files.sort();

    let (violations, read_errors) = files.into_iter().fold(
        (Vec::new(), Vec::new()),
        |(mut violations, mut read_errors), file_path| {
            match std::fs::read(&file_path) {
                Ok(content) => {
                    violations.extend(collect_removed_tailwind4_violations(
                        &file_path, repo_root, &content, ac,
                    ));
                }
                Err(error) => {
                    read_errors.push(format!("{}: read error: {error}", file_path.display()));
                }
            }
            (violations, read_errors)
        },
    );

    Ok((violations, read_errors))
}

fn scan_timeout_wrapper_files(files: &[PathBuf], ac: &AhoCorasick) -> (Vec<String>, Vec<String>) {
    let mut violations = Vec::new();
    let mut read_errors = Vec::new();

    for file_path in files {
        match std::fs::read(file_path) {
            Ok(content) => scan_file_for_violations_ac(file_path, &content, ac, &mut violations),
            Err(e) => read_errors.push(format!("{}: read error: {e}", file_path.display())),
        }
    }

    (violations, read_errors)
}

fn should_skip_file(path: &Path) -> bool {
    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default();

    matches!(file_name, "_TEMPLATE.rs" | "compliance_check.rs" | "mod.rs")
}

fn collect_removed_tailwind4_violations(
    file_path: &Path,
    repo_root: &Path,
    content: &[u8],
    ac: &AhoCorasick,
) -> Vec<String> {
    collect_tailwind_violations(file_path, repo_root, content, ac)
}

fn build_tailwind_violation(
    file_path: &Path,
    repo_root: &Path,
    content: &[u8],
    mat: &aho_corasick::Match,
    line_idx: &LineIndex,
) -> Option<(String, String)> {
    let rule = &REMOVED_TAILWIND4_ANGULAR_CLASSES[mat.pattern().as_usize()];
    let line_number = line_idx.line_number(mat.start()) + 1;
    let line_start = line_idx.line_start(mat.start());
    let line_bytes = line_idx.extract_line(content, mat.start());
    let local_offset = mat.start().saturating_sub(line_start);
    let token = extract_tailwind_token(line_bytes, local_offset)?;

    let candidate = normalize_tailwind_candidate(&token);
    if !tailwind_candidate_matches_rule(candidate, rule) {
        return None;
    }

    let dedupe_key = format!("{line_number}:{candidate}");
    let display_path = file_path.strip_prefix(repo_root).unwrap_or(file_path);
    let violation = format!(
        "{}:{}: Tailwind 3-only class '{}' does not exist in Tailwind 4; replace it with '{}'. This component/file needs rework. Look up the current Tailwind CSS v4 documentation and upgrade guide before changing it.",
        display_path.display(),
        line_number,
        candidate,
        rule.replacement,
    );

    Some((dedupe_key, violation))
}

fn collect_tailwind_violations(
    file_path: &Path,
    repo_root: &Path,
    content: &[u8],
    ac: &AhoCorasick,
) -> Vec<String> {
    let line_idx = LineIndex::new(content);
    let mut seen = HashSet::new();

    ac.find_iter(content)
        .filter_map(|mat| build_tailwind_violation(file_path, repo_root, content, &mat, &line_idx))
        .filter_map(|(dedupe_key, violation)| {
            if seen.insert(dedupe_key) {
                Some(violation)
            } else {
                None
            }
        })
        .collect()
}

/// Scan a single file using Aho-Corasick O(n+m+z) to find all `#[test]`,
/// `with_default_timeout`, and `with_timeout` byte-positions in one pass.
///
/// For each `#[test]` attribute, the enclosing test function body is located
/// via byte-level brace tracking and the O(1) `LineIndex::start_of_line`
/// lookup.  A violation is recorded when no timeout wrapper offset falls
/// within the body byte range `[body_start, body_end)`.
fn scan_file_for_violations_ac(
    file_path: &Path,
    content: &[u8],
    ac: &AhoCorasick,
    violations: &mut Vec<String>,
) {
    let line_idx = LineIndex::new(content);

    // Build byte-slice view of each line once per file (O(n), done once).
    let lines: Vec<&[u8]> = content.split(|&b| b == b'\n').collect();
    let (test_attr_offsets, timeout_offsets) =
        collect_test_and_timeout_offsets(content, ac, &line_idx);

    for test_start in test_attr_offsets {
        process_test_attribute(
            file_path,
            content,
            &lines,
            &line_idx,
            &timeout_offsets,
            test_start,
            violations,
        );
    }
}

fn collect_test_and_timeout_offsets(
    content: &[u8],
    ac: &AhoCorasick,
    line_idx: &LineIndex,
) -> (Vec<usize>, Vec<usize>) {
    ac.find_iter(content)
        .filter_map(|mat| classify_test_or_timeout(mat, content, line_idx))
        .fold(
            (Vec::new(), Vec::new()),
            |(mut tests, mut timeouts), kind| {
                match kind {
                    MatchOffset::Test(offset) => tests.push(offset),
                    MatchOffset::Timeout(offset) => timeouts.push(offset),
                }
                (tests, timeouts)
            },
        )
}

enum MatchOffset {
    Test(usize),
    Timeout(usize),
}

fn classify_test_or_timeout(
    mat: aho_corasick::Match,
    content: &[u8],
    line_idx: &LineIndex,
) -> Option<MatchOffset> {
    match mat.pattern().as_usize() {
        PAT_TEST_ATTR => {
            let line_bytes = line_idx.extract_line(content, mat.start());
            let trimmed = trim_ascii(line_bytes);
            if trimmed == b"#[test]" {
                Some(MatchOffset::Test(mat.start()))
            } else {
                None
            }
        }
        PAT_DEFAULT_TIMEOUT | PAT_TIMEOUT => Some(MatchOffset::Timeout(mat.start())),
        _ => None,
    }
}

fn process_test_attribute(
    file_path: &Path,
    content: &[u8],
    lines: &[&[u8]],
    line_idx: &LineIndex,
    timeout_offsets: &[usize],
    test_start: usize,
    violations: &mut Vec<String>,
) {
    if let Some(context) = gather_test_context(content, lines, line_idx, test_start) {
        if !timeout_inside(
            context.brace_line_byte_start,
            context.fn_end_byte,
            timeout_offsets,
        ) {
            violations.push(format!(
                "  {}:{}: test '{}' missing timeout wrapper (with_default_timeout or with_timeout)",
                file_path.display(),
                context.test_line + 1,
                context.test_name,
            ));
        }
    }
}

struct TestContext {
    test_line: usize,
    brace_line_byte_start: usize,
    fn_end_byte: usize,
    test_name: String,
}

fn gather_test_context(
    content: &[u8],
    lines: &[&[u8]],
    line_idx: &LineIndex,
    test_start: usize,
) -> Option<TestContext> {
    let n = lines.len();
    let test_line = line_idx.line_number(test_start);
    let fn_line_idx = find_fn_line_idx(lines, test_line + 1)?;
    if fn_line_idx >= n {
        return None;
    }

    let fn_line_str = std::str::from_utf8(lines[fn_line_idx]).ok()?;
    let test_name = extract_test_name(fn_line_str)
        .unwrap_or("<unknown>")
        .to_string();

    let brace_line = find_opening_brace_in_lines(lines, fn_line_idx, 5)?;
    let brace_line_byte_start = line_idx.start_of_line(brace_line);
    let cap_line = std::cmp::min(brace_line + 40, n);
    let body_scan_end = if cap_line >= n {
        content.len()
    } else {
        line_idx.start_of_line(cap_line)
    };
    let fn_end_byte = find_function_end_bytes(content, brace_line_byte_start, body_scan_end);

    Some(TestContext {
        test_line,
        brace_line_byte_start,
        fn_end_byte,
        test_name,
    })
}

fn timeout_inside(brace: usize, end: usize, timeout_offsets: &[usize]) -> bool {
    timeout_offsets.iter().any(|&pos| pos >= brace && pos < end)
}

fn find_fn_line_idx(lines: &[&[u8]], mut start_idx: usize) -> Option<usize> {
    const MAX_FN_LOOKAHEAD_LINES: usize = 8;
    let n = lines.len();

    for _ in 0..MAX_FN_LOOKAHEAD_LINES {
        if start_idx >= n {
            return None;
        }

        let trimmed = trim_ascii(lines[start_idx]);
        if trimmed.is_empty() || trimmed.starts_with(b"#") || trimmed.starts_with(b"//") {
            start_idx += 1;
            continue;
        }

        let fn_line_str = match std::str::from_utf8(lines[start_idx]) {
            Ok(s) => s,
            Err(_) => return None,
        };
        if is_fn_decl(fn_line_str) {
            return Some(start_idx);
        }

        break;
    }

    None
}

/// Find the end of a function body by tracking brace depth in raw bytes.
///
/// Scans `content[start..scan_end]` counting `{` and `}` bytes.  Returns the
/// byte offset **one past** the closing `}` when depth reaches 0, or
/// `scan_end` if the body is not closed within the scan window.
fn find_function_end_bytes(content: &[u8], start: usize, scan_end: usize) -> usize {
    let scan_end = scan_end.min(content.len());
    let mut depth: i32 = 0;
    for (i, &b) in content[start..scan_end].iter().enumerate() {
        if b == b'{' {
            depth += 1;
        } else if b == b'}' {
            depth -= 1;
            if depth == 0 {
                return start + i + 1;
            }
        }
    }
    scan_end
}

/// Find the index of the first line (in `lines`) at or after `from_idx`
/// that contains `{`, within `lookahead` additional lines.
fn find_opening_brace_in_lines(
    lines: &[&[u8]],
    from_idx: usize,
    lookahead: usize,
) -> Option<usize> {
    let end = std::cmp::min(from_idx + lookahead + 1, lines.len());
    lines[from_idx..end]
        .iter()
        .enumerate()
        .find_map(|(offset, line)| {
            if line.contains(&b'{') {
                Some(from_idx + offset)
            } else {
                None
            }
        })
}

/// Trim leading and trailing ASCII whitespace (space, tab, carriage-return)
/// from a byte slice.
fn trim_ascii(b: &[u8]) -> &[u8] {
    let is_ws = |&x: &u8| x == b' ' || x == b'\t' || x == b'\r';
    let start = b.iter().position(|x| !is_ws(x)).unwrap_or(b.len());
    let end = b
        .iter()
        .rposition(|x| !is_ws(x))
        .map(|i| i + 1)
        .unwrap_or(0);
    if start >= end {
        &[]
    } else {
        &b[start..end]
    }
}

fn is_fn_decl(line: &str) -> bool {
    let trimmed = line.trim();
    // Match: fn, pub fn, async fn, pub async fn, unsafe fn, pub unsafe fn, etc.
    let after_visibility = trimmed.strip_prefix("pub ").unwrap_or(trimmed);
    let after_async = after_visibility
        .strip_prefix("async ")
        .unwrap_or(after_visibility);
    let after_unsafe = after_async.strip_prefix("unsafe ").unwrap_or(after_async);
    after_unsafe.starts_with("fn ")
}

fn extract_test_name(line: &str) -> Option<&str> {
    let after_fn = line.split("fn ").nth(1)?;
    let name_end = after_fn
        .find(|c: char| !c.is_alphanumeric() && c != '_')
        .unwrap_or(after_fn.len());
    if name_end == 0 {
        return None;
    }
    Some(&after_fn[..name_end])
}

fn compliance_summary_to_native(summary: ComplianceSummary) -> NativeCheckResult {
    let status = CHECK_STATUS_MAPPING
        .get(summary.status_code as usize)
        .copied()
        .unwrap_or(CheckStatus::Error);

    NativeCheckResult {
        status,
        message: summary.message,
    }
}

#[path = "compliance_tests.rs"]
#[cfg(test)]
mod tests;
