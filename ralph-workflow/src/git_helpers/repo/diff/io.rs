// git_helpers/repo/diff/io.rs — boundary module for git diff I/O primitives.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.
//
// This module contains ONLY thin I/O wrappers around git2. All policy and
// orchestration (which baseline to use, workspace checks, start-commit logic,
// handling of unborn-branch vs real error) lives in the parent `diff.rs` module.

use std::path::Path;

use crate::git_helpers::git2_to_io_error;

/// Result of resolving the HEAD tree OID.
///
/// Returned by [`resolve_head_tree_oid`] to let domain code decide the diff strategy
/// without the boundary needing to branch on the git2 error code.
pub(super) enum HeadTreeOid {
    /// HEAD resolved to a valid tree identified by this OID.
    Tree(git2::Oid),
    /// Repository has no commits yet (unborn branch).
    UnbornBranch,
}

pub(super) fn configured_diff_options() -> git2::DiffOptions {
    let mut diff_opts = git2::DiffOptions::new();
    diff_opts.include_untracked(true);
    diff_opts.recurse_untracked_dirs(true);
    diff_opts
}

/// Discover a git repository from the given path.
///
/// # Errors
///
/// Returns error if discovery fails.
pub(super) fn discover_repo(from: &Path) -> std::io::Result<git2::Repository> {
    git2::Repository::discover(from).map_err(|e| git2_to_io_error(&e))
}

/// Resolve the HEAD tree OID, distinguishing between unborn branch and real errors.
///
/// Returns [`HeadTreeOid`] so domain code can decide the diff strategy without
/// the boundary needing to branch on git2 error codes in the returned type.
///
/// `git2_to_io_error` maps `UnbornBranch` → `NotFound`, so an empty repository
/// is classified as `UnbornBranch` without inspecting git2 error codes here.
///
/// # Errors
///
/// Returns error only for genuine failures (not for unborn-branch, which is `Ok(UnbornBranch)`).
pub(super) fn resolve_head_tree_oid(repo: &git2::Repository) -> std::io::Result<HeadTreeOid> {
    repo.head()
        .map_err(|e| git2_to_io_error(&e))
        .and_then(|head| {
            head.peel_to_tree()
                .map_err(|e| git2_to_io_error(&e))
                .map(|tree| HeadTreeOid::Tree(tree.id()))
        })
        .or_else(|io_err| {
            if io_err.kind() == std::io::ErrorKind::NotFound {
                Ok(HeadTreeOid::UnbornBranch)
            } else {
                Err(io_err)
            }
        })
}

/// Generate a diff between a specific commit tree and the current working directory.
///
/// # Errors
///
/// Returns error if the operation fails.
pub(super) fn diff_from_oid_impl(
    repo: &git2::Repository,
    oid: git2::Oid,
) -> std::io::Result<String> {
    let start_commit = repo.find_commit(oid).map_err(|e| git2_to_io_error(&e))?;
    let start_tree = start_commit.tree().map_err(|e| git2_to_io_error(&e))?;
    diff_tree_to_workdir(repo, Some(&start_tree))
}

/// Generate a diff from the tree identified by a given OID and the current working directory.
///
/// # Errors
///
/// Returns error if the operation fails.
pub(super) fn diff_from_tree_oid_impl(
    repo: &git2::Repository,
    tree_oid: git2::Oid,
) -> std::io::Result<String> {
    let tree = repo.find_tree(tree_oid).map_err(|e| git2_to_io_error(&e))?;
    diff_tree_to_workdir(repo, Some(&tree))
}

/// Generate a diff from the empty tree (initial commit case).
///
/// # Errors
///
/// Returns error if the operation fails.
pub(super) fn diff_from_empty_tree_impl(repo: &git2::Repository) -> std::io::Result<String> {
    diff_tree_to_workdir(repo, None)
}

/// Generate a diff between an optional tree and the current working directory.
///
/// `tree = None` means diff against the empty tree (initial commit case).
///
/// # Errors
///
/// Returns error if the operation fails.
pub(super) fn diff_tree_to_workdir(
    repo: &git2::Repository,
    tree: Option<&git2::Tree<'_>>,
) -> std::io::Result<String> {
    let mut diff_opts = configured_diff_options();
    let diff = repo
        .diff_tree_to_workdir_with_index(tree, Some(&mut diff_opts))
        .map_err(|e| git2_to_io_error(&e))?;

    let mut output = String::new();
    diff.print(
        git2::DiffFormat::Patch,
        &mut |_delta: git2::DiffDelta<'_>,
              _hunk: Option<git2::DiffHunk<'_>>,
              line: git2::DiffLine<'_>| {
            if let Ok(content) = std::str::from_utf8(line.content()) {
                output.push_str(content);
            }
            true
        },
    )
    .map_err(|e| git2_to_io_error(&e))?;

    Ok(output)
}
