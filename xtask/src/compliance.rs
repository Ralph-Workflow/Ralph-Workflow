use std::path::{Path, PathBuf};

use crate::verify::{CheckStatus, NativeCheckResult};

/// Scans integration test files for `#[test]` functions that do not call
/// `with_default_timeout` or `with_timeout` in their body.
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

    let files = match collect_rs_files(&test_dir) {
        Ok(f) => f,
        Err(e) => {
            return NativeCheckResult {
                status: CheckStatus::Warning,
                message: format!("Failed to walk integration test directory: {e}"),
            }
        }
    };

    let mut violations: Vec<String> = Vec::new();

    for file_path in &files {
        let contents = match std::fs::read_to_string(file_path) {
            Ok(c) => c,
            Err(e) => {
                violations.push(format!("{}: read error: {e}", file_path.display()));
                continue;
            }
        };

        let lines: Vec<&str> = contents.lines().collect();
        scan_file_for_violations(file_path, &lines, &mut violations);
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

fn collect_rs_files(dir: &Path) -> std::io::Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    collect_rs_files_inner(dir, &mut files)?;
    files.sort();
    Ok(files)
}

fn collect_rs_files_inner(dir: &Path, files: &mut Vec<PathBuf>) -> std::io::Result<()> {
    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();

        if path.is_dir() {
            collect_rs_files_inner(&path, files)?;
        } else if path.extension().and_then(|s| s.to_str()) == Some("rs") {
            if should_skip_file(&path) {
                continue;
            }
            files.push(path);
        }
    }
    Ok(())
}

fn should_skip_file(path: &Path) -> bool {
    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default();

    matches!(file_name, "_TEMPLATE.rs" | "compliance_check.rs" | "mod.rs")
}

fn scan_file_for_violations(file_path: &Path, lines: &[&str], violations: &mut Vec<String>) {
    let n = lines.len();

    for (i, line) in lines.iter().enumerate() {
        if line.trim() != "#[test]" {
            continue;
        }

        // Look for fn declaration on the next line
        let fn_line_idx = i + 1;
        if fn_line_idx >= n {
            continue;
        }

        if !is_fn_decl(lines[fn_line_idx]) {
            continue;
        }

        let test_name = extract_test_name(lines[fn_line_idx]).unwrap_or("<unknown>");

        // Find opening brace within the next 5 lines from the fn declaration
        let brace_line_idx = find_opening_brace(lines, fn_line_idx, 5);

        let Some(brace_idx) = brace_line_idx else {
            continue;
        };

        // Scan the function body using brace-depth tracking (capped at 40 lines as failsafe).
        // This prevents false-negatives from bleeding into the next test function's body.
        let cap = std::cmp::min(brace_idx + 40, n);
        let body_end = find_function_end(lines, brace_idx, cap);

        let has_timeout = lines[brace_idx..body_end]
            .iter()
            .any(|l| l.contains("with_default_timeout") || l.contains("with_timeout"));

        if !has_timeout {
            violations.push(format!(
                "  {}:{}: test '{}' missing timeout wrapper (with_default_timeout or with_timeout)",
                file_path.display(),
                i + 1, // 1-based line number of #[test]
                test_name,
            ));
        }
    }
}

/// Find the end of a function body by tracking brace depth.
/// Returns the index one past the closing `}` of the function.
/// Stops at `scan_end` as a failsafe for malformed files.
fn find_function_end(lines: &[&str], brace_start: usize, scan_end: usize) -> usize {
    let mut depth: i32 = 0;
    for (offset, line) in lines[brace_start..scan_end].iter().enumerate() {
        for ch in line.chars() {
            if ch == '{' {
                depth += 1;
            } else if ch == '}' {
                depth -= 1;
                if depth == 0 {
                    return brace_start + offset + 1;
                }
            }
        }
    }
    scan_end
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

fn find_opening_brace(lines: &[&str], from_idx: usize, lookahead: usize) -> Option<usize> {
    let end = std::cmp::min(from_idx + lookahead + 1, lines.len());
    lines[from_idx..end]
        .iter()
        .enumerate()
        .find_map(|(offset, line)| {
            if line.contains('{') {
                Some(from_idx + offset)
            } else {
                None
            }
        })
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
}
