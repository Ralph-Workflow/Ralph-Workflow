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
    assert_repo_is_isolated(repo);
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
    assert_repo_is_isolated(repo);
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
