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

use git2::{Oid, Repository};
use std::path::Path;
use tempfile::TempDir;

pub mod boundary;
pub mod git_guard;
pub use git_guard::{assert_not_in_git_repo, temp_dir_outside_git, GitGuard};
pub mod git_safety;
pub use git_safety::no_real_git_mutation;

// ── Project-repo helpers (thin wrappers over boundary) ───────────────────────

/// Enforce that no test operates on the project's real git repository.
///
/// # Panics
///
/// Panics with a policy-violation message if `repo_root` is inside or equal to
/// the project's own repository root.
pub fn assert_not_project_repo(repo_root: &Path) {
    boundary::assert_not_project_repo(repo_root);
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
/// # Panics
///
/// Panics with a policy-violation message if `repo.workdir()` is not inside
/// `std::env::temp_dir()`.
pub fn assert_repo_is_temp_isolated(repo: &Repository) {
    boundary::assert_repo_is_temp_isolated(repo);
}

/// Capture the HEAD OID of the project repository.
///
/// Returns `None` if the project root cannot be determined or if reading fails.
pub fn capture_project_head_oid() -> Option<String> {
    boundary::capture_project_head_oid()
}

/// Assert that the project repository's HEAD has not changed since `before` was captured.
///
/// # Panics
///
/// Panics with a clear POLICY VIOLATION message if HEAD differs from `before`.
pub fn assert_project_head_unchanged(before: &Option<String>) {
    let after = match capture_project_head_oid() {
        Some(oid) => oid,
        None => return,
    };
    if before.as_ref() != Some(&after) {
        panic!(
            "POLICY VIOLATION: test attempted to mutate real git repository at project repository\n\
             All tests MUST operate on isolated repositories under std::env::temp_dir().\n\
             Use test_helpers::init_git_repo() to create an isolated test repository.",
        );
    }
}

/// Policy: assert that a repository is allowed to receive git mutations.
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
pub struct GitMutationGuard {
    before_head: Option<String>,
}

impl GitMutationGuard {
    /// Create a new guard, capturing the current project HEAD.
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

// ── Git operation wrappers (policy check + boundary delegation) ───────────────

/// Create an isolated config file in the test directory.
///
/// # Panics
///
/// - If directory creation fails
/// - If config file write fails
#[must_use]
pub fn create_isolated_config(dir: &Path) -> std::path::PathBuf {
    boundary::create_isolated_config(dir)
}

/// Initialize a git repository in a temporary directory.
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
    boundary::init_git_repo(dir)
}

/// Write contents to a file, creating parent directories if needed.
///
/// # Panics
///
/// - If file system write fails
pub fn write_file<P: AsRef<Path>>(path: P, contents: &str) {
    boundary::write_file(path, contents);
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
    assert_git_mutation_allowed(repo);
    boundary::commit_all(repo, message)
}

/// Get the HEAD commit OID as a string.
///
/// Returns an empty string if there is no HEAD (e.g., empty repository).
#[must_use]
pub fn head_oid(repo: &Repository) -> String {
    boundary::head_oid(repo)
}

/// Stage all changes in the repository, including deletions.
///
/// # Panics
///
/// - If index operations fail
/// - If status retrieval fails
pub fn stage_all(repo: &Repository) {
    assert_repo_is_isolated(repo);
    boundary::stage_all(repo);
}

/// Commit all changes using git2 library (no subprocess spawning).
///
/// # Panics
///
/// - If git operations fail (index write, commit creation, etc.)
#[must_use]
pub fn git_commit_all(repo: &Repository, message: &str) -> Oid {
    assert_git_mutation_allowed(repo);
    boundary::git_commit_all(repo, message)
}

/// Switch to a branch using git2 library (no subprocess spawning).
///
/// # Panics
///
/// - If branch cannot be found
/// - If checkout operations fail
pub fn git_switch(repo: &Repository, branch_name: &str) {
    assert_repo_is_isolated(repo);
    boundary::git_switch(repo, branch_name);
}

/// Switch to a branch using git2 library with force checkout (no subprocess spawning).
///
/// # Panics
///
/// - If git operations fail
pub fn git_switch_force(repo: &Repository, branch_name: &str) {
    assert_repo_is_isolated(repo);
    boundary::git_switch_force(repo, branch_name);
}

// ── Safety guardrails ─────────────────────────────────────────────────────────

/// Fail fast if an MCP test session would allow real git mutation.
pub fn assert_mcp_test_no_real_git(workspace_root: &std::path::Path) {
    assert_no_real_git_state(workspace_root);
}

/// Fail fast with a policy error if the test would be able to mutate real git state.
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if `path` is inside a real (non-temp)
/// git repository.
pub fn assert_no_real_git_mutations(path: &std::path::Path) {
    assert_no_real_git_state(path);
}

/// Run a test function in a temporary directory.
///
/// # Panics
///
/// If the temp directory cannot be created or changed to.
pub fn with_temp_cwd<F: FnOnce(&TempDir)>(f: F) {
    boundary::with_temp_cwd(f);
}

/// Fail fast with a policy error if `path` is inside a real (non-temp) git repository.
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if a `.git` directory is found outside
/// of `std::env::temp_dir()`.
pub fn assert_no_real_git_repo(path: &std::path::Path) {
    boundary::assert_no_real_git_repo_impl(path);
}

/// Fail fast with a policy error if `path` is inside any real git repository.
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if a `.git` directory is found in any
/// ancestor of `path`.
pub fn assert_no_real_git_state(path: &std::path::Path) {
    git_safety::assert_not_real_git_repo(path);
}

/// RAII guard that prevents tests from using real git state with a workspace.
pub struct TestWorkspaceGuard<W> {
    workspace: W,
}

impl<W> TestWorkspaceGuard<W> {
    /// Create a new guard for the given workspace.
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
pub trait HasRoot {
    fn root(&self) -> &std::path::Path;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    #[should_panic(expected = "POLICY VIOLATION: test path")]
    fn assert_no_real_git_state_panics_on_real_repo_path() {
        let project_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|p| p.parent());

        if let Some(root) = project_root {
            assert_no_real_git_state(root);
        }
    }

    #[test]
    fn assert_no_real_git_state_does_not_panic_on_temp_path() {
        let temp_path = std::env::temp_dir();
        assert_no_real_git_state(&temp_path);
    }

    #[test]
    #[should_panic(expected = "POLICY VIOLATION: test path")]
    fn assert_no_real_git_state_panics_for_nonexistent_path_inside_real_repo() {
        let project_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|p| p.parent());

        if let Some(root) = project_root {
            let missing_path = root.join("definitely-does-not-exist").join("nested");
            assert_no_real_git_state(&missing_path);
        }
    }

    #[test]
    fn test_workspace_guard_accepts_non_git_workspace() {
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
        let _guard = TestWorkspaceGuard::new(fake_ws, temp_dir.path().to_path_buf());
    }

    #[test]
    #[should_panic(expected = "POLICY VIOLATION: test path")]
    fn test_workspace_guard_rejects_real_git_workspace() {
        let project_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|p| p.parent());

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
            let _guard = TestWorkspaceGuard::new(fake_ws, root.to_path_buf());
        }
    }

    #[test]
    fn test_git_mutation_guard_detects_real_project_repo() {
        let project = boundary::project_repo_root();
        if project.is_none() {
            return;
        }
        let repo = Repository::open(project.unwrap()).expect("open project repo");

        let guard = GitMutationGuard::new();
        if guard.is_none() {
            return;
        }
        let _guard = guard.unwrap();

        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _ = commit_all(&repo, "test commit");
        }));

        assert!(
            result.is_err(),
            "commit_all on project repo should panic with POLICY VIOLATION"
        );
    }

    #[test]
    fn test_git_mutation_guard_allows_temp_repo() {
        let temp_dir = tempfile::TempDir::new().expect("create temp dir");
        let repo = init_git_repo(&temp_dir);

        let guard = GitMutationGuard::new();
        if guard.is_none() {
            return;
        }
        let _guard = guard.unwrap();

        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _ = commit_all(&repo, "test commit in temp repo");
        }));

        assert!(
            result.is_ok(),
            "commit_all on temp repo should succeed without panic"
        );
    }
}
