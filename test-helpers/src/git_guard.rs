//! Git mutation guard for isolated test repositories.
//!
//! `GitGuard` creates an isolated temporary git repository for use in tests,
//! ensuring that no test accidentally operates on the project's real git repository.

use std::path::PathBuf;
use tempfile::TempDir;

/// A test-only guard that creates an isolated temporary git repository.
///
/// `GitGuard` wraps a [`tempfile::TempDir`] and initializes a clean git repository
/// inside it. The temporary directory and all its contents are automatically removed
/// when the guard is dropped.
///
/// # Policy
///
/// Tests MUST use `GitGuard` (or equivalent isolated git infrastructure) for any
/// operation involving a git repository. Violating this policy causes an immediate
/// test failure with a clear error. Use [`crate::assert_not_project_repo`] or
/// [`crate::assert_git_mutation_allowed`] to validate a repository is isolated before
/// mutating it.
///
/// # Example
///
/// ```rust,ignore
/// # use test_helpers::GitGuard;
/// let guard = GitGuard::new();
/// // All git operations are performed on guard.path, not the project repo.
/// let repo = git2::Repository::open(&guard.path).unwrap();
/// ```
pub struct GitGuard {
    /// Keeps the temporary directory alive until the guard is dropped.
    _dir: TempDir,
    /// Path to the isolated git repository root.
    pub path: PathBuf,
}

impl GitGuard {
    /// Create a new isolated temporary git repository for use in tests.
    ///
    /// Initializes a fresh git repository in a system temporary directory.
    /// The repository is completely independent of the project repository.
    ///
    /// # Panics
    ///
    /// Panics if the temporary directory cannot be created, or if git repository
    /// initialization fails.
    pub fn new() -> Self {
        let dir = TempDir::new().expect("GitGuard: failed to create temp dir");
        let path = dir.path().to_path_buf();
        git2::Repository::init(&path).expect("GitGuard: failed to initialize temp git repo");
        Self { _dir: dir, path }
    }
}

impl GitGuard {
    /// Verify that this guard's repository is properly isolated from the real project repository.
    ///
    /// This is an explicit policy assertion that tests can call to document and verify
    /// that their git operations stay within the isolated temporary repository scope.
    ///
    /// # Note on GIT_DIR / GIT_WORK_TREE
    ///
    /// Setting `GIT_DIR`/`GIT_WORK_TREE` as process-global environment variables is
    /// intentionally omitted. These variables are process-wide and would create races
    /// in the parallel test runner. Isolation is achieved structurally: `GitGuard` always
    /// creates repositories inside `std::env::temp_dir()`, which is outside any real project
    /// repository. Tests must explicitly use `guard.path` for all git operations rather than
    /// relying on the current working directory or ambient env-var state.
    ///
    /// For tests that need to assert the current working directory is not inside the
    /// project repository, call [`crate::assert_not_project_repo`] with
    /// `std::env::current_dir().unwrap()` explicitly. Centralising the check in `GitGuard`
    /// itself is not done because `cargo test` always sets the cwd to the package directory,
    /// which IS inside the project repository — so a blanket cwd check here would be a
    /// false positive for every test.
    ///
    /// # Panics
    ///
    /// Panics with a clear policy violation message if the guard's path is inside the
    /// project's real git repository. This should never happen in normal usage since
    /// `GitGuard::new()` always creates repositories under the system temp directory.
    pub fn policy_check(&self) {
        crate::assert_not_project_repo(&self.path);
    }
}

impl Default for GitGuard {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn git_guard_creates_isolated_repo() {
        let guard = GitGuard::new();

        // Path exists
        assert!(guard.path.exists(), "GitGuard path must exist");

        // .git directory was created by init
        assert!(
            guard.path.join(".git").exists(),
            "GitGuard path must contain a .git directory after init"
        );

        // Path is inside the system temp directory (not the project repo)
        let temp =
            std::fs::canonicalize(std::env::temp_dir()).unwrap_or_else(|_| std::env::temp_dir());
        let guard_path = std::fs::canonicalize(&guard.path).unwrap_or_else(|_| guard.path.clone());
        assert!(
            guard_path.starts_with(&temp),
            "GitGuard path must be inside system temp dir; got: {}",
            guard.path.display()
        );
    }

    #[test]
    fn git_guard_repo_is_openable() {
        let guard = GitGuard::new();
        // Verify the initialized repo can be opened with git2
        let repo =
            git2::Repository::open(&guard.path).expect("GitGuard repo must be openable with git2");
        // Confirm it is not bare (has a working directory)
        assert!(
            !repo.is_bare(),
            "GitGuard repo must not be a bare repository"
        );
    }

    #[test]
    fn git_guard_default_matches_new() {
        // Default::default() must produce an equivalent guard to ::new()
        let guard = GitGuard::default();
        assert!(guard.path.join(".git").exists());
    }

    #[test]
    fn git_guard_policy_check_passes_for_isolated_repo() {
        // policy_check() must not panic for a standard GitGuard (which is always in temp dir).
        let guard = GitGuard::new();
        // This must not panic — the guard lives in system temp, not the project repo.
        guard.policy_check();
    }

    #[test]
    #[should_panic(expected = "POLICY VIOLATION")]
    fn assert_not_project_repo_panics_for_real_project_path() {
        // Negative policy test: confirms the guard mechanism fires immediately with the
        // mandated policy message when a path inside the real project repository is checked.
        //
        // CARGO_MANIFEST_DIR is the test-helpers package directory, which is always inside
        // the project repository, so this call MUST panic.
        let project_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        crate::assert_not_project_repo(&project_dir);
    }
}
