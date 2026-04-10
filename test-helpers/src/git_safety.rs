//! Hard safety guardrails preventing tests from touching real git state.
//!
//! # Policy
//!
//! No test is permitted to mutate real git state (commits, branches, tags,
//! index writes, resets). This module provides helpers that panic immediately
//! with a clear policy message if real git mutation is attempted.
//!
//! This is enforced unconditionally — there is no environment variable or
//! feature flag to bypass this requirement.

/// Panics immediately with a clear policy violation message.
///
/// Call this at the entry point of any function that would perform real git
/// mutation if reached from a test context.
///
/// # Panics
///
/// Always panics with a policy violation message.
#[track_caller]
pub fn no_real_git_mutation(operation: &str) {
    panic!(
        "GIT MUTATION POLICY VIOLATION: test attempted real git operation '{}'. \
         Tests must use in-memory fakes (MockAppEffectHandler, MockWorkspace) \
         instead of real git state. See test-helpers/src/git_safety.rs for policy.",
        operation
    );
}

/// Fail-fast guardrail: panic immediately with a policy error if `path` is
/// inside any real git repository (checked by walking ancestors for .git).
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if a `.git` directory is found in any
/// ancestor of `path`.
#[track_caller]
pub fn assert_not_real_git_repo(path: &std::path::Path) {
    crate::boundary::assert_not_real_git_repo_impl(path);
}

/// Fail-fast guardrail: panic if a workspace root value is `None`.
///
/// This catches the class of bugs where `RealAppEffectHandler` (or any handler)
/// is constructed without a workspace root, which later causes "workspace root
/// is not set" errors deep in git or file operations.
///
/// Call this at test entry points that create effect handlers to verify the
/// workspace root is properly initialized before any operations are attempted.
///
/// # Panics
///
/// Panics with a clear policy message if `workspace_root` is `None`.
#[track_caller]
pub fn assert_effect_handler_has_workspace_root(workspace_root: Option<&std::path::Path>) {
    if workspace_root.is_none() {
        panic!(
            "WORKSPACE ROOT POLICY VIOLATION: effect handler was constructed without a \
             workspace root. All effect handlers used in tests must have a workspace root \
             set — either via with_workspace_root(path) or via new() (which defaults to \
             cwd). A missing workspace root causes 'workspace root is not set' errors \
             in git and file operations. See test-helpers/src/git_safety.rs for policy.",
        );
    }
}

/// Fail-fast guardrail: panic immediately with a policy error if `path` is
/// inside the project git repository.
///
/// Unlike `assert_not_real_git_repo` which panics if `path` is inside ANY git
/// repository, this function only panics if `path` is inside THE PROJECT
/// repository specifically.
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if the project git directory is found
/// in the ancestor chain of `path`.
#[track_caller]
pub fn assert_in_isolated_temp_repo(path: &std::path::Path) {
    crate::boundary::assert_in_isolated_temp_repo_impl(path);
}

#[cfg(test)]
mod tests {
    /// Sentinel test: scan integration/system test source files for patterns that
    /// indicate real (non-isolated) git commit operations.
    ///
    /// This catches tests that accidentally call `git commit` via `Command::new`
    /// outside of the guarded `test-helpers/src/boundary` module, which would
    /// mutate real repository state.
    #[test]
    fn sentinel_no_unguarded_git_commit_in_tests() {
        use std::path::Path;

        let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
        let repo_root = manifest_dir
            .parent()
            .expect("test-helpers must be inside repo");

        let test_dirs = [
            repo_root.join("tests").join("integration_tests"),
            repo_root.join("tests").join("integration_tests_agent_core"),
            repo_root.join("tests").join("integration_tests_reducer"),
            repo_root.join("tests").join("integration_tests_workflow"),
            // process_system_tests and deduplication_integration_tests are also
            // scanned to catch raw git-commit calls that would mutate real state.
            // Note: tests/system_tests/git/ is intentionally excluded here — that
            // directory tests git hook enforcement and necessarily exercises commit
            // operations in isolated temp repos (guarded by assert_in_isolated_temp_repo).
            repo_root.join("tests").join("process_system_tests"),
            repo_root
                .join("tests")
                .join("deduplication_integration_tests"),
            // Also scan inline #[cfg(test)] blocks in the ralph-workflow and
            // mcp-server crates.  Production code in these crates uses the git2
            // library (never Command::new("git")), so the patterns below only
            // fire on shell-invocation patterns that would bypass safety helpers.
            repo_root.join("ralph-workflow").join("src"),
            repo_root.join("mcp-server").join("src"),
            // External test files in the mcp-server crate (integration/standalone tests).
            repo_root.join("mcp-server").join("tests"),
        ];

        // Forbidden patterns in test source: raw git invocations that bypass the
        // guarded helpers in test-helpers/src/boundary.
        //
        // Patterns cover the most common shell-dispatch forms:
        //   commit  – create a new commit (highest risk: mutates project history)
        //   push    – push commits to a remote (data loss risk, affects shared state)
        //   reset   – potentially destroy uncommitted work or move HEAD
        //   add     – stage files (prerequisite for unauthorized commits)
        //   checkout – switch branches (could discard local changes)
        //   branch  – create/delete branches (affects repo structure)
        //   merge   – merge branches (could cause conflicts)
        //   clean   – remove untracked files (irreversible data loss)
        //   stash   – stash changes (hides work, could be lost)
        //
        // Only Command-dispatch patterns are listed here.  git2 library calls
        // (e.g., repo.commit()) are permitted in production code paths and in
        // test-helpers/src/boundary (which itself enforces mutation guards).
        let forbidden_patterns: &[&str] = &[
            // commit
            ".arg(\"commit\")",
            ".args([\"commit\"",
            ".args(&[\"commit\"",
            // push
            ".arg(\"push\")",
            ".args([\"push\"",
            ".args(&[\"push\"",
            // reset
            ".arg(\"reset\")",
            ".args([\"reset\"",
            ".args(&[\"reset\"",
            // add
            ".arg(\"add\")",
            ".args([\"add\"",
            ".args(&[\"add\"",
            // checkout
            ".arg(\"checkout\")",
            ".args([\"checkout\"",
            ".args(&[\"checkout\"",
            // branch
            ".arg(\"branch\")",
            ".args([\"branch\"",
            ".args(&[\"branch\"",
            // merge
            ".arg(\"merge\")",
            ".args([\"merge\"",
            ".args(&[\"merge\"",
            // clean
            ".arg(\"clean\")",
            ".args([\"clean\"",
            ".args(&[\"clean\"",
            // stash
            ".arg(\"stash\")",
            ".args([\"stash\"",
            ".args(&[\"stash\"",
        ];

        let mut violations = Vec::new();

        for dir in &test_dirs {
            if !dir.exists() {
                continue;
            }
            scan_dir_for_forbidden_patterns(dir, forbidden_patterns, &mut violations);
        }

        assert!(
            violations.is_empty(),
            "GIT SAFETY SENTINEL VIOLATION: found unguarded git commit patterns in test code:\n{}",
            violations.join("\n")
        );
    }

    fn scan_dir_for_forbidden_patterns(
        dir: &std::path::Path,
        patterns: &[&str],
        violations: &mut Vec<String>,
    ) {
        let entries = match std::fs::read_dir(dir) {
            Ok(e) => e,
            Err(_) => return,
        };

        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                scan_dir_for_forbidden_patterns(&path, patterns, violations);
            } else if path.extension().is_some_and(|ext| ext == "rs") {
                let content = match std::fs::read_to_string(&path) {
                    Ok(c) => c,
                    Err(_) => continue,
                };
                for pattern in patterns {
                    if content.contains(pattern) {
                        violations.push(format!("  {} contains '{}'", path.display(), pattern));
                    }
                }
            }
        }
    }
}
