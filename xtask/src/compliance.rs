use std::path::{Path, PathBuf};

use aho_corasick::AhoCorasick;

use crate::scanner::LineIndex;
use crate::verify::{CheckStatus, NativeCheckResult};

// Pattern IDs for the timeout-wrapper Aho-Corasick automaton (O(n+m+z) scan).
const PAT_TEST_ATTR: usize = 0; // "#[test]"
const PAT_DEFAULT_TIMEOUT: usize = 1; // "with_default_timeout"
const PAT_TIMEOUT: usize = 2; // "with_timeout"
const TIMEOUT_PATTERNS: &[&str] = &["#[test]", "with_default_timeout", "with_timeout"];

/// Scans `scripts/` and `tests/integration_tests/` for `.sh` files.
///
/// Shell scripts were migrated to Rust xtask commands; their presence after
/// migration is a regression.  Returns `Error` if any `.sh` file is found,
/// listing the offending paths.  Returns `Pass` when the directories do not
/// exist (e.g. in unit-test environments with fake repo paths).
pub fn check_no_shell_scripts(repo_root: &Path) -> NativeCheckResult {
    let scan_dirs = ["scripts", "tests/integration_tests"];
    let mut found: Vec<String> = Vec::new();

    for rel_dir in &scan_dirs {
        let dir = repo_root.join(rel_dir);
        if !dir.exists() {
            continue;
        }
        collect_sh_files(&dir, &mut found);
    }

    if found.is_empty() {
        NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        }
    } else {
        NativeCheckResult {
            status: CheckStatus::Error,
            message: format!(
                "Found {} .sh file(s) that must not exist after the shell-script migration:\n{}",
                found.len(),
                found.join("\n")
            ),
        }
    }
}

fn collect_sh_files(dir: &Path, out: &mut Vec<String>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_sh_files(&path, out);
        } else if path.extension().and_then(|e| e.to_str()) == Some("sh") {
            out.push(path.display().to_string());
        }
    }
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

    let files = collect_rs_files(&test_dir);

    let ac = AhoCorasick::new(TIMEOUT_PATTERNS).expect("valid patterns");
    let mut violations: Vec<String> = Vec::new();

    for file_path in &files {
        let content = match std::fs::read(file_path) {
            Ok(c) => c,
            Err(e) => {
                violations.push(format!("{}: read error: {e}", file_path.display()));
                continue;
            }
        };

        scan_file_for_violations_ac(file_path, &content, &ac, &mut violations);
    }

    if violations.is_empty() {
        NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        }
    } else {
        NativeCheckResult {
            status: CheckStatus::Warning,
            message: format!(
                "Found {} test(s) missing timeout wrapper:\n{}",
                violations.len(),
                violations.join("\n")
            ),
        }
    }
}

fn collect_rs_files(dir: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    crate::scanner::collect_files_with_glob(dir, "*.rs", &mut files);
    files.retain(|p| !should_skip_file(p));
    files.sort();
    files
}

fn should_skip_file(path: &Path) -> bool {
    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default();

    matches!(file_name, "_TEMPLATE.rs" | "compliance_check.rs" | "mod.rs")
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
    let n = lines.len();

    // Single O(n+m+z) Aho-Corasick pass over the file bytes.
    let mut test_attr_offsets: Vec<usize> = Vec::new();
    let mut timeout_offsets: Vec<usize> = Vec::new();

    for mat in ac.find_iter(content) {
        match mat.pattern().as_usize() {
            PAT_TEST_ATTR => {
                // Accept only when the entire trimmed line equals "#[test]"
                // (same semantics as the original line.trim() == "#[test]" check).
                let line_bytes = line_idx.extract_line(content, mat.start());
                let trimmed = trim_ascii(line_bytes);
                if trimmed == b"#[test]" {
                    test_attr_offsets.push(mat.start());
                }
            }
            PAT_DEFAULT_TIMEOUT | PAT_TIMEOUT => {
                timeout_offsets.push(mat.start());
            }
            _ => {}
        }
    }

    for test_start in test_attr_offsets {
        // O(log L) binary-search line lookup via LineIndex.
        let test_line = line_idx.line_number(test_start); // 0-based
        let fn_line_idx = test_line + 1;
        if fn_line_idx >= n {
            continue;
        }

        let fn_line_str = match std::str::from_utf8(lines[fn_line_idx]) {
            Ok(s) => s,
            Err(_) => continue,
        };
        if !is_fn_decl(fn_line_str) {
            continue;
        }

        let test_name = extract_test_name(fn_line_str).unwrap_or("<unknown>");

        // Find the line that contains the opening `{` (up to 5 lines lookahead).
        let brace_line = match find_opening_brace_in_lines(&lines, fn_line_idx, 5) {
            Some(l) => l,
            None => continue,
        };

        // O(1) byte offset of the brace line start via LineIndex.start_of_line.
        let brace_line_byte_start = line_idx.start_of_line(brace_line);

        // Cap the body scan at 40 lines past the brace line (failsafe for
        // malformed files), matching the original 40-line limit.
        let cap_line = std::cmp::min(brace_line + 40, n);
        let body_scan_end = if cap_line >= n {
            content.len()
        } else {
            line_idx.start_of_line(cap_line)
        };

        // Brace-depth tracking over raw bytes to find the exact body end.
        let fn_end_byte = find_function_end_bytes(content, brace_line_byte_start, body_scan_end);

        // O(z) check: does any timeout wrapper offset fall inside the body?
        let has_timeout = timeout_offsets
            .iter()
            .any(|&pos| pos >= brace_line_byte_start && pos < fn_end_byte);

        if !has_timeout {
            violations.push(format!(
                "  {}:{}: test '{}' missing timeout wrapper (with_default_timeout or with_timeout)",
                file_path.display(),
                test_line + 1, // 1-based line number of #[test]
                test_name,
            ));
        }
    }
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn make_temp_dir(name: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!("xtask-compliance-{name}"));
        let _ = fs::remove_dir_all(&base);
        fs::create_dir_all(&base).unwrap();
        base
    }

    fn write_file(dir: &Path, path: &str, content: &str) {
        let full = dir.join(path);
        if let Some(parent) = full.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(full, content).unwrap();
    }

    // ── check_no_shell_scripts tests ──────────────────────────────────────────

    #[test]
    fn test_check_no_shell_scripts_pass_when_dirs_missing() {
        let result = check_no_shell_scripts(Path::new("/nonexistent-fake-repo-path"));
        assert_eq!(result.status, CheckStatus::Pass);
        assert!(result.message.is_empty());
    }

    #[test]
    fn test_check_no_shell_scripts_pass_when_no_sh_files() {
        let dir = make_temp_dir("no-sh-pass");
        fs::create_dir_all(dir.join("scripts")).unwrap();
        fs::create_dir_all(dir.join("tests/integration_tests")).unwrap();
        write_file(&dir, "scripts/README.md", "# no scripts here");
        write_file(&dir, "tests/integration_tests/my_test.rs", "// rust file");

        let result = check_no_shell_scripts(&dir);
        assert_eq!(
            result.status,
            CheckStatus::Pass,
            "message: {}",
            result.message
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_no_shell_scripts_error_when_sh_file_found() {
        let dir = make_temp_dir("sh-found");
        fs::create_dir_all(dir.join("scripts")).unwrap();
        write_file(&dir, "scripts/migrate.sh", "#!/bin/bash\necho hello");

        let result = check_no_shell_scripts(&dir);
        assert_eq!(result.status, CheckStatus::Error);
        assert!(
            result.message.contains("migrate.sh"),
            "message must mention the file: {}",
            result.message
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_no_shell_scripts_error_when_sh_in_integration_tests() {
        let dir = make_temp_dir("sh-in-integration");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();
        write_file(&dir, "tests/integration_tests/old_check.sh", "#!/bin/bash");

        let result = check_no_shell_scripts(&dir);
        assert_eq!(result.status, CheckStatus::Error);
        assert!(
            result.message.contains("old_check.sh"),
            "{}",
            result.message
        );

        let _ = fs::remove_dir_all(&dir);
    }

    // ── check_timeout_wrappers tests ──────────────────────────────────────────

    #[test]
    fn test_check_timeout_wrappers_pass_when_dir_missing() {
        let result = check_timeout_wrappers(Path::new("/nonexistent-fake-repo-path"));
        assert_eq!(result.status, CheckStatus::Pass);
        assert!(result.message.is_empty());
    }

    #[test]
    fn test_check_timeout_wrappers_pass_when_all_tests_wrapped() {
        let dir = make_temp_dir("pass");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        write_file(
            &dir,
            "tests/integration_tests/my_test.rs",
            r#"
#[test]
fn test_something() {
    with_default_timeout(|| {
        // test body
    });
}
"#,
        );

        let result = check_timeout_wrappers(&dir);
        assert_eq!(
            result.status,
            CheckStatus::Pass,
            "message: {}",
            result.message
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_timeout_wrappers_warning_when_missing_wrapper() {
        let dir = make_temp_dir("warn");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        write_file(
            &dir,
            "tests/integration_tests/bad_test.rs",
            r#"
#[test]
fn test_missing_timeout() {
    // No timeout wrapper here
    assert!(true);
}
"#,
        );

        let result = check_timeout_wrappers(&dir);
        assert_eq!(result.status, CheckStatus::Warning);
        assert!(
            result.message.contains("test_missing_timeout"),
            "message should mention the failing test: {}",
            result.message
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_timeout_wrappers_skip_template_file() {
        let dir = make_temp_dir("skip-template");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        // _TEMPLATE.rs should be skipped even if it has violations
        write_file(
            &dir,
            "tests/integration_tests/_TEMPLATE.rs",
            r#"
#[test]
fn test_template_no_timeout() {
    // Template test without timeout wrapper
    assert!(true);
}
"#,
        );

        let result = check_timeout_wrappers(&dir);
        assert_eq!(
            result.status,
            CheckStatus::Pass,
            "message: {}",
            result.message
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_timeout_wrappers_handles_nested_module() {
        let dir = make_temp_dir("nested");

        write_file(
            &dir,
            "tests/integration_tests/submodule/mod.rs",
            r#"
#[test]
fn test_nested_missing() {
    assert!(true);
}
"#,
        );

        // mod.rs is skipped, so no violations
        let result = check_timeout_wrappers(&dir);
        assert_eq!(
            result.status,
            CheckStatus::Pass,
            "message: {}",
            result.message
        );

        write_file(
            &dir,
            "tests/integration_tests/submodule/tests.rs",
            r#"
#[test]
fn test_nested_no_timeout() {
    assert!(true);
}
"#,
        );

        let result2 = check_timeout_wrappers(&dir);
        assert_eq!(result2.status, CheckStatus::Warning);
        assert!(result2.message.contains("test_nested_no_timeout"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_timeout_wrappers_with_timeout_variant() {
        let dir = make_temp_dir("with-timeout");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        write_file(
            &dir,
            "tests/integration_tests/slow_test.rs",
            r#"
#[test]
fn test_slow() {
    with_timeout(|| {
        // slow test body
    }, std::time::Duration::from_secs(30));
}
"#,
        );

        let result = check_timeout_wrappers(&dir);
        assert_eq!(
            result.status,
            CheckStatus::Pass,
            "message: {}",
            result.message
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_timeout_wrappers_multiple_tests_mixed() {
        let dir = make_temp_dir("mixed");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        write_file(
            &dir,
            "tests/integration_tests/mixed.rs",
            r#"
#[test]
fn test_ok() {
    with_default_timeout(|| {
        assert!(true);
    });
}

#[test]
fn test_missing() {
    assert!(true);
}

#[test]
fn test_also_ok() {
    with_timeout(|| {
        assert!(true);
    }, std::time::Duration::from_secs(10));
}
"#,
        );

        let result = check_timeout_wrappers(&dir);
        assert_eq!(result.status, CheckStatus::Warning);
        assert!(result.message.contains("test_missing"));
        assert!(!result.message.contains("test_ok"));
        assert!(!result.message.contains("test_also_ok"));
        let _ = fs::remove_dir_all(&dir);
    }

    // ── New Aho-Corasick specific tests ───────────────────────────────────────

    #[test]
    fn test_check_timeout_wrappers_fn_with_brace_on_same_line() {
        // fn declaration and opening brace on the same line (e.g. `fn test_foo() {`)
        let dir = make_temp_dir("brace-same-line");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        write_file(
            &dir,
            "tests/integration_tests/inline.rs",
            "#[test]\nfn test_inline() {\n    with_default_timeout(|| assert!(true));\n}\n",
        );

        let result = check_timeout_wrappers(&dir);
        assert_eq!(
            result.status,
            CheckStatus::Pass,
            "fn with brace on same line should pass when wrapper present: {}",
            result.message
        );

        // Also check missing wrapper on same-line-brace fn.
        let dir2 = make_temp_dir("brace-same-line-missing");
        let test_dir2 = dir2.join("tests/integration_tests");
        fs::create_dir_all(&test_dir2).unwrap();

        write_file(
            &dir2,
            "tests/integration_tests/inline_missing.rs",
            "#[test]\nfn test_inline_missing() {\n    assert!(true);\n}\n",
        );

        let result2 = check_timeout_wrappers(&dir2);
        assert_eq!(result2.status, CheckStatus::Warning);
        assert!(
            result2.message.contains("test_inline_missing"),
            "{}",
            result2.message
        );

        let _ = fs::remove_dir_all(&dir);
        let _ = fs::remove_dir_all(&dir2);
    }

    #[test]
    fn test_check_timeout_wrappers_multiple_files_mixed() {
        // Two files: one passing, one failing.
        let dir = make_temp_dir("multi-file-mixed");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        write_file(
            &dir,
            "tests/integration_tests/good.rs",
            r#"
#[test]
fn test_good() {
    with_default_timeout(|| {
        assert!(true);
    });
}
"#,
        );

        write_file(
            &dir,
            "tests/integration_tests/bad.rs",
            r#"
#[test]
fn test_bad() {
    assert!(false);
}
"#,
        );

        let result = check_timeout_wrappers(&dir);
        assert_eq!(result.status, CheckStatus::Warning);
        assert!(
            result.message.contains("test_bad"),
            "message must mention the bad test: {}",
            result.message
        );
        assert!(
            !result.message.contains("test_good"),
            "message must not mention the good test: {}",
            result.message
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_timeout_wrappers_timeout_outside_body_not_counted() {
        // A timeout wrapper present in a sibling function must not satisfy
        // the constraint for a test that lacks one.
        let dir = make_temp_dir("timeout-outside-body");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        write_file(
            &dir,
            "tests/integration_tests/sibling.rs",
            r#"
#[test]
fn test_has_timeout() {
    with_default_timeout(|| {
        assert!(true);
    });
}

#[test]
fn test_lacks_timeout() {
    assert!(true);
}
"#,
        );

        let result = check_timeout_wrappers(&dir);
        assert_eq!(result.status, CheckStatus::Warning);
        assert!(
            result.message.contains("test_lacks_timeout"),
            "test_lacks_timeout should be flagged: {}",
            result.message
        );
        assert!(
            !result.message.contains("test_has_timeout"),
            "test_has_timeout should NOT be flagged: {}",
            result.message
        );

        let _ = fs::remove_dir_all(&dir);
    }
}
