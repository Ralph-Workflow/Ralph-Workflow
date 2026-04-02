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
/// Call this at the start of any test that creates or modifies files.
/// Policy: no test may touch real git state. See docs/agents/testing-guide.md.
///
/// # Algorithm
///
/// This function walks up the directory tree from `path` to the filesystem root,
/// checking each directory for a `.git` subdirectory. If found, it panics because
/// the path is inside a real git repository.
///
/// Unlike `assert_not_project_repo` which only checks if the immediate path equals
/// the project root, this function detects any git repository anywhere in the
/// ancestor chain. This catches nested worktrees, git submodules, and any other
/// git repository that happens to be a parent of the test path.
///
/// Unlike the previous implementation, this function does NOT return early when
/// canonicalization fails. It uses ancestor walking to detect .git directories
/// even for paths that don't exist on disk yet (e.g., temp directories that
/// will be created by tests). If canonicalization fails at the starting path,
/// it attempts to canonicalize parent directories until it finds a valid path
/// or reaches the root.
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if a `.git` directory is found in any
/// ancestor of `path`.
#[track_caller]
pub fn assert_not_real_git_repo(path: &std::path::Path) {
    // Walk up the directory tree checking for .git.
    // Use ancestor walking to detect .git even for non-existent paths.
    let mut current = path.to_path_buf();

    loop {
        if current.join(".git").exists() {
            panic!(
                "POLICY VIOLATION: test path '{}' is inside a real git repository at '{}'. \
                 Tests must use MemoryWorkspace or isolated temp directories outside any repo. \
                 See docs/agents/testing-guide.md.",
                path.display(),
                current.display()
            );
        }

        // Try to canonicalize current to get its parent for the next iteration.
        // If canonicalization fails, use parent() as fallback.
        let next = std::fs::canonicalize(&current)
            .ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .or_else(|| current.parent().map(|p| p.to_path_buf()));

        match next {
            Some(parent) if parent != current => {
                current = parent;
            }
            _ => break,
        }
    }
}
