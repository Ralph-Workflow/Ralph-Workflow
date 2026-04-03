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

/// Fail-fast guardrail: panic immediately with a policy error if `path` is
/// inside the project git repository.
///
/// Call this at the start of any test that uses `git2::Repository::init` or
/// creates commits to verify the operation happens in an isolated temp directory,
/// not inside the project repository.
///
/// Unlike `assert_not_real_git_repo` which panics if `path` is inside ANY git
/// repository, this function only panics if `path` is inside THE PROJECT
/// repository specifically. This allows tests to create isolated git repos
/// in other locations (e.g., `/tmp/foo`) while still catching accidental
/// operations inside the project.
///
/// # Algorithm
///
/// 1. Find the project git directory by walking up from the current working
///    directory until a `.git` directory is found.
/// 2. Walk up the directory tree from `path`, checking each ancestor for a
///    `.git` directory.
/// 3. If any ancestor's `.git` canonicalizes to the same path as the project
///    git directory, panic with a policy violation.
///
/// This means:
/// - `/tmp/foo` with no `.git` ancestors → PASS (isolated)
/// - `/tmp/foo/.git` (separate repo) → PASS (different repo)
/// - `/project/src/temp` where `/project/.git` exists → FAIL (inside project)
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if the project git directory is found
/// in the ancestor chain of `path`.
#[track_caller]
pub fn assert_in_isolated_temp_repo(path: &std::path::Path) {
    // Find the project git directory by walking up from cwd
    let project_git_dir = match find_project_git_dir() {
        Some(p) => p,
        None => return, // No project git dir found, can't violate
    };

    // Walk up from path checking for project git dir
    let mut current = path.to_path_buf();

    loop {
        let git_dir = current.join(".git");
        if git_dir.exists() {
            // Check if this .git is the project .git
            if let Ok(canonical) = git_dir.canonicalize() {
                if canonical == project_git_dir {
                    panic!(
                        "POLICY VIOLATION: test path '{}' is inside the project git repository at '{}'. \
                         Tests must use isolated temp directories outside the project repo. \
                         See docs/agents/testing-guide.md.",
                        path.display(),
                        project_git_dir.display()
                    );
                }
            }
        }

        // Try to canonicalize current to get its parent for the next iteration
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

/// Find the project git directory by walking up from the current working directory.
///
/// Returns the canonical path to the project's `.git` directory, or None if not found.
fn find_project_git_dir() -> Option<std::path::PathBuf> {
    let mut current = std::env::current_dir().ok()?;

    loop {
        let git_dir = current.join(".git");
        if git_dir.exists() {
            return git_dir.canonicalize().ok();
        }

        if !current.pop() {
            return None;
        }
    }
}
