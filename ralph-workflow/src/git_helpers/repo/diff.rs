use std::io;
use std::path::Path;

use crate::git_helpers::git2_to_io_error;
use crate::workspace::Workspace;

/// Get the diff of all changes (unstaged and staged).
///
/// Returns a formatted diff string suitable for LLM analysis.
/// This is similar to `git diff HEAD`.
///
/// Handles the case of an empty repository (no commits yet) by
/// diffing against an empty tree using a read-only approach.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_diff() -> io::Result<String> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    git_diff_impl(&repo)
}

/// Get the diff of all changes (unstaged and staged) by discovering from an explicit path.
///
/// This avoids coupling diff generation to the process current working directory.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_diff_in_repo(repo_root: &Path) -> io::Result<String> {
    let repo = git2::Repository::discover(repo_root).map_err(|e| git2_to_io_error(&e))?;
    git_diff_impl(&repo)
}

/// Generate a diff from a specific starting commit.
///
/// Takes a starting commit OID and generates a diff between that commit
/// and the current working tree. Returns a formatted diff string suitable
/// for LLM analysis.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn git_diff_from(start_oid: &str) -> io::Result<String> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;

    // Parse the starting OID.
    let oid = git2::Oid::from_str(start_oid).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("Invalid commit OID: {start_oid}"),
        )
    })?;

    git_diff_from_oid(&repo, oid)
}

/// Get the git diff from the starting commit.
///
/// Uses the saved starting commit from `.agent/start_commit` to generate
/// an incremental diff. Falls back to diffing from HEAD if no start commit
/// file exists.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_git_diff_from_start() -> io::Result<String> {
    use crate::git_helpers::start_commit::{load_start_point, save_start_commit, StartPoint};

    // Ensure a valid starting point exists. This is expected to persist across runs,
    // but we also repair missing/corrupt files opportunistically for robustness.
    save_start_commit()?;

    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;

    match load_start_point()? {
        StartPoint::Commit(oid) => git_diff_from(&oid.to_string()),
        StartPoint::EmptyRepo => git_diff_from_empty_tree(&repo),
    }
}

/// Get the git diff from the starting commit (workspace-aware).
///
/// This uses `.agent/start_commit` as the baseline and generates a diff between that baseline
/// and the current state on disk, including staged + unstaged changes and untracked files.
///
/// Unlike [`get_git_diff_from_start`], this does not rely on the process CWD.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_git_diff_from_start_with_workspace(workspace: &dyn Workspace) -> io::Result<String> {
    use crate::git_helpers::start_commit::{
        load_start_point_with_workspace, save_start_commit_with_workspace, StartPoint,
    };

    // Fast path: if the workspace has no on-disk .git, refuse to emit a diff.
    // This ensures MemoryWorkspace and other in-memory workspaces never accidentally
    // leak into the process CWD's git repository.
    if !workspace.exists(std::path::Path::new(".git")) {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "Workspace has no on-disk git repository",
        ));
    }

    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;

    // Ensure a valid start point exists. This is expected to persist across runs, but we also
    // repair missing/corrupt files opportunistically for robustness.
    save_start_commit_with_workspace(workspace, &repo)?;

    match load_start_point_with_workspace(workspace, &repo)? {
        StartPoint::Commit(oid) => git_diff_from_oid(&repo, oid),
        StartPoint::EmptyRepo => git_diff_from_empty_tree(&repo),
    }
}

/// Get the diff content that should be shown to reviewers.
///
/// Baseline selection:
/// - If `.agent/review_baseline.txt` is set, diff from that commit.
/// - Otherwise, diff from `.agent/start_commit` (the initial pipeline baseline).
///
/// The diff is always generated against the current state on disk (staged + unstaged + untracked).
///
/// Returns `(diff, baseline_oid_for_prompts)` where `baseline_oid_for_prompts` is the commit hash
/// to mention in fallback instructions (or empty for empty repo baseline).
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_git_diff_for_review_with_workspace(
    workspace: &dyn Workspace,
) -> io::Result<(String, String)> {
    use crate::git_helpers::review_baseline::{
        load_review_baseline_with_workspace, ReviewBaseline,
    };
    use crate::git_helpers::start_commit::{
        load_start_point_with_workspace, save_start_commit_with_workspace, StartPoint,
    };

    // NOTE: We discover the repo from CWD here because the `ReviewBaseline` and `start_commit`
    // files live in the injected Workspace, but the diff itself must be generated from the real
    // on-disk git repository.
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;

    let baseline = load_review_baseline_with_workspace(workspace).unwrap_or(ReviewBaseline::NotSet);
    match baseline {
        ReviewBaseline::Commit(oid) => {
            let diff = git_diff_from_oid(&repo, oid)?;
            Ok((diff, oid.to_string()))
        }
        ReviewBaseline::NotSet => {
            // Ensure a valid start point exists.
            save_start_commit_with_workspace(workspace, &repo)?;

            match load_start_point_with_workspace(workspace, &repo)? {
                StartPoint::Commit(oid) => {
                    let diff = git_diff_from_oid(&repo, oid)?;
                    Ok((diff, oid.to_string()))
                }
                StartPoint::EmptyRepo => Ok((git_diff_from_empty_tree(&repo)?, String::new())),
            }
        }
    }
}

/// Implementation of git diff.
fn git_diff_impl(repo: &git2::Repository) -> io::Result<String> {
    let mut output = String::new();

    let print_cb = &mut |_delta: git2::DiffDelta<'_>,
                         _hunk: Option<git2::DiffHunk<'_>>,
                         line: git2::DiffLine<'_>| {
        if let Ok(content) = std::str::from_utf8(line.content()) {
            output.push_str(content);
        }
        true
    };

    // Try to get HEAD tree.
    let head_tree = match repo.head() {
        Ok(head) => Some(head.peel_to_tree().map_err(|e| git2_to_io_error(&e))?),
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => {
            // No commits yet: diff an empty tree against the workdir.
            let mut diff_opts = git2::DiffOptions::new();
            diff_opts.include_untracked(true);
            diff_opts.recurse_untracked_dirs(true);

            let diff = repo
                .diff_tree_to_workdir_with_index(None, Some(&mut diff_opts))
                .map_err(|e| git2_to_io_error(&e))?;

            diff.print(git2::DiffFormat::Patch, print_cb)
                .map_err(|e| git2_to_io_error(&e))?;

            return Ok(output);
        }
        Err(e) => return Err(git2_to_io_error(&e)),
    };

    // For repos with commits, diff HEAD against working tree (staged + unstaged + untracked).
    let mut diff_opts = git2::DiffOptions::new();
    diff_opts.include_untracked(true);
    diff_opts.recurse_untracked_dirs(true);

    let diff = repo
        .diff_tree_to_workdir_with_index(head_tree.as_ref(), Some(&mut diff_opts))
        .map_err(|e| git2_to_io_error(&e))?;

    diff.print(git2::DiffFormat::Patch, print_cb)
        .map_err(|e| git2_to_io_error(&e))?;

    Ok(output)
}

fn git_diff_from_oid(repo: &git2::Repository, oid: git2::Oid) -> io::Result<String> {
    let start_commit = repo.find_commit(oid).map_err(|e| git2_to_io_error(&e))?;
    let start_tree = start_commit.tree().map_err(|e| git2_to_io_error(&e))?;

    let mut diff_opts = git2::DiffOptions::new();
    diff_opts.include_untracked(true);
    diff_opts.recurse_untracked_dirs(true);

    let diff = repo
        .diff_tree_to_workdir_with_index(Some(&start_tree), Some(&mut diff_opts))
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

/// Generate a diff from the empty tree (initial commit).
fn git_diff_from_empty_tree(repo: &git2::Repository) -> io::Result<String> {
    let mut diff_opts = git2::DiffOptions::new();
    diff_opts.include_untracked(true);
    diff_opts.recurse_untracked_dirs(true);

    let diff = repo
        .diff_tree_to_workdir_with_index(None, Some(&mut diff_opts))
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
