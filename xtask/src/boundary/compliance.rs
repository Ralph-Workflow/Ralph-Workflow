use std::collections::HashSet;
use std::path::{Path, PathBuf};

use aho_corasick::AhoCorasick;

use crate::domain::compliance::{
    extract_test_name, find_fn_line_idx, find_function_end_bytes, find_opening_brace_in_lines,
    shell_script_scan_result, tailwind_scan_result, timeout_wrapper_scan_result, trim_ascii,
    ComplianceSummary,
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
///
/// `scripts/remote/` is excluded from the scan because it contains intentional
/// thin SSH wrappers for the remote build server workflow.
pub fn check_no_shell_scripts(repo_root: &Path) -> NativeCheckResult {
    let scan_dirs = ["scripts", "tests/integration_tests"];
    let scan_paths: Vec<PathBuf> = scan_dirs.iter().map(|rel| repo_root.join(rel)).collect();
    // scripts/remote/ contains intentional SSH thin wrappers; exclude from the
    // migration-regression scan.
    let excluded = vec![repo_root.join("scripts/remote")];
    let (found, walk_errors) =
        crate::io::shell_scripts::scan_for_shell_scripts(&scan_paths, &excluded);

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
/// Returns `Pass` when no violations are found or when the test directory does not exist.
pub fn check_no_real_git_in_tests(repo_root: &Path) -> NativeCheckResult {
    let files = match collect_git_scan_files(repo_root) {
        Ok(f) => f,
        Err(r) => return r,
    };
    if files.is_empty() {
        return pass_native();
    }
    let ac = AhoCorasick::new(GIT_VIOLATION_PATTERNS).expect("valid git violation patterns");
    let (violations, read_errors) = scan_files_for_git_violations(&files, &ac);
    build_git_scan_result(&violations, &read_errors)
}

// Patterns that indicate real git usage or risky CWD workspace construction in tests.
const GIT_VIOLATION_PATTERNS: &[&str] = &[
    "Repository::commit",
    ".commit(",
    "CommitEffect",
    "WorkspaceFs::new(std::env::current_dir",
    "WorkspaceFs::new(PathBuf::from(\".\")",
];

fn collect_git_scan_files(repo_root: &Path) -> Result<Vec<PathBuf>, NativeCheckResult> {
    let test_dirs = [
        repo_root.join("tests/integration_tests"),
        repo_root.join("tests/process_system_tests"),
        repo_root.join("ralph-workflow/src/mcp_server/tests"),
        repo_root.join("mcp-server/tests"),
    ];
    let mut files = Vec::new();
    for test_dir in &test_dirs {
        collect_from_dir_if_exists(test_dir, &mut files)?;
    }
    Ok(files)
}

fn collect_from_dir_if_exists(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), NativeCheckResult> {
    if dir.exists() {
        collect_rs_files_from(dir, out).map_err(|e| {
            error_native(format!(
                "Failed to walk test directory {}: {e}",
                dir.display()
            ))
        })
    } else {
        Ok(())
    }
}

fn collect_rs_files_from(dir: &Path, out: &mut Vec<PathBuf>) -> std::io::Result<()> {
    let mut all_files = Vec::new();
    crate::io::scanner::collect_files_with_glob(dir, "*.rs", &mut all_files)?;
    out.extend(
        all_files
            .into_iter()
            .filter(|p| !should_exclude_from_git_scan(p)),
    );
    Ok(())
}

fn should_exclude_from_git_scan(path: &Path) -> bool {
    path.components().any(|c| c.as_os_str() == "system_tests") || should_skip_file(path)
}

fn scan_files_for_git_violations(
    files: &[PathBuf],
    ac: &AhoCorasick,
) -> (Vec<String>, Vec<String>) {
    let mut violations = Vec::new();
    let mut read_errors = Vec::new();
    for file_path in files {
        match std::fs::read(file_path) {
            Ok(content) => scan_file_for_git_violations(file_path, &content, ac, &mut violations),
            Err(e) => read_errors.push(format!("{}: read error: {e}", file_path.display())),
        }
    }
    (violations, read_errors)
}

fn build_git_scan_result(violations: &[String], read_errors: &[String]) -> NativeCheckResult {
    if !read_errors.is_empty() {
        return error_native(format!(
            "Failed to read {} test file(s) during git-violation scan:\n{}",
            read_errors.len(),
            read_errors.join("\n")
        ));
    }
    if violations.is_empty() {
        pass_native()
    } else {
        error_native(format!(
            "Found {} test file(s) with real git operations or risky workspace construction \
             (real git mutations and CWD-rooted workspaces are forbidden in tests — use \
             MemoryWorkspace or TempDir-backed WorkspaceFs):\n{}",
            violations.len(),
            violations.join("\n")
        ))
    }
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
        PAT_TEST_ATTR => classify_test_attr_match(mat, content, line_idx),
        PAT_DEFAULT_TIMEOUT | PAT_TIMEOUT => Some(MatchOffset::Timeout(mat.start())),
        _ => None,
    }
}

fn classify_test_attr_match(
    mat: aho_corasick::Match,
    content: &[u8],
    line_idx: &LineIndex,
) -> Option<MatchOffset> {
    let line_bytes = line_idx.extract_line(content, mat.start());
    if trim_ascii(line_bytes) == b"#[test]" {
        Some(MatchOffset::Test(mat.start()))
    } else {
        None
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
    let fn_line = find_fn_line_idx(lines, test_line + 1).filter(|&idx| idx < n)?;
    let fn_line_str = std::str::from_utf8(lines[fn_line]).ok()?;
    let test_name = extract_test_name(fn_line_str)
        .unwrap_or("<unknown>")
        .to_string();
    let brace_line = find_opening_brace_in_lines(lines, fn_line, 5)?;
    let brace_line_byte_start = line_idx.start_of_line(brace_line);
    let fn_end_byte = compute_fn_end_byte(content, line_idx, n, brace_line, brace_line_byte_start);
    Some(TestContext {
        test_line,
        brace_line_byte_start,
        fn_end_byte,
        test_name,
    })
}

fn compute_fn_end_byte(
    content: &[u8],
    line_idx: &LineIndex,
    n: usize,
    brace_line: usize,
    brace_line_byte_start: usize,
) -> usize {
    let cap_line = std::cmp::min(brace_line + 40, n);
    let body_scan_end = if cap_line >= n {
        content.len()
    } else {
        line_idx.start_of_line(cap_line)
    };
    find_function_end_bytes(content, brace_line_byte_start, body_scan_end)
}

fn timeout_inside(brace: usize, end: usize, timeout_offsets: &[usize]) -> bool {
    timeout_offsets.iter().any(|&pos| pos >= brace && pos < end)
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

fn pass_native() -> NativeCheckResult {
    NativeCheckResult {
        status: CheckStatus::Pass,
        message: String::new(),
    }
}

fn error_native(message: String) -> NativeCheckResult {
    NativeCheckResult {
        status: CheckStatus::Error,
        message,
    }
}

/// Verifies that `mcp-server` has no dependency on `ralph-workflow` (direct or
/// transitive). This enforces the standalone principle: mcp-server must be usable
/// without ralph-workflow in the dependency graph.
///
/// Uses `cargo metadata --format-version 1` to get the full dependency graph and
/// traverses all dependencies of mcp-server to verify ralph-workflow is not present.
pub fn check_mcp_server_dep_isolation(repo_root: &Path) -> NativeCheckResult {
    if !repo_root.exists() {
        return pass_native();
    }
    match run_cargo_metadata_for_mcp(repo_root) {
        Ok(Some(metadata)) => check_dep_isolation_in_metadata(&metadata),
        Ok(None) => pass_native(),
        Err(r) => r,
    }
}

fn run_cargo_metadata_for_mcp(repo_root: &Path) -> Result<Option<String>, NativeCheckResult> {
    let output = match std::process::Command::new("cargo")
        .args([
            "metadata",
            "--format-version",
            "1",
            "--manifest-path",
            "mcp-server/Cargo.toml",
        ])
        .current_dir(repo_root)
        .output()
    {
        Ok(o) => o,
        Err(_) => return Ok(None),
    };
    if !output.status.success() {
        return handle_cargo_metadata_failure(&String::from_utf8_lossy(&output.stderr));
    }
    Ok(Some(String::from_utf8_lossy(&output.stdout).into_owned()))
}

fn handle_cargo_metadata_failure(stderr: &str) -> Result<Option<String>, NativeCheckResult> {
    if stderr.contains("package not found")
        || stderr.contains("`mcp-server` is not found")
        || stderr.contains("could not find `Cargo.toml`")
        || stderr.contains("does not exist")
    {
        return Ok(None);
    }
    Err(error_native(format!(
        "DEPENDENCY ISOLATION VIOLATION: cargo metadata failed: {stderr}"
    )))
}

fn check_dep_isolation_in_metadata(metadata: &str) -> NativeCheckResult {
    compliance_summary_to_native(
        crate::domain::compliance::check_mcp_dep_isolation_from_metadata(metadata),
    )
}

/// Verifies that `ralph_submit_artifact` is the only MCP tool name retaining the `ralph_`
/// prefix in production source code.
///
/// All other tool names must have dropped the prefix (per commit d1f09f19 "rename all tools
/// to drop ralph_ prefix"). This check ensures no future tool registration accidentally
/// reintroduces the `ralph_` prefix on a tool other than the artifact submission tool.
///
/// Scanned directories (test directories are excluded):
/// - `mcp-server/src/`
/// - `ralph-workflow/src/mcp_server/` (excluding `tests/` subdirectory)
///
/// Returns `Pass` when only `ralph_submit_artifact` (or no `ralph_`-prefixed strings at all)
/// appear in production source. Returns `Error` when any other `ralph_`-prefixed tool name
/// is found.
pub fn check_mcp_tool_naming_policy(repo_root: &Path) -> NativeCheckResult {
    if !repo_root.exists() {
        return pass_native();
    }
    let dirs = [
        repo_root.join("mcp-server/src"),
        repo_root.join("ralph-workflow/src/mcp_server"),
    ];
    match collect_ralph_prefix_violations(&dirs, repo_root) {
        Ok(violations) => compliance_summary_to_native(
            crate::domain::compliance::ralph_prefix_violations_summary(&violations),
        ),
        Err(e) => error_native(e),
    }
}

fn collect_ralph_prefix_violations(
    dirs: &[PathBuf],
    repo_root: &Path,
) -> Result<Vec<String>, String> {
    let mut violations = Vec::new();
    for dir in dirs {
        if !dir.exists() {
            continue;
        }
        collect_ralph_violations_from_dir(dir, repo_root, &mut violations)
            .map_err(|e| format!("Failed to scan {}: {e}", dir.display()))?;
    }
    Ok(violations)
}

fn collect_ralph_violations_from_dir(
    dir: &Path,
    repo_root: &Path,
    violations: &mut Vec<String>,
) -> std::io::Result<()> {
    let mut rs_files = Vec::new();
    crate::io::scanner::collect_files_with_glob(dir, "*.rs", &mut rs_files)?;
    for file_path in rs_files.iter().filter(|p| !is_in_test_subdir(p)) {
        let content = std::fs::read_to_string(file_path)?;
        let display = file_path
            .strip_prefix(repo_root)
            .unwrap_or(file_path.as_path());
        violations.extend(
            crate::domain::compliance::scan_content_for_ralph_violations(&content, display),
        );
    }
    Ok(())
}

fn is_in_test_subdir(path: &Path) -> bool {
    path.components()
        .any(|c| c.as_os_str() == "tests" || c.as_os_str() == "test")
}

/// Verifies that `mcp-server/tests/standalone_host.rs` enforces git safety using either:
///
/// 1. **Shared helper (preferred)**: calls `test_helpers::assert_not_in_git_repo`
/// 2. **Local function (legacy)**: defines `fn assert_no_real_git_state` with `loop {`
///
/// Returns `Pass` when either requirement is met or when the file does not exist.
pub fn check_standalone_host_git_safety_parity(repo_root: &Path) -> NativeCheckResult {
    let standalone_host = repo_root.join("mcp-server/tests/standalone_host.rs");
    if !standalone_host.exists() {
        return pass_native();
    }
    let content = match std::fs::read_to_string(&standalone_host) {
        Ok(c) => c,
        Err(e) => {
            return error_native(format!("Failed to read {}: {e}", standalone_host.display()))
        }
    };
    compliance_summary_to_native(
        crate::domain::compliance::standalone_host_git_safety_summary(
            content.contains("test_helpers::assert_not_in_git_repo"),
            content.contains("fn assert_no_real_git_state"),
            content.contains("loop {"),
        ),
    )
}

#[path = "compliance_tests.rs"]
#[cfg(test)]
mod tests;
