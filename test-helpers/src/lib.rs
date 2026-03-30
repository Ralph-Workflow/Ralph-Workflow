// Lint policy: `test-helpers` is a boundary crate for higher-level tests, not an
// exemption from the style guide.
//
// See `CODE_STYLE.md`, `docs/code-style/testing.md`,
// `docs/code-style/boundaries.md`, and `test-helpers/clippy.toml`.
//
// `clippy::cargo` stays off because it reports dependency conflicts outside the
// code-shape problems this crate can actually fix.
#![deny(warnings)]
#![deny(clippy::all)]
#![forbid(unsafe_code)]
#![deny(
    // No explicit iterator loops when a more idiomatic form exists
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    // No implicit crashes / partial operations
    // This crate intentionally keeps a narrow libgit2/test-fixture exception for
    // panic-oriented setup helpers. Ordinary helper code should still prefer explicit
    // values and boundary-local effects.
    clippy::panic_in_result_fn,
    clippy::indexing_slicing,
    // No casual side effects / debugging leftovers
    clippy::print_stdout,
    clippy::print_stderr,
    clippy::dbg_macro,
    // Treat unchecked arithmetic as suspicious
    clippy::arithmetic_side_effects,
    // Push toward combinators instead of hand-written control flow
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    clippy::needless_collect
)]

use git2::build::CheckoutBuilder;
use git2::{IndexAddOption, Oid, Repository, Signature, Status, StatusOptions};
use std::fs;
use std::path::Path;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::OnceLock;
use tempfile::TempDir;

static PROJECT_REPO_ROOT: OnceLock<Option<PathBuf>> = OnceLock::new();

fn project_repo_root() -> Option<&'static Path> {
    PROJECT_REPO_ROOT
        .get_or_init(|| {
            let exe = std::env::current_exe().ok()?;
            let start = exe.parent()?.to_path_buf();
            std::iter::successors(Some(start), |p| {
                p.parent().map(|parent| parent.to_path_buf())
            })
            .find(|p| p.join(".git").exists())
        })
        .as_deref()
}

/// Enforce that no test operates on the project's real git repository.
///
/// # Panics
///
/// Panics with a policy-violation message if `repo_root` is inside or equal to
/// the project's own repository root. All test git operations must use isolated temporary
/// repositories created with `TempDir::new()`.
pub fn assert_not_project_repo(repo_root: &Path) {
    let Some(project) = project_repo_root() else {
        return;
    };
    let repo_abs = std::fs::canonicalize(repo_root).unwrap_or_else(|_| repo_root.to_path_buf());
    let project_abs = std::fs::canonicalize(project).unwrap_or_else(|_| project.to_path_buf());
    let is_project_repo = repo_abs == project_abs || repo_abs.starts_with(&project_abs);

    if is_project_repo {
        panic!(
            "POLICY VIOLATION: test attempted to operate on the project's real git \
             repository at '{}'. Tests must use isolated temporary repositories created \
             with TempDir::new(). This check exists because previous test runs modified \
             the real repository, corrupted git hooks, and reverted developer changes. \
             Use init_git_repo(&TempDir::new().unwrap()) to get an isolated repo. \
             If TMPDIR is set to a subdirectory of the project, check your environment.",
            repo_abs.display()
        );
    }
}

/// Enforce that a `Repository` is not the project's real git repository.
///
/// # Panics
///
/// See [`assert_not_project_repo`].
pub fn assert_repo_is_isolated(repo: &Repository) {
    if let Some(workdir) = repo.workdir() {
        assert_not_project_repo(workdir);
    }
}

/// Enforce that a `Repository` is inside a temporary directory.
///
/// This prevents tests from accidentally creating repos in the project directory
/// even if TMPDIR is set to a subdirectory of the project.
///
/// # Panics
///
/// Panics with a policy-violation message if `repo.workdir()` is not inside
/// `std::env::temp_dir()`. Uses `canonicalize()` on both paths to resolve
/// symlinks (important on macOS where `/var/folders` symlinks to `/private/var/folders`).
pub fn assert_repo_is_temp_isolated(repo: &Repository) {
    let Some(workdir) = repo.workdir() else {
        return; // Bare repo - skip check
    };

    let workdir_abs = std::fs::canonicalize(workdir).unwrap_or_else(|_| workdir.to_path_buf());
    let temp_dir_abs =
        std::fs::canonicalize(std::env::temp_dir()).unwrap_or_else(|_| std::env::temp_dir());

    if !workdir_abs.starts_with(&temp_dir_abs) {
        panic!(
            "POLICY VIOLATION: repository workdir '{}' is not inside temp directory '{}'. \
             All test repositories must be created with TempDir::new() and live under the \
             system's temp directory. This prevents tests from modifying the project repo \
             even if TMPDIR is misconfigured. \
             Workdir canonicalized: '{}', TempDir canonicalized: '{}'",
            workdir.display(),
            temp_dir_abs.display(),
            workdir_abs.display(),
            temp_dir_abs.display()
        );
    }
}

/// Capture the HEAD OID of the project repository.
///
/// Returns `None` if the project root cannot be determined or if reading fails.
/// Uses git2 directly (no subprocess) for fast, reliable reading.
pub fn capture_project_head_oid() -> Option<String> {
    let project = project_repo_root()?;
    let repo = Repository::open(project).ok()?;
    let head = repo.head().ok()?;
    let oid = head.target()?;
    Some(oid.to_string())
}

/// Assert that the project repository's HEAD has not changed since `before` was captured.
///
/// This guards against tests creating commits in the real project repository.
/// Used before and after spawning Ralph processes to ensure no commits were injected.
///
/// # Panics
///
/// Panics with a clear POLICY VIOLATION message if HEAD differs from `before`.
pub fn assert_project_head_unchanged(before: &Option<String>) {
    let after = match capture_project_head_oid() {
        Some(oid) => oid,
        None => return, // Cannot read HEAD — skip check gracefully
    };
    if before.as_ref() != Some(&after) {
        panic!(
            "POLICY VIOLATION: a test created a commit in the project repository. \
             HEAD moved from {} to {}. \
             Tests must never modify the real project repo.",
            before.as_ref().map(|s| s.as_str()).unwrap_or("(none)"),
            after
        );
    }
}

/// Policy: assert that a repository is allowed to receive git mutations.
///
/// This function is called by `commit_all()` and `git_commit_all()` before performing
/// any mutation. It verifies the repository is isolated from the project repo.
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if the repository is the project repository
/// or if it is not in a temporary directory.
pub fn assert_git_mutation_allowed(repo: &Repository) {
    assert_repo_is_isolated(repo);
    assert_repo_is_temp_isolated(repo);
}

/// RAII guard that detects real git mutations in tests.
///
/// On construction, captures the HEAD OID of the project repository.
/// On drop, asserts the HEAD has not changed.
///
/// This provides fail-fast detection of any test that creates commits
/// in the real project repository without requiring per-test assertions.
///
/// # Example
///
/// ```ignore
/// #[test]
/// fn my_test() {
///     let _guard = GitMutationGuard::new();
///     // Any commit_all() or git_commit_all() that targets the real project repo
///     // will now panic when the guard drops.
/// }
/// ```
///
/// # Panics
///
/// Panics on drop if HEAD changed between construction and destruction,
/// indicating a test created a commit in the real project repository.
pub struct GitMutationGuard {
    before_head: Option<String>,
}

impl GitMutationGuard {
    /// Create a new guard, capturing the current project HEAD.
    ///
    /// If the project root cannot be determined, the guard becomes a no-op
    /// (returns None) since we cannot verify isolation anyway.
    #[must_use]
    pub fn new() -> Option<Self> {
        Some(Self {
            before_head: capture_project_head_oid(),
        })
    }
}

impl Default for GitMutationGuard {
    fn default() -> Self {
        Self::new().unwrap_or(Self { before_head: None })
    }
}

impl Drop for GitMutationGuard {
    fn drop(&mut self) {
        assert_project_head_unchanged(&self.before_head);
    }
}

/// Create an isolated config file in the test directory.
/// This prevents user config from interfering with tests.
///
/// # Panics
///
/// - If directory creation fails
/// - If config file write fails
#[must_use]
pub fn create_isolated_config(dir: &Path) -> std::path::PathBuf {
    let config_home = dir.join(".config");
    fs::create_dir_all(&config_home).expect("create config home");
    fs::write(
        config_home.join("ralph-workflow.toml"),
        r#"[agent_chain]
developer = ["codex"]
reviewer = ["codex"]
"#,
    )
    .expect("write ralph-workflow.toml");
    config_home
}

/// Initialize a git repository in a temporary directory.
///
/// This function:
/// 1. Creates a new git repository
/// 2. Configures user.name and user.email
/// 3. Creates initial .gitignore and PROMPT.md files
/// 4. Creates the .agent directory
///
/// # Panics
///
/// - If repository initialization fails
/// - If config operations fail
/// - If file system writes fail
/// - If directory creation fails
#[must_use]
pub fn init_git_repo(dir: &TempDir) -> Repository {
    assert_not_project_repo(dir.path());
    let repo = Repository::init(dir.path()).expect("init git repo");

    // Configure user for libgit2's repo.signature() and Ralph's git_commit().
    let mut cfg = repo.config().expect("repo config");
    cfg.set_str("user.name", "Test User")
        .expect("set user.name");
    cfg.set_str("user.email", "test@example.com")
        .expect("set user.email");

    fs::write(dir.path().join(".gitignore"), ".agent/\nPROMPT.md\n").expect("write .gitignore");
    fs::write(
        dir.path().join("PROMPT.md"),
        r#"# Test Requirements

## Goal

Test the Ralph workflow integration.

## Acceptance

- Tests pass successfully
- No validation errors occur
"#,
    )
    .expect("write PROMPT.md");
    fs::create_dir_all(dir.path().join(".agent")).expect("create .agent");

    repo
}

/// Write contents to a file, creating parent directories if needed.
///
/// # Panics
///
/// - If file system write fails
pub fn write_file<P: AsRef<Path>>(path: P, contents: &str) {
    if let Some(parent) = path.as_ref().parent() {
        if !parent.as_os_str().is_empty() {
            let _ = fs::create_dir_all(parent);
        }
    }
    fs::write(path, contents).expect("write file");
}

/// Stage all changes and create a commit.
///
/// # Panics
///
/// - If index operations fail
/// - If tree operations fail
/// - If commit creation fails
#[must_use]
pub fn commit_all(repo: &Repository, message: &str) -> Oid {
    // Policy: tests must never mutate real git state. This assertion fires immediately
    // if the repo is the project repo or not in temp isolation.
    assert_git_mutation_allowed(repo);
    stage_all(repo);

    let mut index = repo.index().expect("open index");
    let tree_id = index.write_tree().expect("write tree");
    let tree = repo.find_tree(tree_id).expect("find tree");

    let sig = Signature::now("Test User", "test@example.com").expect("signature");

    let commit_oid = match repo.head() {
        Ok(head) => {
            let parent = head.peel_to_commit().expect("parent commit");
            repo.commit(Some("HEAD"), &sig, &sig, message, &tree, &[&parent])
                .expect("commit")
        }
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => repo
            .commit(Some("HEAD"), &sig, &sig, message, &tree, &[])
            .expect("initial commit"),
        Err(e) => panic!("unexpected head error: {e}"),
    };
    commit_oid
}

/// Get the HEAD commit OID as a string.
///
/// Returns an empty string if there is no HEAD (e.g., empty repository).
#[must_use]
pub fn head_oid(repo: &Repository) -> String {
    repo.head()
        .ok()
        .and_then(|h| h.target())
        .map(|oid| oid.to_string())
        .unwrap_or_default()
}

/// Stage all changes in the repository, including deletions.
///
/// # Panics
///
/// - If index operations fail
/// - If status retrieval fails
pub fn stage_all(repo: &Repository) {
    assert_repo_is_isolated(repo);
    let mut index = repo.index().expect("open index");

    // Stage deletions explicitly.
    let mut status_opts = StatusOptions::new();
    status_opts
        .include_untracked(true)
        .recurse_untracked_dirs(true)
        .include_ignored(false);
    let statuses = repo.statuses(Some(&mut status_opts)).expect("statuses");
    for entry in statuses.iter() {
        if entry.status().contains(Status::WT_DELETED) {
            if let Some(path) = entry.path() {
                index
                    .remove_path(Path::new(path))
                    .expect("remove deleted path");
            }
        }
    }

    index
        .add_all(["."], IndexAddOption::DEFAULT, None)
        .expect("add_all");
    index.write().expect("write index");
}

/// Commit all changes using git2 library (no subprocess spawning).
///
/// This function uses git2 library APIs directly to create commits without
/// spawning external git processes. All operations are in-process and mockable.
/// It stages all changes (including deletions) and creates a commit.
///
/// # Arguments
///
/// * `repo` - The git repository (must be initialized)
/// * `message` - The commit message
///
/// # Returns
///
/// The OID of the created commit.
///
/// # Panics
///
/// - If git operations fail (index write, commit creation, etc.)
#[must_use]
pub fn git_commit_all(repo: &Repository, message: &str) -> Oid {
    // Policy: tests must never mutate real git state. This assertion fires immediately
    // if the repo is the project repo or not in temp isolation.
    assert_git_mutation_allowed(repo);
    // Stage all changes using git2 (same as commit_all, but for git CLI migration)
    stage_all(repo);

    let mut index = repo.index().expect("open index");
    let tree_id = index.write_tree().expect("write tree");
    let tree = repo.find_tree(tree_id).expect("find tree");

    let sig = Signature::now("Test User", "test@example.com").expect("signature");

    // Create commit using git2 API (no subprocess)
    let commit_oid = match repo.head() {
        Ok(head) => {
            let parent = head.peel_to_commit().expect("parent commit");
            repo.commit(Some("HEAD"), &sig, &sig, message, &tree, &[&parent])
                .expect("commit")
        }
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => {
            // Initial commit (no HEAD yet)
            repo.commit(Some("HEAD"), &sig, &sig, message, &tree, &[])
                .expect("initial commit")
        }
        Err(e) => panic!("unexpected head error: {e}"),
    };
    commit_oid
}

/// Switch to a branch using git2 library (no subprocess spawning).
///
/// This function uses git2 library APIs to checkout branches directly
/// without spawning external git processes. It updates HEAD, index, and
/// working directory to match the target branch.
///
/// # Arguments
///
/// * `repo` - The git repository
/// * `branch_name` - The name of branch to checkout (e.g., "main", "feature")
///
/// # Panics
///
/// - If branch cannot be found
/// - If checkout operations fail
pub fn git_switch(repo: &Repository, branch_name: &str) {
    assert_repo_is_isolated(repo);
    let branch_ref = format!("refs/heads/{branch_name}");
    let obj = repo
        .revparse_single(&branch_ref)
        .expect("find branch for checkout");
    let commit = obj.peel_to_commit().expect("peel to commit");

    // Use git2 checkout builder (no subprocess)
    let mut checkout_builder = CheckoutBuilder::new();
    checkout_builder
        .force()
        .remove_untracked(true)
        .remove_ignored(true);

    repo.checkout_tree(commit.as_object(), Some(&mut checkout_builder))
        .expect("checkout tree");

    repo.set_head(&branch_ref).expect("set HEAD");
}

/// Switch to a branch using git2 library with force checkout (no subprocess spawning).
///
/// This function uses git2 library APIs to force checkout branches
/// and update working directory without spawning external git processes.
/// The force checkout is built into git2's checkout builder.
///
/// # Arguments
///
/// * `repo` - The git repository
/// * `branch_name` - The name of branch to switch to
///
/// # Panics
///
/// - If git operations fail
pub fn git_switch_force(repo: &Repository, branch_name: &str) {
    assert_repo_is_isolated(repo);
    // Use git2 checkout with force option (built-in, no separate commands)
    let branch_ref = format!("refs/heads/{branch_name}");
    let obj = repo
        .revparse_single(&branch_ref)
        .expect("find branch for checkout");
    let commit = obj.peel_to_commit().expect("peel to commit");

    let mut checkout_builder = CheckoutBuilder::new();
    checkout_builder
        .force() // This handles checkout-index -f -a behavior
        .remove_untracked(true)
        .remove_ignored(true);

    repo.checkout_tree(commit.as_object(), Some(&mut checkout_builder))
        .expect("checkout tree");

    repo.set_head(&branch_ref).expect("set HEAD");
}

/// Atomic counter for CWD lock simulation.
/// We use atomic increment/decrement to serialize CWD changes in tests.
/// A value of 0 means unlocked, >0 means locked.
static CWD_LOCK: AtomicU32 = AtomicU32::new(0);

/// RAII guard to restore the working directory on drop.
struct DirGuard(std::path::PathBuf);

impl Drop for DirGuard {
    fn drop(&mut self) {
        let _ = std::env::set_current_dir(&self.0);
    }
}

/// Run a test function in a temporary directory.
///
/// This function:
/// 1. Acquires a global lock to prevent CWD race conditions
/// 2. Creates a temporary directory
/// 3. Changes to that directory
/// 4. Runs the provided test function
/// 5. Restores the original directory (even on panic)
///
/// # Panics
///
/// If the mutex is poisoned (a previous test panicked while holding it),
/// this function will clear the poison and continue. This prevents a single
/// test failure from causing cascading failures.
///
/// # Example
///
/// ```ignore
/// use test_helpers::with_temp_cwd;
///
/// #[test]
/// fn test_something() {
///     with_temp_cwd(|dir| {
///         // dir is the TempDir, and we're already in it
///         std::fs::write("test.txt", "hello").unwrap();
///         assert!(std::path::Path::new("test.txt").exists());
///     });
/// }
/// ```
pub fn with_temp_cwd<F: FnOnce(&TempDir)>(f: F) {
    loop {
        match CWD_LOCK.compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire) {
            Ok(_) => break,
            Err(_) => std::thread::yield_now(),
        }
    }

    let dir = TempDir::new().expect("Failed to create temp directory");
    let old_dir = std::env::current_dir().unwrap_or_else(|_| std::env::temp_dir());
    std::env::set_current_dir(dir.path()).expect("Failed to change to temp directory");
    let _guard = DirGuard(old_dir);

    f(&dir);

    CWD_LOCK.store(0, Ordering::Release);
}

/// Check whether any ancestor of `path` (including `path` itself) is inside a real git
/// repository (identified by the presence of a `.git` directory).
///
/// This is a fail-fast guardrail: any test that attempts to use real git state must
/// panic immediately with a clear policy error rather than silently succeeding or
/// corrupting the project's git state.
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if a `.git` directory is found in any
/// ancestor of `path`.
pub fn assert_no_real_git_state(path: &std::path::Path) {
    let path = match std::fs::canonicalize(path) {
        Ok(p) => p,
        Err(_) => return, // Cannot canonicalize - skip check
    };

    // Check the path and all its ancestors for .git
    let mut current: &std::path::Path = &path;
    loop {
        if current.join(".git").exists() {
            panic!(
                "POLICY VIOLATION: test is using real git state at '{}'. \
                 All tests must use MemoryWorkspace. See docs/agents/testing-guide.md.",
                path.display()
            );
        }
        match current.parent() {
            Some(parent) => {
                if parent == current {
                    break;
                }
                current = parent;
            }
            None => break,
        }
    }
}

/// RAII guard that prevents tests from using real git state with a workspace.
///
/// Constructing a `TestWorkspaceGuard` with a workspace that has a root inside
/// a real git repository will panic immediately with a POLICY VIOLATION error.
/// This ensures tests cannot accidentally mutate the project's real git state.
///
/// # Example
///
/// ```ignore
/// let guard = TestWorkspaceGuard::new(workspace, workspace.root().to_path_buf());
/// // Use workspace safely - any git state access will panic
/// ```
pub struct TestWorkspaceGuard<W> {
    workspace: W,
}

impl<W> TestWorkspaceGuard<W> {
    /// Create a new guard for the given workspace.
    ///
    /// The `root_hint` parameter specifies the root path to check for real git state.
    /// This allows guards to be created for workspaces that may not expose their root
    /// directly, or to explicitly validate the intended root path.
    ///
    /// # Panics
    ///
    /// Panics if `root_hint` is inside a real git repository.
    pub fn new(workspace: W, root_hint: std::path::PathBuf) -> Self {
        assert_no_real_git_state(&root_hint);
        Self { workspace }
    }

    /// Get a reference to the wrapped workspace.
    pub fn workspace(&self) -> &W {
        &self.workspace
    }
}

/// Trait for workspace types that have a root directory.
///
/// This allows `TestWorkspaceGuard` to work with any workspace-like type
/// without requiring a dependency on ralph-workflow.
pub trait HasRoot {
    fn root(&self) -> &std::path::Path;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[should_panic(expected = "POLICY VIOLATION: test is using real git state")]
    fn assert_no_real_git_state_panics_on_real_repo_path() {
        // Use the project repo root as a test case - it contains .git
        // This test verifies the policy guard works correctly by checking
        // that it panics with the expected message when given a real git repo path.
        let project_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent() // test-helpers/
            .and_then(|p| p.parent()); // workspace root

        if let Some(root) = project_root {
            // Only run if we found a valid project root
            // The test will panic with POLICY VIOLATION if the path is inside a git repo
            assert_no_real_git_state(root);
            // If we get here without panicking, the path was not inside a git repo
            // which is fine for the test - we've verified the function doesn't panic unexpectedly
        }
    }

    #[test]
    fn assert_no_real_git_state_does_not_panic_on_temp_path() {
        // Temp directories are not inside a git repo (unless TMPDIR is misconfigured)
        let temp_path = std::env::temp_dir();
        // This should not panic
        assert_no_real_git_state(&temp_path);
    }

    #[test]
    fn test_workspace_guard_accepts_non_git_workspace() {
        // TestWorkspaceGuard should accept a workspace with a temp root
        struct FakeWorkspace {
            root: PathBuf,
        }
        impl HasRoot for FakeWorkspace {
            fn root(&self) -> &Path {
                &self.root
            }
        }

        let temp_dir = tempfile::TempDir::new().unwrap();
        let fake_ws = FakeWorkspace {
            root: temp_dir.path().to_path_buf(),
        };
        // This should not panic
        let _guard = TestWorkspaceGuard::new(fake_ws, temp_dir.path().to_path_buf());
    }

    #[test]
    #[should_panic(expected = "POLICY VIOLATION: test is using real git state")]
    fn test_workspace_guard_rejects_real_git_workspace() {
        // Use the project repo root to verify the guard rejects it
        let project_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent() // test-helpers/
            .and_then(|p| p.parent()); // workspace root

        if let Some(root) = project_root {
            struct FakeWorkspace {
                root: PathBuf,
            }
            impl HasRoot for FakeWorkspace {
                fn root(&self) -> &Path {
                    &self.root
                }
            }

            let fake_ws = FakeWorkspace {
                root: root.to_path_buf(),
            };
            // This should panic because root is inside a real git repo
            let _guard = TestWorkspaceGuard::new(fake_ws, root.to_path_buf());
        }
    }

    #[test]
    fn test_git_mutation_guard_detects_real_project_repo() {
        // Regression test: verify that GitMutationGuard detects when a test
        // attempts to create commits in the real project repository.
        //
        // This test opens the project repo and attempts to create a commit.
        // The GitMutationGuard should panic when dropped if HEAD changed.
        let project = project_repo_root();
        if project.is_none() {
            return; // Cannot test without project repo
        }
        let repo = Repository::open(project.unwrap()).expect("open project repo");

        // Create guard - captures current HEAD
        let guard = GitMutationGuard::new();
        if guard.is_none() {
            return; // Cannot test without HEAD access
        }
        let _guard = guard.unwrap();

        // Attempting to call commit_all on the project repo would panic.
        // We use catch_unwind to verify the policy fires correctly.
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            // This would panic with POLICY VIOLATION because the repo is the project repo
            let _ = commit_all(&repo, "test commit");
        }));

        // The commit should have panicked, not succeeded
        assert!(
            result.is_err(),
            "commit_all on project repo should panic with POLICY VIOLATION"
        );
    }

    #[test]
    fn test_git_mutation_guard_allows_temp_repo() {
        // Verify that GitMutationGuard does NOT panic for an isolated temp repo.
        let temp_dir = tempfile::TempDir::new().expect("create temp dir");
        let repo = init_git_repo(&temp_dir);

        // Create guard - captures current HEAD
        let guard = GitMutationGuard::new();
        if guard.is_none() {
            return; // Cannot test without HEAD access
        }
        let _guard = guard.unwrap();

        // This should NOT panic - repo is isolated
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _ = commit_all(&repo, "test commit in temp repo");
        }));

        // The commit should have succeeded (no panic)
        assert!(
            result.is_ok(),
            "commit_all on temp repo should succeed without panic"
        );
    }
}
