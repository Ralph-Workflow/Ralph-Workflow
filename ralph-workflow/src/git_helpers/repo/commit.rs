use std::path::Path;

use crate::git_helpers::git2_to_io_error;
use crate::git_helpers::identity::GitIdentity;
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
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => Ok(!index.is_empty()),
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
    let repo = git2::Repository::discover(repo_root).map_err(|e| git2_to_io_error(&e))?;
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
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => {
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
            Err(ref e) if e.code() == git2::ErrorCode::NotFound => {
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

/// Stage all changes.
///
/// Similar to `git add -A`.
///
/// # Returns
///
/// Returns `Ok(true)` if files were successfully staged, `Ok(false)` if there
/// were no files to stage, or an error if staging failed.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_add_all() -> std::io::Result<bool> {
    git_add_all_in_repo(Path::new("."))
}

/// Stage all changes in the repository discovered from `repo_root`.
///
/// This avoids relying on process-wide CWD and allows callers (including tests)
/// to control which repository is targeted.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_add_all_in_repo(repo_root: &Path) -> std::io::Result<bool> {
    let repo = git2::Repository::discover(repo_root).map_err(|e| git2_to_io_error(&e))?;
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

fn resolve_commit_identity(
    repo: &git2::Repository,
    provided_name: Option<&str>,
    provided_email: Option<&str>,
    executor: Option<&dyn crate::executor::ProcessExecutor>,
    env: Option<&dyn crate::runtime::environment::Environment>,
) -> GitIdentity {
    use crate::git_helpers::identity::{default_identity, fallback_email, fallback_username};

    let env = env.unwrap_or(&crate::runtime::environment::RealEnvironment);

    let mut name = String::new();
    let mut email = String::new();
    let mut has_git_config = false;

    if let Ok(sig) = repo.signature() {
        let git_name = sig.name().unwrap_or("");
        let git_email = sig.email().unwrap_or("");
        if !git_name.is_empty() && !git_email.is_empty() {
            name = git_name.to_string();
            email = git_email.to_string();
            has_git_config = true;
        }
    }

    let env_name = env.var("RALPH_GIT_USER_NAME");
    let env_email = env.var("RALPH_GIT_USER_EMAIL");

    // Apply in priority order: git config > provided args > env vars.
    let final_name = if has_git_config && !name.is_empty() {
        name.as_str()
    } else {
        provided_name
            .filter(|s| !s.is_empty())
            .or(env_name.as_deref())
            .filter(|s| !s.is_empty())
            .unwrap_or("")
    };

    let final_email = if has_git_config && !email.is_empty() {
        email.as_str()
    } else {
        provided_email
            .filter(|s| !s.is_empty())
            .or(env_email.as_deref())
            .filter(|s| !s.is_empty())
            .unwrap_or("")
    };

    if !final_name.is_empty() && !final_email.is_empty() {
        let identity = GitIdentity::new(final_name.to_string(), final_email.to_string());
        if identity.validate().is_ok() {
            return identity;
        }
    }

    let username = fallback_username(executor);
    let system_email = fallback_email(&username, executor);
    let identity = GitIdentity::new(
        if final_name.is_empty() {
            username
        } else {
            final_name.to_string()
        },
        if final_email.is_empty() {
            system_email
        } else {
            final_email.to_string()
        },
    );

    if identity.validate().is_ok() {
        return identity;
    }

    default_identity()
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
pub fn git_commit(
    message: &str,
    git_user_name: Option<&str>,
    git_user_email: Option<&str>,
    executor: Option<&dyn crate::executor::ProcessExecutor>,
    env: Option<&dyn crate::runtime::environment::Environment>,
) -> std::io::Result<Option<git2::Oid>> {
    git_commit_in_repo(
        Path::new("."),
        message,
        git_user_name,
        git_user_email,
        executor,
        env,
    )
}

/// Create a commit in the repository discovered from `repo_root`.
///
/// This avoids relying on process-wide CWD and allows callers to select the
/// repository to operate on.
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
    let repo = git2::Repository::discover(repo_root).map_err(|e| git2_to_io_error(&e))?;
    git_commit_impl(&repo, message, git_user_name, git_user_email, executor, env)
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

    // Debug logging: identity resolution source.
    if env
        .unwrap_or(&crate::runtime::environment::RealEnvironment)
        .var("RALPH_DEBUG")
        .is_some()
    {
        let identity_source = if git_user_name.is_some() || git_user_email.is_some() {
            "CLI/config override"
        } else if env
            .unwrap_or(&crate::runtime::environment::RealEnvironment)
            .var("RALPH_GIT_USER_NAME")
            .is_some()
            || env
                .unwrap_or(&crate::runtime::environment::RealEnvironment)
                .var("RALPH_GIT_USER_EMAIL")
                .is_some()
        {
            "environment variable"
        } else if repo.signature().is_ok() {
            "git config"
        } else {
            "system/default"
        };
        let _ = std::io::Write::write_fmt(
            &mut std::io::stderr(),
            format_args!("Git identity: {name} <{email}> (source: {identity_source})\n"),
        );
    }

    let sig = git2::Signature::now(&name, &email).map_err(|e| git2_to_io_error(&e))?;

    let oid = match repo.head() {
        Ok(head) => {
            let head_commit = head.peel_to_commit().map_err(|e| git2_to_io_error(&e))?;
            repo.commit(Some("HEAD"), &sig, &sig, message, &tree, &[&head_commit])
        }
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => {
            let mut has_entries = false;
            tree.walk(git2::TreeWalkMode::PreOrder, |_, _| {
                has_entries = true;
                1 // Stop iteration after first entry.
            })
            .ok();

            if !has_entries {
                return Ok(None);
            }
            repo.commit(Some("HEAD"), &sig, &sig, message, &tree, &[])
        }
        Err(e) => return Err(git2_to_io_error(&e)),
    }
    .map_err(|e| git2_to_io_error(&e))?;

    Ok(Some(oid))
}
