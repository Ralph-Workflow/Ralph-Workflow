// git_helpers/repo/commit/io.rs — boundary module for git commit and staging operations.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

use std::path::Path;

use crate::git_helpers::git2_to_io_error;
use crate::git_helpers::identity::GitIdentity;

fn is_git2_not_found(err: &git2::Error) -> bool {
    err.code() == git2::ErrorCode::NotFound
}

fn is_git2_unborn_branch(err: &git2::Error) -> bool {
    err.code() == git2::ErrorCode::UnbornBranch
}
fn index_has_changes_to_commit(
    repo: &git2::Repository,
    index: &git2::Index,
) -> std::io::Result<bool> {
    match repo.head() {
        Ok(head) => {
            let head_tree = head.peel_to_tree().map_err(|e| git2_to_io_error(&e))?;
            let diff = repo
                .diff_tree_to_index(Some(&head_tree), Some(index), None)
                .map_err(|e| git2_to_io_error(&e))?;
            Ok(diff.deltas().len() > 0)
        }
        Err(ref e) if is_git2_unborn_branch(e) => Ok(!index.is_empty()),
        Err(e) => Err(git2_to_io_error(&e)),
    }
}

fn is_internal_agent_artifact(path: &std::path::Path) -> bool {
    let path_str = path.to_string_lossy();
    path_str == ".no_agent_commit"
        || path_str == ".agent"
        || path_str.starts_with(".agent/")
        || path_str == ".git"
        || path_str.starts_with(".git/")
}

/// Stage specific files for commit.
///
/// Similar to `git add <files>`. Only stages the named paths.
/// Paths that match `is_internal_agent_artifact` are silently skipped.
///
/// # Returns
///
/// Returns `Ok(true)` if the index has staged changes after adding the specified
/// files, `Ok(false)` if there is nothing to commit, or an error if staging failed.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_add_specific_in_repo(repo_root: &Path, files: &[&str]) -> std::io::Result<bool> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    let mut index = repo.index().map_err(|e| git2_to_io_error(&e))?;

    // Strict selective staging: start from a clean index that matches HEAD, so we don't
    // accidentally commit pre-existing staged changes when a file list is provided.
    match repo.head() {
        Ok(head) => {
            let head_tree = head.peel_to_tree().map_err(|e| git2_to_io_error(&e))?;
            index
                .read_tree(&head_tree)
                .map_err(|e| git2_to_io_error(&e))?;
        }
        Err(ref e) if is_git2_unborn_branch(e) => {
            index.clear().map_err(|e| git2_to_io_error(&e))?;
        }
        Err(e) => return Err(git2_to_io_error(&e)),
    }

    files.iter().try_for_each(|path_str| {
        let path = std::path::Path::new(path_str);
        if is_internal_agent_artifact(path) {
            return Ok(());
        }

        match index.add_path(path) {
            Ok(()) => Ok(()),
            Err(ref e) if is_git2_not_found(e) => {
                let tracked_in_head = index.get_path(path, 0).is_some();
                if !tracked_in_head {
                    let io_err = git2_to_io_error(e);
                    return Err(std::io::Error::new(
                        io_err.kind(),
                        format!(
                            "path '{}' not found for selective staging: {io_err}",
                            path.display()
                        ),
                    ));
                }

                index.remove_path(path).map_err(|remove_err| {
                    let io_err = git2_to_io_error(&remove_err);
                    std::io::Error::new(
                        io_err.kind(),
                        format!(
                            "failed to stage deletion for '{}': {io_err}",
                            path.display()
                        ),
                    )
                })
            }
            Err(e) => {
                let io_err = git2_to_io_error(&e);
                Err(std::io::Error::new(
                    io_err.kind(),
                    format!("failed to stage path '{}': {io_err}", path.display()),
                ))
            }
        }
    })?;

    index.write().map_err(|e| git2_to_io_error(&e))?;
    index_has_changes_to_commit(&repo, &index)
}

/// Stage all changes in the repository at the given `repo_root`.
///
/// This avoids relying on process-wide CWD and allows callers (including tests)
/// to control which repository is targeted.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_add_all_in_repo(repo_root: &Path) -> std::io::Result<bool> {
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    git_add_all_impl(&repo)
}

/// Result of commit operation with fallback.
///
/// This is the fallback-aware version of `CommitResult`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommitResultFallback {
    /// A commit was successfully created with the given OID.
    Success(git2::Oid),
    /// No commit was created because there were no meaningful changes.
    NoChanges,
    /// The commit operation failed with an error message.
    Failed(String),
}

/// Build the status options used when scanning the working tree.
fn configured_status_options() -> git2::StatusOptions {
    let mut status_opts = git2::StatusOptions::new();
    status_opts
        .include_untracked(true)
        .recurse_untracked_dirs(true)
        .include_ignored(false);
    status_opts
}

/// Implementation of git add all.
fn git_add_all_impl(repo: &git2::Repository) -> std::io::Result<bool> {
    let mut index = repo.index().map_err(|e| git2_to_io_error(&e))?;

    // Stage deletions (equivalent to `git add -A` behavior).
    // libgit2's `add_all` doesn't automatically remove deleted paths.
    let mut status_opts = configured_status_options();
    let statuses = repo
        .statuses(Some(&mut status_opts))
        .map_err(|e| git2_to_io_error(&e))?;

    let deletions: Vec<_> = statuses
        .iter()
        .filter(|entry| entry.status().contains(git2::Status::WT_DELETED))
        .filter_map(|entry| entry.path().map(std::path::PathBuf::from))
        .collect();

    deletions
        .iter()
        .try_for_each(|path| index.remove_path(path).map_err(|e| git2_to_io_error(&e)))?;

    // Add all files (staged, unstaged, and untracked).
    // Note: add_all() is required here, not update_all(), to include untracked files.
    let mut filter_cb = |path: &std::path::Path, _matched: &[u8]| -> i32 {
        // Return 0 to add the file, non-zero to skip.
        // We skip (return 1) internal agent artifacts to avoid committing them.
        i32::from(is_internal_agent_artifact(path))
    };
    index
        .add_all(
            vec!["."],
            git2::IndexAddOption::DEFAULT,
            Some(&mut filter_cb),
        )
        .map_err(|e| git2_to_io_error(&e))?;

    index.write().map_err(|e| git2_to_io_error(&e))?;

    // Return true if staging produced something commit-worthy.
    index_has_changes_to_commit(repo, &index)
}

struct GitConfigIdentity {
    name: String,
    email: String,
    has_git_config: bool,
}

fn extract_sig_fields(sig: &git2::Signature<'_>) -> Option<(String, String)> {
    let name = sig.name().unwrap_or("");
    let email = sig.email().unwrap_or("");
    if name.is_empty() || email.is_empty() {
        return None;
    }
    Some((name.to_string(), email.to_string()))
}

fn read_git_config_identity(repo: &git2::Repository) -> GitConfigIdentity {
    repo.signature()
        .ok()
        .and_then(|sig| extract_sig_fields(&sig))
        .map_or(
            GitConfigIdentity { name: String::new(), email: String::new(), has_git_config: false },
            |(name, email)| GitConfigIdentity { name, email, has_git_config: true },
        )
}

fn resolve_final_field<'a>(
    git_config_value: &'a str,
    has_git_config: bool,
    provided: Option<&'a str>,
    env_value: Option<&'a str>,
) -> &'a str {
    if has_git_config && !git_config_value.is_empty() {
        return git_config_value;
    }
    provided
        .filter(|s| !s.is_empty())
        .or(env_value)
        .filter(|s| !s.is_empty())
        .unwrap_or("")
}

fn build_fallback_identity(
    final_name: &str,
    final_email: &str,
    executor: Option<&dyn crate::executor::ProcessExecutor>,
) -> GitIdentity {
    use crate::git_helpers::identity::{fallback_email, fallback_username};
    let username = fallback_username(executor);
    let system_email = fallback_email(&username, executor);
    GitIdentity::new(
        if final_name.is_empty() { username } else { final_name.to_string() },
        if final_email.is_empty() { system_email } else { final_email.to_string() },
    )
}

fn resolve_name_and_email<'a>(
    git_id: &'a GitConfigIdentity,
    provided_name: Option<&'a str>,
    provided_email: Option<&'a str>,
    env_name: Option<&'a str>,
    env_email: Option<&'a str>,
) -> (&'a str, &'a str) {
    let final_name = resolve_final_field(&git_id.name, git_id.has_git_config, provided_name, env_name);
    let final_email = resolve_final_field(&git_id.email, git_id.has_git_config, provided_email, env_email);
    (final_name, final_email)
}

fn try_validated_identity(name: &str, email: &str) -> Option<GitIdentity> {
    if name.is_empty() || email.is_empty() {
        return None;
    }
    let identity = GitIdentity::new(name.to_string(), email.to_string());
    identity.validate().ok().map(|_| identity)
}

fn resolve_commit_identity(
    repo: &git2::Repository,
    provided_name: Option<&str>,
    provided_email: Option<&str>,
    executor: Option<&dyn crate::executor::ProcessExecutor>,
    env: Option<&dyn crate::runtime::environment::Environment>,
) -> GitIdentity {
    use crate::git_helpers::identity::default_identity;

    let env = env.unwrap_or(&crate::runtime::environment::RealEnvironment);
    let git_id = read_git_config_identity(repo);
    let env_name = env.var("RALPH_GIT_USER_NAME");
    let env_email = env.var("RALPH_GIT_USER_EMAIL");
    let (final_name, final_email) =
        resolve_name_and_email(&git_id, provided_name, provided_email, env_name.as_deref(), env_email.as_deref());

    try_validated_identity(final_name, final_email)
        .or_else(|| {
            let identity = build_fallback_identity(final_name, final_email, executor);
            identity.validate().ok().map(|_| identity)
        })
        .unwrap_or_else(default_identity)
}

/// Create a commit.
///
/// Similar to `git commit -m <message>`.
///
/// Handles both initial commits (no HEAD yet) and subsequent commits.
///
/// # Identity Resolution
///
/// The git commit identity (name and email) is resolved using the following priority:
/// 1. Git config (via libgit2) - primary source
/// 2. Provided `git_user_name` and `git_user_email` parameters (overrides)
/// 3. Environment variables (`RALPH_GIT_USER_NAME`, `RALPH_GIT_USER_EMAIL`)
/// 4. Ralph config file (read by caller, passed as parameters)
/// 5. System username + derived email (sane fallback)
/// 6. Default values ("Ralph Workflow", "ralph@localhost") - last resort
///
/// Partial overrides are supported: CLI args/env vars/config can override individual
/// fields (name or email) from git config.
///
/// # Errors
///
/// Returns error if the operation fails.
/// Create a commit in the repository at the given `repo_root`.
///
/// This avoids relying on process-wide CWD and requires callers to supply
/// an explicit repository path. This is intentional: any code that calls
/// this function must think carefully about which repository it targets.
///
/// # Policy Assertion
///
/// This function will panic if `repo_root` resolves to the project's own
/// development repository. The project's development repository is identified
/// by walking up from the current executable to find a directory containing
/// both `CLAUDE.md` and `PROMPT.md` — these files exist only in the project root.
///
/// This guard exists because test runs have previously created commits
/// ("feat: add new file flow") in the development repository and reverted
/// developer changes. Structural enforcement (path-based) is used instead of
/// environment variables because env vars can be overridden or forgotten.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_commit_in_repo(
    repo_root: &Path,
    message: &str,
    git_user_name: Option<&str>,
    git_user_email: Option<&str>,
    executor: Option<&dyn crate::executor::ProcessExecutor>,
    env: Option<&dyn crate::runtime::environment::Environment>,
) -> std::io::Result<Option<git2::Oid>> {
    // Policy assertion: prevent commits to the project's own development repository.
    assert_not_project_repo_for_commit(repo_root);

    // Use open() instead of discover() to require an explicit path.
    // discover() walks up the directory tree, which can accidentally target
    // the project repo when called with "." in tests.
    let repo = git2::Repository::open(repo_root).map_err(|e| git2_to_io_error(&e))?;
    git_commit_impl(&repo, message, git_user_name, git_user_email, executor, env)
}

/// Policy check: panic if `repo_root` is the project's own development repository.
///
/// The project repository is identified by walking up from the current executable
/// to find a directory containing both `CLAUDE.md` and `PROMPT.md`.
fn assert_not_project_repo_for_commit(repo_root: &Path) {
    let repo_abs = resolve_path_canonical(repo_root);
    let project_abs = match find_project_root_containing_markers() {
        Some(p) => resolve_path_canonical(&p),
        None => return, // Cannot determine project root — skip check
    };

    check_repo_not_project_repo(&repo_abs, &project_abs);
}

/// Resolve a path to its canonical form, falling back to the original if canonicalization fails.
fn resolve_path_canonical(path: &Path) -> std::path::PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

/// Pure policy check: panics if repo_abs is at or under project_abs.
fn check_repo_not_project_repo(repo_abs: &Path, project_abs: &Path) {
    if repo_abs == project_abs || repo_abs.starts_with(project_abs) {
        panic!(
            "POLICY VIOLATION: git write operations on the project's own development \
             repository are forbidden. This check exists because test runs previously \
             created commits ('feat: add new file flow') in the development repository \
             and reverted developer changes. Fix your test to use an isolated TempDir \
             workspace, not the project root. Detected project root: {}",
            project_abs.display()
        );
    }
}

/// Find the project root by walking up from the current executable,
/// looking for a directory containing both CLAUDE.md and PROMPT.md.
fn find_project_root_containing_markers() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let start = exe.parent()?.to_path_buf();
    std::iter::successors(Some(start), |p| p.parent().map(|pp| pp.to_path_buf()))
        .find(|p| p.join("CLAUDE.md").exists() && p.join("PROMPT.md").exists())
}

fn has_cli_override(git_user_name: Option<&str>, git_user_email: Option<&str>) -> bool {
    git_user_name.is_some() || git_user_email.is_some()
}

fn has_env_override(env: &dyn crate::runtime::environment::Environment) -> bool {
    env.var("RALPH_GIT_USER_NAME").is_some() || env.var("RALPH_GIT_USER_EMAIL").is_some()
}

fn identity_source_from_repo_or_default(repo: &git2::Repository) -> &'static str {
    if repo.signature().is_ok() { "git config" } else { "system/default" }
}

fn identity_source_label(
    repo: &git2::Repository,
    git_user_name: Option<&str>,
    git_user_email: Option<&str>,
    env: &dyn crate::runtime::environment::Environment,
) -> &'static str {
    if has_cli_override(git_user_name, git_user_email) {
        "CLI/config override"
    } else if has_env_override(env) {
        "environment variable"
    } else {
        identity_source_from_repo_or_default(repo)
    }
}

fn log_identity_if_debug(
    repo: &git2::Repository,
    name: &str,
    email: &str,
    git_user_name: Option<&str>,
    git_user_email: Option<&str>,
    env: &dyn crate::runtime::environment::Environment,
) {
    if env.var("RALPH_DEBUG").is_some() {
        let identity_source = identity_source_label(repo, git_user_name, git_user_email, env);
        let _ = std::io::Write::write_fmt(
            &mut std::io::stderr(),
            format_args!("Git identity: {name} <{email}> (source: {identity_source})\n"),
        );
    }
}

fn commit_on_existing_branch(
    repo: &git2::Repository,
    sig: &git2::Signature<'_>,
    message: &str,
    tree: &git2::Tree<'_>,
    head: git2::Reference<'_>,
) -> Result<git2::Oid, git2::Error> {
    let head_commit = head.peel_to_commit()?;
    repo.commit(Some("HEAD"), sig, sig, message, tree, &[&head_commit])
}

fn commit_on_unborn_branch(
    repo: &git2::Repository,
    sig: &git2::Signature<'_>,
    message: &str,
    tree: &git2::Tree<'_>,
) -> std::io::Result<Option<Result<git2::Oid, git2::Error>>> {
    if !tree_has_entries(tree) {
        return Ok(None);
    }
    Ok(Some(repo.commit(Some("HEAD"), sig, sig, message, tree, &[])))
}

fn commit_with_head(
    repo: &git2::Repository,
    sig: &git2::Signature<'_>,
    message: &str,
    tree: &git2::Tree<'_>,
) -> std::io::Result<Option<git2::Oid>> {
    let git2_result = match repo.head() {
        Ok(head) => commit_on_existing_branch(repo, sig, message, tree, head),
        Err(ref e) if is_git2_unborn_branch(e) => {
            return commit_on_unborn_branch(repo, sig, message, tree)?
                .map(|r| r.map(Some).map_err(|e| git2_to_io_error(&e)))
                .transpose()
                .map(Option::flatten);
        }
        Err(e) => return Err(git2_to_io_error(&e)),
    };
    Ok(Some(git2_result.map_err(|e| git2_to_io_error(&e))?))
}

fn git_commit_impl(
    repo: &git2::Repository,
    message: &str,
    git_user_name: Option<&str>,
    git_user_email: Option<&str>,
    executor: Option<&dyn crate::executor::ProcessExecutor>,
    env: Option<&dyn crate::runtime::environment::Environment>,
) -> std::io::Result<Option<git2::Oid>> {
    let mut index = repo.index().map_err(|e| git2_to_io_error(&e))?;

    // Don't create empty commits: if the index matches HEAD (or is empty on an unborn branch),
    // there's nothing to commit.
    if !index_has_changes_to_commit(repo, &index)? {
        return Ok(None);
    }

    let tree_oid = index.write_tree().map_err(|e| git2_to_io_error(&e))?;
    let tree = repo.find_tree(tree_oid).map_err(|e| git2_to_io_error(&e))?;

    let GitIdentity { name, email } =
        resolve_commit_identity(repo, git_user_name, git_user_email, executor, env);

    let real_env = env.unwrap_or(&crate::runtime::environment::RealEnvironment);
    log_identity_if_debug(repo, &name, &email, git_user_name, git_user_email, real_env);

    let sig = git2::Signature::now(&name, &email).map_err(|e| git2_to_io_error(&e))?;
    commit_with_head(repo, &sig, message, &tree)
}

fn tree_has_entries(tree: &git2::Tree<'_>) -> bool {
    tree.iter().next().is_some()
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    fn tree_has_entries_for_paths(paths: &[&str]) -> bool {
        let repo_dir = tempfile::TempDir::new().expect("create temp git repo dir");
        let repo = git2::Repository::init(repo_dir.path()).expect("init repo");
        let mut index = repo.index().expect("open index");

        paths.iter().for_each(|path| {
            let absolute_path = repo_dir.path().join(path);
            if let Some(parent) = absolute_path.parent() {
                std::fs::create_dir_all(parent).expect("create parent dirs");
            }
            std::fs::write(&absolute_path, "content\n").expect("write file");
            index.add_path(Path::new(path)).expect("stage file path");
        });

        index.write().expect("write index");
        let tree_oid = index.write_tree().expect("write tree");
        let tree = repo.find_tree(tree_oid).expect("find tree");
        super::tree_has_entries(&tree)
    }

    /// Regression test: the policy guard in `check_repo_not_project_repo` must
    /// fire when the repo root equals the project root. This prevents the
    /// historical bug where tests created real commits ("feat: add new file
    /// flow") in the development repository, reverting developer changes.
    ///
    /// The guard identifies the project root by looking for `CLAUDE.md` and
    /// `PROMPT.md` marker files, then panics if `repo_root` is at or under
    /// that path.
    #[test]
    fn policy_violation_fires_on_project_repo_commit_attempt() {
        let project_root = super::find_project_root_containing_markers();
        let Some(project_root) = project_root else {
            // Cannot locate project root (e.g. on remote build server) — skip.
            return;
        };

        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            super::assert_not_project_repo_for_commit(&project_root);
        }));

        assert!(
            result.is_err(),
            "assert_not_project_repo_for_commit must panic when given the project \
             repo root — this guard prevents the historical 'feat: add new file flow' \
             bug where tests mutated the development repository"
        );

        // Verify the panic message references the policy violation.
        let panic_msg = result
            .unwrap_err()
            .downcast_ref::<String>()
            .cloned()
            .unwrap_or_default();
        assert!(
            panic_msg.contains("POLICY VIOLATION"),
            "panic message must contain 'POLICY VIOLATION', got: {}",
            panic_msg
        );
    }

    /// A subdirectory of the project root must also be rejected — the policy
    /// guard uses `starts_with` to catch nested paths.
    #[test]
    fn policy_violation_fires_on_project_subdirectory() {
        let project_root = super::find_project_root_containing_markers();
        let Some(project_root) = project_root else {
            return;
        };

        let subdirectory = project_root.join("some-nested-path");
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            super::check_repo_not_project_repo(&subdirectory, &project_root);
        }));

        assert!(
            result.is_err(),
            "check_repo_not_project_repo must panic for paths under the project root"
        );
    }

    /// A temp directory must NOT trigger the policy guard — isolated repos are safe.
    #[test]
    fn policy_guard_allows_temp_directory() {
        let project_root = super::find_project_root_containing_markers();
        let Some(project_root) = project_root else {
            return;
        };

        let temp_dir = tempfile::TempDir::new().expect("create temp dir");
        // Must not panic.
        super::check_repo_not_project_repo(temp_dir.path(), &project_root);
    }

    #[test]
    fn tree_has_entries_returns_false_for_empty_tree() {
        assert!(!tree_has_entries_for_paths(&[]));
    }

    #[test]
    fn tree_has_entries_returns_true_for_non_empty_tree() {
        assert!(tree_has_entries_for_paths(&["src/example.rs"]));
    }
}
