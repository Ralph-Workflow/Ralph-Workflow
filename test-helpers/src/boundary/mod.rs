// Lint policy: `test-helpers` is a boundary crate for higher-level tests, not an
// exemption from the style guide.
//
// See `CODE_STYLE.md`, `docs/code-style/testing.md`,
// `docs/code-style/boundaries.md`, and `test-helpers/clippy.toml`.
//
// `clippy::cargo` stays off because it reports dependency conflicts outside the
// code-shape problems this crate can actually fix.

use git2::build::CheckoutBuilder;
use git2::{Commit, IndexAddOption, Oid, Repository, Signature, Status, StatusOptions};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::OnceLock;
use tempfile::TempDir;

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
    stage_all(repo);

    let mut index = repo.index().expect("open index");
    let tree_id = index.write_tree().expect("write tree");
    let tree = repo.find_tree(tree_id).expect("find tree");

    let sig = Signature::now("Test User", "test@example.com").expect("signature");

    let parent_commit = repo.head().ok().and_then(|head| head.peel_to_commit().ok());
    let parent_refs: Vec<&Commit> = parent_commit.iter().collect();

    let commit_oid = repo
        .commit(
            Some("HEAD"),
            &sig,
            &sig,
            message,
            &tree,
            parent_refs.as_slice(),
        )
        .expect("commit");

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
    let mut index = repo.index().expect("open index");
    stage_deleted_entries(repo, &mut index);

    index
        .add_all(["."], IndexAddOption::DEFAULT, None)
        .expect("add_all");
    index.write().expect("write index");
}

fn stage_deleted_entries(repo: &Repository, index: &mut git2::Index) {
    let deleted_paths = collect_deleted_paths(repo);
    remove_deleted_paths(index, deleted_paths);
}

fn collect_deleted_paths(repo: &Repository) -> Vec<PathBuf> {
    let mut status_opts = StatusOptions::new();
    status_opts
        .include_untracked(true)
        .recurse_untracked_dirs(true)
        .include_ignored(false);

    let statuses = repo.statuses(Some(&mut status_opts)).expect("statuses");

    statuses
        .iter()
        .filter(|entry| entry.status().contains(Status::WT_DELETED))
        .filter_map(|entry| entry.path().map(PathBuf::from))
        .collect()
}

fn remove_deleted_paths(index: &mut git2::Index, paths: Vec<PathBuf>) {
    for path in paths {
        index
            .remove_path(path.as_path())
            .expect("remove deleted path");
    }
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
    // Stage all changes using git2 (same as commit_all, but for git CLI migration)
    stage_all(repo);

    let mut index = repo.index().expect("open index");
    let tree_id = index.write_tree().expect("write tree");
    let tree = repo.find_tree(tree_id).expect("find tree");

    let sig = Signature::now("Test User", "test@example.com").expect("signature");

    // Create commit using git2 API (no subprocess)
    let parent_commit = repo.head().ok().and_then(|head| head.peel_to_commit().ok());
    let parent_refs: Vec<&Commit> = parent_commit.iter().collect();

    let commit_oid = repo
        .commit(
            Some("HEAD"),
            &sig,
            &sig,
            message,
            &tree,
            parent_refs.as_slice(),
        )
        .expect("commit");

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

struct CwdLockGuard;

impl Drop for CwdLockGuard {
    fn drop(&mut self) {
        CWD_LOCK.store(0, Ordering::Release);
    }
}

fn acquire_cwd_lock() -> CwdLockGuard {
    while CWD_LOCK
        .compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        std::thread::yield_now();
    }

    CwdLockGuard
}

fn set_temp_directory(dir: &TempDir) -> DirGuard {
    let old_dir = std::env::current_dir().unwrap_or_else(|_| std::env::temp_dir());
    std::env::set_current_dir(dir.path()).expect("Failed to change to temp directory");
    DirGuard(old_dir)
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
    let _lock = acquire_cwd_lock();
    let dir = TempDir::new().expect("Failed to create temp directory");
    let _guard = set_temp_directory(&dir);

    f(&dir);
}

// ── Project-repo detection ───────────────────────────────────────────────────

static PROJECT_REPO_ROOT: OnceLock<Option<PathBuf>> = OnceLock::new();

/// Return the root of the project git repository (cached), or `None`.
pub fn project_repo_root() -> Option<&'static Path> {
    PROJECT_REPO_ROOT
        .get_or_init(|| {
            let start = std::env::current_exe().ok()?.parent()?.to_path_buf();
            std::iter::successors(Some(start), |p| p.parent().map(|pp| pp.to_path_buf()))
                .find(|p| p.join(".git").exists())
        })
        .as_deref()
}

/// Panic if `repo_root` is inside the project's own repository.
pub fn assert_not_project_repo(repo_root: &Path) {
    let Some(project) = project_repo_root() else {
        return;
    };
    let repo_abs = std::fs::canonicalize(repo_root).unwrap_or_else(|_| repo_root.to_path_buf());
    let project_abs = std::fs::canonicalize(project).unwrap_or_else(|_| project.to_path_buf());
    let is_project_repo = repo_abs == project_abs || repo_abs.starts_with(&project_abs);
    if is_project_repo {
        panic!(
            "POLICY VIOLATION: test attempted to mutate real git repository at {}\n\
             All tests MUST operate on isolated repositories under std::env::temp_dir().\n\
             Use test_helpers::init_git_repo() to create an isolated test repository.",
            repo_abs.display()
        );
    }
}

/// Panic if `repo.workdir()` is not inside the system temp directory.
pub fn assert_repo_is_temp_isolated(repo: &Repository) {
    let Some(workdir) = repo.workdir() else {
        return;
    };
    let workdir_abs = std::fs::canonicalize(workdir).unwrap_or_else(|_| workdir.to_path_buf());
    let temp_dir_abs =
        std::fs::canonicalize(std::env::temp_dir()).unwrap_or_else(|_| std::env::temp_dir());
    if !workdir_abs.starts_with(&temp_dir_abs) {
        panic!(
            "POLICY VIOLATION: repository workdir '{}' is not inside temp directory '{}'. \
             All test repositories must be created with TempDir::new() and live under the \
             system's temp directory.",
            workdir.display(),
            temp_dir_abs.display()
        );
    }
}

/// Capture the HEAD OID of the project repository, or `None`.
pub fn capture_project_head_oid() -> Option<String> {
    let project = project_repo_root()?;
    let repo = Repository::open(project).ok()?;
    let head = repo.head().ok()?;
    let oid = head.target()?;
    Some(oid.to_string())
}

// ── Git-ancestor path walkers ─────────────────────────────────────────────────

/// Walk ancestors of `path` (with canonicalization) and return the first one
/// that contains a `.git` directory, or `None` if none is found.
pub fn find_git_ancestor(path: &Path) -> Option<PathBuf> {
    std::iter::successors(Some(path.to_path_buf()), |p| {
        std::fs::canonicalize(p)
            .ok()
            .and_then(|c| c.parent().map(|pp| pp.to_path_buf()))
            .or_else(|| p.parent().map(|pp| pp.to_path_buf()))
            .filter(|next| next != p)
    })
    .find(|p| p.join(".git").exists())
}

/// Walk ancestors of `path` (no canonicalization) and return the first one
/// that contains a `.git` directory, or `None`.
pub fn find_git_ancestor_simple(path: &Path) -> Option<PathBuf> {
    std::iter::successors(Some(path.to_path_buf()), |p| {
        p.parent()
            .filter(|pp| *pp != p.as_path())
            .map(|pp| pp.to_path_buf())
    })
    .find(|p| p.join(".git").exists())
}

/// Panic if `path` is inside any real git repository (checked with canonicalization).
///
/// Used by `git_safety::assert_not_real_git_repo`.
pub fn assert_not_real_git_repo_impl(path: &Path) {
    if let Some(git_root) = find_git_ancestor(path) {
        panic!(
            "POLICY VIOLATION: test path '{}' is inside a real git repository at '{}'. \
             Tests must use MemoryWorkspace or isolated temp directories outside any repo. \
             See docs/agents/testing-guide.md.",
            path.display(),
            git_root.display()
        );
    }
}

/// Panic if `path` is inside a `.git` directory in a non-temp location.
///
/// Used by `lib::assert_no_real_git_repo`.
pub fn assert_no_real_git_repo_impl(path: &Path) {
    if let Some(git_root) = find_git_ancestor(path) {
        let tmp = std::env::temp_dir();
        assert!(
            git_root.starts_with(&tmp),
            "POLICY VIOLATION: Test attempted to operate on a real git repository at {:?}. \
             All tests must use MemoryWorkspace or a git repo inside a temp directory. \
             Do NOT use environment variables or feature flags to bypass this requirement.",
            git_root
        );
    }
}

/// Panic if `path` is inside the project's own git repository (not any isolated repo).
///
/// Used by `git_safety::assert_in_isolated_temp_repo`.
pub fn assert_in_isolated_temp_repo_impl(path: &Path) {
    let Some(project_git_dir) = find_project_git_dir_canonical() else {
        return;
    };
    if let Some(git_root) = find_git_ancestor(path) {
        let is_project = git_root
            .join(".git")
            .canonicalize()
            .ok()
            .map(|c| c == project_git_dir)
            .unwrap_or(false);
        if is_project {
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

/// Panic if `path` is inside any git repository (simple walk, no canonicalization).
///
/// Used by `git_guard::assert_not_in_git_repo`.
pub fn assert_not_in_git_repo_impl(path: &Path) {
    if let Some(git_root) = find_git_ancestor_simple(path) {
        panic!(
            "POLICY VIOLATION: test path '{}' is inside a git repository at '{}'.\n\
             Tests must not operate on real git state. Use temp_dir_outside_git() \
             to create a safe temporary directory, or ensure your test path is \
             outside all git repositories.",
            path.display(),
            git_root.display()
        );
    }
}

fn find_project_git_dir_canonical() -> Option<PathBuf> {
    let start = std::env::current_dir().ok()?;
    std::iter::successors(Some(start), |p| p.parent().map(|pp| pp.to_path_buf()))
        .find(|p| p.join(".git").exists())
        .and_then(|p| p.join(".git").canonicalize().ok())
}

/// Create a temporary directory guaranteed to be outside any git repository.
///
/// Used by `git_guard::temp_dir_outside_git`.
///
/// # Panics
///
/// Panics if the temporary directory cannot be created, or if it is unexpectedly
/// inside a git repository.
#[must_use]
pub fn temp_dir_outside_git_impl(prefix: &str) -> TempDir {
    let dir = tempfile::Builder::new()
        .prefix(prefix)
        .tempdir()
        .unwrap_or_else(|e| panic!("temp_dir_outside_git: failed to create temp dir: {e}"));
    assert_not_in_git_repo_impl(dir.path());
    dir
}
