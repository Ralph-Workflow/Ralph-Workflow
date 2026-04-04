use std::collections::HashSet;
use std::path::{Path, PathBuf};

use aho_corasick::AhoCorasick;

use crate::io::scanner::LineIndex;
use crate::runtime::verify::{CheckStatus, NativeCheckResult};

// Pattern IDs for the timeout-wrapper Aho-Corasick automaton (O(n+m+z) scan).
const PAT_TEST_ATTR: usize = 0; // "#[test]"
const PAT_DEFAULT_TIMEOUT: usize = 1; // "with_default_timeout"
const PAT_TIMEOUT: usize = 2; // "with_timeout"
const TIMEOUT_PATTERNS: &[&str] = &["#[test]", "with_default_timeout", "with_timeout"];

struct RemovedTailwindClass {
    literal: &'static str,
    replacement: &'static str,
    is_prefix: bool,
}

const REMOVED_TAILWIND4_ANGULAR_CLASSES: &[RemovedTailwindClass] = &[
    RemovedTailwindClass {
        literal: "bg-opacity-",
        replacement: "bg-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "text-opacity-",
        replacement: "text-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "border-opacity-",
        replacement: "border-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "divide-opacity-",
        replacement: "divide-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "ring-opacity-",
        replacement: "ring-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "placeholder-opacity-",
        replacement: "placeholder-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "flex-shrink-",
        replacement: "shrink-*",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "flex-grow-",
        replacement: "grow-*",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "overflow-ellipsis",
        replacement: "text-ellipsis",
        is_prefix: false,
    },
    RemovedTailwindClass {
        literal: "decoration-slice",
        replacement: "box-decoration-slice",
        is_prefix: false,
    },
    RemovedTailwindClass {
        literal: "decoration-clone",
        replacement: "box-decoration-clone",
        is_prefix: false,
    },
];

/// Scans `scripts/` and `tests/integration_tests/` for `.sh` files.
///
/// Shell scripts were migrated to Rust xtask commands; their presence after
/// migration is a regression.  Returns `Error` if any `.sh` file is found,
/// listing the offending paths.  Returns `Pass` when the directories do not
/// exist (e.g. in unit-test environments with fake repo paths).
pub fn check_no_shell_scripts(repo_root: &Path) -> NativeCheckResult {
    let scan_dirs = ["scripts", "tests/integration_tests"];
    let mut found: Vec<String> = Vec::new();
    let mut walk_errors: Vec<String> = Vec::new();

    for rel_dir in &scan_dirs {
        let dir = repo_root.join(rel_dir);
        if !dir.exists() {
            continue;
        }
        if let Err(e) = collect_sh_files(&dir, &mut found) {
            walk_errors.push(format!("read_dir error for {}: {e}", dir.display()));
        }
    }

    if !walk_errors.is_empty() {
        return NativeCheckResult {
            status: CheckStatus::Error,
            message: format!(
                "Failed to scan for .sh files due to directory walk errors:\n{}",
                walk_errors.join("\n")
            ),
        };
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

fn collect_sh_files(dir: &Path, out: &mut Vec<String>) -> std::io::Result<()> {
    let entries = std::fs::read_dir(dir)?;
    for entry in entries {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            collect_sh_files(&path, out)?;
        } else if path.extension().and_then(|e| e.to_str()) == Some("sh") {
            out.push(path.display().to_string());
        }
    }

    Ok(())
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

    let files = match collect_rs_files(&test_dir) {
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
    let mut violations: Vec<String> = Vec::new();
    let mut read_errors: Vec<String> = Vec::new();

    for file_path in &files {
        let content = match std::fs::read(file_path) {
            Ok(c) => c,
            Err(e) => {
                read_errors.push(format!("{}: read error: {e}", file_path.display()));
                continue;
            }
        };

        scan_file_for_violations_ac(file_path, &content, &ac, &mut violations);
    }

    if !read_errors.is_empty() {
        return NativeCheckResult {
            status: CheckStatus::Error,
            message: format!(
                "Failed to read {} integration test file(s) during timeout-wrapper compliance scan:\n{}",
                read_errors.len(),
                read_errors.join("\n")
            ),
        };
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

pub fn check_tailwind4_removed_angular_classes(repo_root: &Path) -> NativeCheckResult {
    let template_dir = repo_root.join("ralph-gui/ui/src");

    if !template_dir.exists() {
        return NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        };
    }

    let mut files = Vec::new();
    if let Err(error) =
        crate::io::scanner::collect_files_with_glob(&template_dir, "*.html", &mut files)
    {
        return NativeCheckResult {
            status: CheckStatus::Error,
            message: format!(
                "Failed to walk Angular template directory {}: {error}",
                template_dir.display()
            ),
        };
    }
    files.sort();

    let ac = AhoCorasick::new(
        REMOVED_TAILWIND4_ANGULAR_CLASSES
            .iter()
            .map(|pattern| pattern.literal),
    )
    .expect("valid Tailwind migration patterns");

    let mut violations = Vec::new();
    let mut read_errors = Vec::new();

    for file_path in &files {
        let content = match std::fs::read(file_path) {
            Ok(content) => content,
            Err(error) => {
                read_errors.push(format!("{}: read error: {error}", file_path.display()));
                continue;
            }
        };

        scan_file_for_removed_tailwind4_classes(
            file_path,
            repo_root,
            &content,
            &ac,
            &mut violations,
        );
    }

    if !read_errors.is_empty() {
        return NativeCheckResult {
            status: CheckStatus::Error,
            message: format!(
                "Failed to read {} Angular template file(s) during Tailwind 4 migration scan:\n{}",
                read_errors.len(),
                read_errors.join("\n")
            ),
        };
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
                "Found {} Tailwind 3-only class usage(s) in Angular templates that do not exist in Tailwind 4. Each affected component/file needs rework:\n{}",
                violations.len(),
                violations.join("\n")
            ),
        }
    }
}

/// Scans test files (excluding system_tests) for direct git commit operations and
/// risky workspace construction patterns that could bypass the test isolation policy.
///
/// This enforces two policies:
/// 1. No real git mutations in tests (commit operations are forbidden)
/// 2. No workspace construction using CWD or repo-root paths (must use TempDir/TestTempDir)
///
/// Scanned directories:
/// - `tests/integration_tests`
/// - `tests/process_system_tests`
/// - `ralph-workflow/src/mcp_server/tests/` (MCP e2e tests)
///
/// System tests in `tests/system_tests/` are allowed to use real git for testing
/// git functionality itself.
///
/// Uses Aho-Corasick O(n+m+z) scan for these patterns:
///
/// **Git mutation patterns (always forbidden):**
/// - `Repository::commit` — direct call to Repository::commit
/// - `.commit(` — method call on any object
/// - `CommitEffect` — type reference in effect handlers
///
/// **Risky workspace construction patterns (forbidden in tests):**
/// - `WorkspaceFs::new(std::env::current_dir` — workspace rooted at CWD (real repo)
/// - `WorkspaceFs::new(PathBuf::from(".")` — workspace rooted at CWD (real repo)
///
/// Returns `Pass` when no violations are found or when the test directory does not exist.
pub fn check_no_real_git_in_tests(repo_root: &Path) -> NativeCheckResult {
    // Scan integration_tests, process_system_tests, and MCP e2e tests.
    // system_tests are excluded (they are allowed to use real git).
    let test_dirs = [
        repo_root.join("tests/integration_tests"),
        repo_root.join("tests/process_system_tests"),
        repo_root.join("ralph-workflow/src/mcp_server/tests"),
        repo_root.join("mcp-server/tests"),
    ];

    // Collect files to scan
    let mut files = Vec::new();
    for test_dir in &test_dirs {
        if test_dir.exists() {
            if let Err(e) = collect_rs_files_from(test_dir, &mut files) {
                return NativeCheckResult {
                    status: CheckStatus::Error,
                    message: format!("Failed to walk test directory {}: {e}", test_dir.display()),
                };
            }
        }
    }

    if files.is_empty() {
        return NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        };
    }

    // Patterns that indicate real git usage or risky CWD workspace construction in tests.
    // All of these are policy violations — tests must use MemoryWorkspace or TempDir-backed
    // WorkspaceFs, never the real repository directory.
    const GIT_VIOLATION_PATTERNS: &[&str] = &[
        "Repository::commit",
        ".commit(",
        "CommitEffect",
        "WorkspaceFs::new(std::env::current_dir",
        "WorkspaceFs::new(PathBuf::from(\".\")",
    ];

    let ac = AhoCorasick::new(GIT_VIOLATION_PATTERNS).expect("valid git violation patterns");
    let mut violations: Vec<String> = Vec::new();
    let mut read_errors: Vec<String> = Vec::new();

    for file_path in &files {
        let content = match std::fs::read(file_path) {
            Ok(c) => c,
            Err(e) => {
                read_errors.push(format!("{}: read error: {e}", file_path.display()));
                continue;
            }
        };

        scan_file_for_git_violations(file_path, &content, &ac, &mut violations);
    }

    if !read_errors.is_empty() {
        return NativeCheckResult {
            status: CheckStatus::Error,
            message: format!(
                "Failed to read {} test file(s) during git-violation scan:\n{}",
                read_errors.len(),
                read_errors.join("\n")
            ),
        };
    }

    if violations.is_empty() {
        NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        }
    } else {
        NativeCheckResult {
            status: CheckStatus::Error,
            message: format!(
                "Found {} test file(s) with real git operations or risky workspace construction \
                (real git mutations and CWD-rooted workspaces are forbidden in tests — use \
                MemoryWorkspace or TempDir-backed WorkspaceFs):\n{}",
                violations.len(),
                violations.join("\n")
            ),
        }
    }
}

fn collect_rs_files_from(dir: &Path, out: &mut Vec<PathBuf>) -> std::io::Result<()> {
    if !dir.exists() {
        return Ok(());
    }

    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();

        // Skip system_tests - those are allowed to use real git
        if path.components().any(|c| c.as_os_str() == "system_tests") {
            continue;
        }

        if path.is_dir() {
            collect_rs_files_from(&path, out)?;
        } else if path.extension().and_then(|e| e.to_str()) == Some("rs")
            && !should_skip_file(&path)
        {
            out.push(path);
        }
    }

    Ok(())
}

fn scan_file_for_git_violations(
    file_path: &Path,
    content: &[u8],
    ac: &AhoCorasick,
    violations: &mut Vec<String>,
) {
    let line_idx = LineIndex::new(content);

    for mat in ac.find_iter(content) {
        let line_number = line_idx.line_number(mat.start()) + 1;
        let line_bytes = line_idx.extract_line(content, mat.start());
        let line_str = String::from_utf8_lossy(line_bytes);

        violations.push(format!(
            "{}:{}: found git operation '{}' — tests must use MemoryWorkspace or MockAppEffectHandler",
            file_path.display(),
            line_number,
            line_str.trim()
        ));
    }
}

fn collect_rs_files(dir: &Path) -> std::io::Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    crate::io::scanner::collect_files_with_glob(dir, "*.rs", &mut files)?;
    files.retain(|p| !should_skip_file(p));
    files.sort();
    Ok(files)
}

fn should_skip_file(path: &Path) -> bool {
    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default();

    matches!(file_name, "_TEMPLATE.rs" | "compliance_check.rs" | "mod.rs")
}

fn scan_file_for_removed_tailwind4_classes(
    file_path: &Path,
    repo_root: &Path,
    content: &[u8],
    ac: &AhoCorasick,
    violations: &mut Vec<String>,
) {
    let line_idx = LineIndex::new(content);
    let mut seen = HashSet::new();

    for mat in ac.find_iter(content) {
        let rule = &REMOVED_TAILWIND4_ANGULAR_CLASSES[mat.pattern().as_usize()];
        let line_number = line_idx.line_number(mat.start()) + 1;
        let line_start = line_idx.line_start(mat.start());
        let line_bytes = line_idx.extract_line(content, mat.start());
        let local_offset = mat.start().saturating_sub(line_start);
        let Some(token) = extract_tailwind_token(line_bytes, local_offset) else {
            continue;
        };
        let candidate = normalize_tailwind_candidate(&token);
        let matches_rule = if rule.is_prefix {
            candidate.starts_with(rule.literal)
        } else {
            candidate == rule.literal
        };
        if !matches_rule {
            continue;
        }

        let dedupe_key = format!("{line_number}:{candidate}");
        if !seen.insert(dedupe_key) {
            continue;
        }

        let display_path = file_path.strip_prefix(repo_root).unwrap_or(file_path);
        violations.push(format!(
            "{}:{}: Tailwind 3-only class '{}' does not exist in Tailwind 4; replace it with '{}'. This component/file needs rework. Look up the current Tailwind CSS v4 documentation and upgrade guide before changing it.",
            display_path.display(),
            line_number,
            candidate,
            rule.replacement
        ));
    }
}

fn extract_tailwind_token(line: &[u8], match_offset: usize) -> Option<String> {
    if match_offset >= line.len() {
        return None;
    }

    let mut start = match_offset;
    while start > 0 && is_tailwind_token_char(line[start - 1]) {
        start -= 1;
    }

    let mut end = match_offset;
    while end < line.len() && is_tailwind_token_char(line[end]) {
        end += 1;
    }

    if start == end {
        return None;
    }

    Some(String::from_utf8_lossy(&line[start..end]).to_string())
}

fn is_tailwind_token_char(byte: u8) -> bool {
    byte.is_ascii_alphanumeric()
        || matches!(
            byte,
            b'-' | b'_' | b':' | b'/' | b'[' | b']' | b'!' | b'.' | b'(' | b')'
        )
}

fn normalize_tailwind_candidate(token: &str) -> &str {
    token
        .rsplit(':')
        .next()
        .unwrap_or(token)
        .trim_start_matches('!')
        .trim_end_matches('!')
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
        let mut fn_line_idx = test_line + 1;
        if fn_line_idx >= n {
            continue;
        }

        // Some tests include additional attributes between `#[test]` and the
        // function declaration (e.g., `#[ignore]`, `#[cfg_attr]`).
        // Advance to the next plausible `fn` declaration within a small bound.
        const MAX_FN_LOOKAHEAD_LINES: usize = 8;
        let mut found_fn = None;
        for _ in 0..MAX_FN_LOOKAHEAD_LINES {
            if fn_line_idx >= n {
                break;
            }

            let trimmed = trim_ascii(lines[fn_line_idx]);
            if trimmed.is_empty() || trimmed.starts_with(b"#") || trimmed.starts_with(b"//") {
                fn_line_idx += 1;
                continue;
            }

            let fn_line_str = match std::str::from_utf8(lines[fn_line_idx]) {
                Ok(s) => s,
                Err(_) => break,
            };
            if is_fn_decl(fn_line_str) {
                found_fn = Some(fn_line_idx);
            }
            break;
        }

        let Some(fn_line_idx) = found_fn else {
            continue;
        };

        let fn_line_str = match std::str::from_utf8(lines[fn_line_idx]) {
            Ok(s) => s,
            Err(_) => continue,
        };

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

/// Verifies that `mcp-server` has no dependency on `ralph-workflow` (direct or
/// transitive). This enforces the standalone principle: mcp-server must be usable
/// without ralph-workflow in the dependency graph.
///
/// Uses `cargo metadata --format-version 1` to get the full dependency graph and
/// traverses all dependencies of mcp-server to verify ralph-workflow is not present.
pub fn check_mcp_server_dep_isolation(repo_root: &Path) -> NativeCheckResult {
    // Skip if the repo root doesn't exist (e.g., fake path in tests)
    if !repo_root.exists() {
        return NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        };
    }

    let output = std::process::Command::new("cargo")
        .args([
            "metadata",
            "--format-version",
            "1",
            "--manifest-path",
            "mcp-server/Cargo.toml",
        ])
        .current_dir(repo_root)
        .output();

    let output = match output {
        Ok(o) => o,
        Err(_) => {
            // If cargo fails to run (e.g., no Cargo.toml), skip the check
            return NativeCheckResult {
                status: CheckStatus::Pass,
                message: String::new(),
            };
        }
    };

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        // If the package is not found or Cargo.toml is missing, skip the check
        // (not an error in test scenarios with fake repo paths)
        if stderr.contains("package not found")
            || stderr.contains("`mcp-server` is not found")
            || stderr.contains("could not find `Cargo.toml`")
            || stderr.contains("does not exist")
        {
            return NativeCheckResult {
                status: CheckStatus::Pass,
                message: String::new(),
            };
        }
        return NativeCheckResult {
            status: CheckStatus::Error,
            message: format!("DEPENDENCY ISOLATION VIOLATION: cargo metadata failed: {stderr}"),
        };
    }

    // Parse cargo metadata JSON manually using serde_json
    let metadata_str = String::from_utf8_lossy(&output.stdout);
    let metadata: serde_json::Value = match serde_json::from_str(&metadata_str) {
        Ok(v) => v,
        Err(e) => {
            return NativeCheckResult {
                status: CheckStatus::Error,
                message: format!(
                    "DEPENDENCY ISOLATION VIOLATION: failed to parse cargo metadata: {e}"
                ),
            };
        }
    };

    // Build a map of package name -> package dependencies
    // cargo metadata structure: { "packages": [{ "name": "...", "dependencies": [{ "name": "..." }, ...] }, ...] }
    let mut deps_map: std::collections::HashMap<&str, Vec<&str>> = std::collections::HashMap::new();

    if let Some(packages) = metadata.get("packages").and_then(|p| p.as_array()) {
        for pkg in packages {
            let name = pkg.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let deps = pkg
                .get("dependencies")
                .and_then(|d| d.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|d| d.get("name").and_then(|n| n.as_str()))
                        .collect()
                })
                .unwrap_or_default();
            deps_map.insert(name, deps);
        }
    }

    // BFS traversal from mcp-server to find all transitive dependencies
    let mut visited: std::collections::HashSet<&str> = std::collections::HashSet::new();
    let mut queue: std::collections::VecDeque<&str> = std::collections::VecDeque::new();
    queue.push_back("mcp-server");
    visited.insert("mcp-server");

    while let Some(current) = queue.pop_front() {
        if let Some(deps) = deps_map.get(current) {
            for dep in deps {
                if *dep == "ralph-workflow" {
                    return NativeCheckResult {
                        status: CheckStatus::Error,
                        message: format!(
                            "DEPENDENCY ISOLATION VIOLATION: mcp-server must not depend on ralph-workflow (direct or transitive). Found dependency path: mcp-server -> {}. Remove the dependency and use adapter traits instead.",
                            dep
                        ),
                    };
                }
                if !visited.contains(dep) {
                    visited.insert(dep);
                    queue.push_back(dep);
                }
            }
        }
    }

    NativeCheckResult {
        status: CheckStatus::Pass,
        message: String::new(),
    }
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

    #[cfg(unix)]
    #[test]
    fn test_check_no_shell_scripts_errors_on_unreadable_directory() {
        use std::os::unix::fs::PermissionsExt;

        let dir = make_temp_dir("no-shell-unreadable");
        let scripts_dir = dir.join("scripts");
        fs::create_dir_all(&scripts_dir).unwrap();
        write_file(&dir, "scripts/migrate.sh", "#!/bin/bash\necho hi");

        let mut perms = fs::metadata(&scripts_dir).unwrap().permissions();
        perms.set_mode(0o000);
        fs::set_permissions(&scripts_dir, perms).unwrap();

        let result = check_no_shell_scripts(&dir);

        // Restore permissions so cleanup works.
        let mut perms_restore = fs::metadata(&scripts_dir).unwrap().permissions();
        perms_restore.set_mode(0o755);
        let _ = fs::set_permissions(&scripts_dir, perms_restore);

        assert_eq!(result.status, CheckStatus::Error, "{}", result.message);
        assert!(
            result.message.contains("read_dir") || result.message.contains("Failed"),
            "message must mention directory walk error: {}",
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
    fn test_check_timeout_wrappers_finds_fn_after_additional_attributes() {
        let dir = make_temp_dir("warn-extra-attrs");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        write_file(
            &dir,
            "tests/integration_tests/bad_test_attr.rs",
            r#"
#[test]
#[ignore = "https://example.com/issues/123"]
fn test_missing_timeout_with_extra_attr() {
    // No timeout wrapper here
    assert!(true);
}
"#,
        );

        let result = check_timeout_wrappers(&dir);
        assert_eq!(result.status, CheckStatus::Warning);
        assert!(
            result
                .message
                .contains("test_missing_timeout_with_extra_attr"),
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

    #[cfg(unix)]
    #[test]
    fn test_check_timeout_wrappers_reports_read_errors_separately() {
        use std::os::unix::fs::PermissionsExt;

        let dir = make_temp_dir("read-error");
        let test_dir = dir.join("tests/integration_tests");
        fs::create_dir_all(&test_dir).unwrap();

        let file_rel = "tests/integration_tests/unreadable.rs";
        write_file(
            &dir,
            file_rel,
            "#[test]\nfn test_unreadable() { assert!(true); }\n",
        );

        let file_path = dir.join(file_rel);
        let mut perms = fs::metadata(&file_path).unwrap().permissions();
        perms.set_mode(0o000);
        fs::set_permissions(&file_path, perms).unwrap();

        let result = check_timeout_wrappers(&dir);

        // Restore permissions so cleanup works.
        let mut perms_restore = fs::metadata(&file_path).unwrap().permissions();
        perms_restore.set_mode(0o644);
        let _ = fs::set_permissions(&file_path, perms_restore);

        assert_eq!(result.status, CheckStatus::Error, "{}", result.message);
        assert!(
            result.message.contains("read error"),
            "message must mention read error: {}",
            result.message
        );
        assert!(
            !result.message.contains("missing timeout wrapper"),
            "read errors must not be counted as missing wrappers: {}",
            result.message
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_check_tailwind4_removed_angular_classes_warns_on_tailwind3_only_class() {
        let dir = make_temp_dir("tailwind4-removed-class");
        write_file(
            &dir,
            "ralph-gui/ui/src/app/components/example/example.component.html",
            r#"<div class="flex items-center flex-shrink-0">Example</div>"#,
        );

        let result = check_tailwind4_removed_angular_classes(&dir);

        assert_eq!(result.status, CheckStatus::Warning, "{}", result.message);
        assert!(
            result.message.contains("flex-shrink-0"),
            "message must mention the removed Tailwind 3 class: {}",
            result.message
        );
        assert!(
            result.message.contains("shrink-0"),
            "message must mention the Tailwind 4 replacement: {}",
            result.message
        );
        assert!(
            result.message.contains("needs rework"),
            "message must tell the user the component/file needs rework: {}",
            result.message
        );
        assert!(
            result
                .message
                .contains("Tailwind CSS v4 documentation and upgrade guide"),
            "message must direct the user to current Tailwind v4 docs: {}",
            result.message
        );

        let _ = fs::remove_dir_all(&dir);
    }
}
