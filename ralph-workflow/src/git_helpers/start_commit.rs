//! Starting commit tracking for incremental diff generation.
//!
//! This module manages the starting commit reference that enables incremental
//! diffs for reviewers while keeping agents isolated from git history.
//!
//! # Overview
//!
//! When a Ralph pipeline starts, the current HEAD commit is saved as the
//! "starting commit" in `.agent/start_commit`. This reference is used to:
//!
//! 1. Generate incremental diffs for reviewers (changes since pipeline start)
//! 2. Keep agents unaware of git history (no git context in prompts)
//! 3. Enable proper review of accumulated changes across iterations
//!
//! The starting commit file persists across pipeline runs unless explicitly
//! reset by the user via the `--reset-start-commit` CLI command.

use std::io;
use std::path::Path;

use crate::workspace::{Workspace, WorkspaceFs};

/// Path to the starting commit file.
///
/// Stored in `.agent/start_commit`, this file contains the OID (SHA) of the
/// commit that was HEAD when the pipeline started.
const START_COMMIT_FILE: &str = ".agent/start_commit";

/// Sentinel value written to `.agent/start_commit` when the repository has no commits yet.
///
/// This enables incremental diffs to work in a single run that starts on an unborn HEAD
/// by treating the starting point as the empty tree.
const EMPTY_REPO_SENTINEL: &str = "__EMPTY_REPO__";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StartPoint {
    /// A concrete commit OID to diff from.
    Commit(git2::Oid),
    /// An empty repository baseline (diff from the empty tree).
    EmptyRepo,
}

/// Get the current HEAD commit OID.
///
/// Returns the full SHA-1 hash of the current HEAD commit.
///
/// # Errors
///
/// Returns an error if:
/// - Not in a git repository
/// - HEAD cannot be resolved (e.g., unborn branch)
/// - HEAD is not a commit (e.g., symbolic ref to tag)
///
/// **Note:** This function uses the current working directory to discover the repo.
pub fn get_current_head_oid() -> io::Result<String> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    get_current_head_oid_impl(&repo)
}

/// Get the current HEAD commit OID for an explicit repository root.
///
/// This avoids accidentally discovering a different repository when the process
/// current working directory is not inside `repo_root`.
///
/// # Errors
///
/// Returns an error if the repository cannot be opened or HEAD cannot be resolved.
pub fn get_current_head_oid_at(repo_root: &Path) -> io::Result<String> {
    let repo = git2::Repository::open(repo_root).map_err(|e| to_io_error(&e))?;
    get_current_head_oid_impl(&repo)
}

/// Implementation of `get_current_head_oid`.
fn get_current_head_oid_impl(repo: &git2::Repository) -> io::Result<String> {
    let head = repo.head().map_err(|e| {
        // Handle UnbornBranch error consistently with git_diff()
        // This provides a clearer error message for empty repositories
        if e.code() == git2::ErrorCode::UnbornBranch {
            io::Error::new(io::ErrorKind::NotFound, "No commits yet (unborn branch)")
        } else {
            to_io_error(&e)
        }
    })?;

    // Get the commit OID
    let head_commit = head.peel_to_commit().map_err(|e| to_io_error(&e))?;

    Ok(head_commit.id().to_string())
}

fn get_current_start_point(repo: &git2::Repository) -> io::Result<StartPoint> {
    let head = repo.head();
    let start_point = match head {
        Ok(head) => {
            let head_commit = head.peel_to_commit().map_err(|e| to_io_error(&e))?;
            StartPoint::Commit(head_commit.id())
        }
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => StartPoint::EmptyRepo,
        Err(e) => return Err(to_io_error(&e)),
    };
    Ok(start_point)
}

/// Save the current HEAD commit as the starting commit.
///
/// Writes the current HEAD OID to `.agent/start_commit` only if it doesn't
/// already exist. This ensures the `start_commit` persists across pipeline runs
/// and is only reset when explicitly requested via `--reset-start-commit`.
///
/// # Errors
///
/// Returns an error if:
/// - The current HEAD cannot be determined
/// - The `.agent` directory cannot be created
/// - The file cannot be written
///
pub fn save_start_commit() -> io::Result<()> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    let repo_root = repo
        .workdir()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No workdir for repository"))?;
    save_start_commit_impl(&repo, repo_root)
}

/// Implementation of `save_start_commit`.
fn save_start_commit_impl(repo: &git2::Repository, repo_root: &Path) -> io::Result<()> {
    // If a start commit exists and is valid, preserve it across runs.
    // If it exists but is invalid/corrupt, automatically repair it.
    if load_start_point_impl(repo, repo_root).is_ok() {
        return Ok(());
    }

    write_start_point(repo_root, get_current_start_point(repo)?)
}

fn write_start_commit_with_oid(repo_root: &Path, oid: &str) -> io::Result<()> {
    let workspace = WorkspaceFs::new(repo_root.to_path_buf());
    workspace.write(Path::new(START_COMMIT_FILE), oid)
}

fn write_start_point(repo_root: &Path, start_point: StartPoint) -> io::Result<()> {
    let content = match start_point {
        StartPoint::Commit(oid) => oid.to_string(),
        StartPoint::EmptyRepo => EMPTY_REPO_SENTINEL.to_string(),
    };
    let workspace = WorkspaceFs::new(repo_root.to_path_buf());
    workspace.write(Path::new(START_COMMIT_FILE), &content)
}

/// Write start point to file using workspace abstraction.
///
/// This is the workspace-aware version that should be used in pipeline code
/// where a workspace is available.
fn write_start_point_with_workspace(
    workspace: &dyn Workspace,
    start_point: StartPoint,
) -> io::Result<()> {
    let path = Path::new(START_COMMIT_FILE);
    let content = match start_point {
        StartPoint::Commit(oid) => oid.to_string(),
        StartPoint::EmptyRepo => EMPTY_REPO_SENTINEL.to_string(),
    };
    workspace.write(path, &content)
}

/// Load start point from file using workspace abstraction.
///
/// This version reads the file content via workspace but still validates
/// the commit exists using the provided repository.
///
/// This is the workspace-aware version for pipeline code where git operations
/// are needed for validation.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn load_start_point_with_workspace(
    workspace: &dyn Workspace,
    repo: &git2::Repository,
) -> io::Result<StartPoint> {
    let path = Path::new(START_COMMIT_FILE);
    let content = workspace.read(path)?;
    let raw = content.trim();

    if raw.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Starting commit file is empty. Run 'ralph --reset-start-commit' to fix.",
        ));
    }

    if raw == EMPTY_REPO_SENTINEL {
        return Ok(StartPoint::EmptyRepo);
    }

    // Validate OID format using libgit2.
    let oid = git2::Oid::from_str(raw).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "Invalid OID format in {START_COMMIT_FILE}: '{raw}'. Run 'ralph --reset-start-commit' to fix."
            ),
        )
    })?;

    // Ensure the commit still exists in this repository.
    repo.find_commit(oid).map_err(|e| {
        let err_msg = e.message();
        if err_msg.contains("not found") || err_msg.contains("invalid") {
            io::Error::new(
                io::ErrorKind::NotFound,
                format!(
                    "Start commit '{raw}' no longer exists (history rewritten). \
                     Run 'ralph --reset-start-commit' to fix."
                ),
            )
        } else {
            to_io_error(&e)
        }
    })?;

    Ok(StartPoint::Commit(oid))
}

/// Save start commit using workspace abstraction.
///
/// This is the workspace-aware version for pipeline code where a workspace
/// is available. If a valid start commit already exists, it is preserved.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn save_start_commit_with_workspace(
    workspace: &dyn Workspace,
    repo: &git2::Repository,
) -> io::Result<()> {
    // If a start commit exists and is valid, preserve it across runs.
    if load_start_point_with_workspace(workspace, repo).is_ok() {
        return Ok(());
    }

    write_start_point_with_workspace(workspace, get_current_start_point(repo)?)
}

/// Load the starting commit OID from the file.
///
/// Reads the `.agent/start_commit` file and returns the stored OID.
///
/// # Errors
///
/// Returns an error if:
/// - The file does not exist
/// - The file cannot be read
/// - The file content is invalid
///
pub fn load_start_point() -> io::Result<StartPoint> {
    let repo = git2::Repository::discover(".").map_err(|e| {
        io::Error::new(
            io::ErrorKind::NotFound,
            format!("Git repository error: {e}. Run 'ralph --reset-start-commit' to fix."),
        )
    })?;
    let repo_root = repo
        .workdir()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No workdir for repository"))?;
    load_start_point_impl(&repo, repo_root)
}

/// Implementation of `load_start_point`.
fn load_start_point_impl(repo: &git2::Repository, repo_root: &Path) -> io::Result<StartPoint> {
    let workspace = WorkspaceFs::new(repo_root.to_path_buf());
    load_start_point_with_workspace(&workspace, repo)
}

/// Result of resetting the start commit.
#[derive(Debug, Clone)]
pub struct ResetStartCommitResult {
    /// The OID that `start_commit` was set to.
    pub oid: String,
    /// The default branch used for merge-base calculation (if applicable).
    pub default_branch: Option<String>,
    /// Whether we fell back to HEAD (when on main/master branch).
    pub fell_back_to_head: bool,
}

/// Reset the starting commit to merge-base with the default branch.
///
/// This is a CLI command that updates `.agent/start_commit` to the merge-base
/// between HEAD and the default branch (main/master). This provides a better
/// baseline for feature branch workflows, showing only changes since branching.
///
/// If the current branch is main/master itself, falls back to current HEAD.
///
/// # Errors
///
/// Returns an error if:
/// - The current HEAD cannot be determined
/// - The default branch cannot be found
/// - No common ancestor exists between HEAD and the default branch
/// - The file cannot be written
///
pub fn reset_start_commit() -> io::Result<ResetStartCommitResult> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    let repo_root = repo
        .workdir()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No workdir for repository"))?;
    reset_start_commit_impl(&repo, repo_root)
}

/// Implementation of `reset_start_commit`.
fn reset_start_commit_impl(
    repo: &git2::Repository,
    repo_root: &Path,
) -> io::Result<ResetStartCommitResult> {
    // Get current HEAD
    let head = repo.head().map_err(|e| {
        if e.code() == git2::ErrorCode::UnbornBranch {
            io::Error::new(io::ErrorKind::NotFound, "No commits yet (unborn branch)")
        } else {
            to_io_error(&e)
        }
    })?;
    let head_commit = head.peel_to_commit().map_err(|e| to_io_error(&e))?;

    // Check if we're on main/master - if so, fall back to HEAD
    let current_branch = head.shorthand().unwrap_or("HEAD");
    if current_branch == "main" || current_branch == "master" {
        let oid = head_commit.id().to_string();
        write_start_commit_with_oid(repo_root, &oid)?;
        return Ok(ResetStartCommitResult {
            oid,
            default_branch: None,
            fell_back_to_head: true,
        });
    }

    // Get the default branch
    let default_branch = super::branch::get_default_branch_at(repo_root)?;

    // Find the default branch commit
    let default_ref = format!("refs/heads/{default_branch}");
    let default_commit = if let Ok(reference) = repo.find_reference(&default_ref) {
        reference.peel_to_commit().map_err(|e| to_io_error(&e))?
    } else {
        // Try origin/<default_branch> as fallback
        let origin_ref = format!("refs/remotes/origin/{default_branch}");
        match repo.find_reference(&origin_ref) {
            Ok(reference) => reference.peel_to_commit().map_err(|e| to_io_error(&e))?,
            Err(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!(
                        "Default branch '{default_branch}' not found locally or in origin. \
                         Make sure the branch exists."
                    ),
                ));
            }
        }
    };

    // Calculate merge-base
    let merge_base = repo
        .merge_base(head_commit.id(), default_commit.id())
        .map_err(|e| {
            if e.code() == git2::ErrorCode::NotFound {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    format!(
                        "No common ancestor between current branch and '{default_branch}' (unrelated branches)"
                    ),
                )
            } else {
                to_io_error(&e)
            }
        })?;

    let oid = merge_base.to_string();
    write_start_commit_with_oid(repo_root, &oid)?;

    Ok(ResetStartCommitResult {
        oid,
        default_branch: Some(default_branch),
        fell_back_to_head: false,
    })
}

/// Start commit summary for display.
///
/// Contains information about the start commit for user display.
#[derive(Debug, Clone)]
pub struct StartCommitSummary {
    /// The start commit OID (short form, or None if not set).
    pub start_oid: Option<String>,
    /// Number of commits since start commit.
    pub commits_since: usize,
    /// Whether the start commit is stale (>10 commits behind).
    pub is_stale: bool,
}

impl StartCommitSummary {
    /// Format a compact version for inline display.
    pub fn format_compact(&self) -> String {
        self.start_oid.as_ref().map_or_else(
            || "Start: not set".to_string(),
            |oid| {
                let short_oid = &oid[..8.min(oid.len())];
                if self.is_stale {
                    format!(
                        "Start: {} (+{} commits, STALE)",
                        short_oid, self.commits_since
                    )
                } else if self.commits_since > 0 {
                    format!("Start: {} (+{} commits)", short_oid, self.commits_since)
                } else {
                    format!("Start: {short_oid}")
                }
            },
        )
    }
}

/// Get a summary of the start commit state for display.
///
/// Returns a `StartCommitSummary` containing information about the current
/// start commit, commits since start, and staleness status.
///
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_start_commit_summary() -> io::Result<StartCommitSummary> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    let repo_root = repo
        .workdir()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No workdir for repository"))?;
    get_start_commit_summary_impl(&repo, repo_root)
}

/// Implementation of `get_start_commit_summary`.
fn get_start_commit_summary_impl(
    repo: &git2::Repository,
    repo_root: &Path,
) -> io::Result<StartCommitSummary> {
    let start_oid = match load_start_point_impl(repo, repo_root)? {
        StartPoint::Commit(oid) => Some(oid.to_string()),
        StartPoint::EmptyRepo => None,
    };

    let (commits_since, is_stale) = if let Some(ref oid) = start_oid {
        // Get HEAD commit
        let head_oid = get_current_head_oid_impl(repo)?;
        let head_commit = repo
            .find_commit(git2::Oid::from_str(&head_oid).map_err(|_| {
                io::Error::new(io::ErrorKind::InvalidData, "Invalid HEAD OID format")
            })?)
            .map_err(|e| to_io_error(&e))?;

        let start_commit_oid = git2::Oid::from_str(oid)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Invalid start OID format"))?;

        let start_commit = repo
            .find_commit(start_commit_oid)
            .map_err(|e| to_io_error(&e))?;

        // Count commits between start and HEAD
        let mut revwalk = repo.revwalk().map_err(|e| to_io_error(&e))?;
        revwalk
            .push(head_commit.id())
            .map_err(|e| to_io_error(&e))?;

        let count = revwalk
            .map(|res| res.map_err(|e| to_io_error(&e)))
            .take_while(|res| res.as_ref().map_or(true, |id| *id != start_commit.id()))
            .take(1000)
            .count();

        let is_stale = count > 10;
        (count, is_stale)
    } else {
        (0, false)
    };

    Ok(StartCommitSummary {
        start_oid,
        commits_since,
        is_stale,
    })
}

#[cfg(test)]
fn has_start_commit() -> bool {
    load_start_point().is_ok()
}

/// Convert git2 error to `io::Error`.
fn to_io_error(err: &git2::Error) -> io::Error {
    io::Error::other(err.to_string())
}

#[cfg(test)]
mod tests;
