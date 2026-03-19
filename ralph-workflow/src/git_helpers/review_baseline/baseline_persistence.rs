// Review baseline persistence, parsing, and commit-distance calculation.

/// Path to the review baseline file.
///
/// Stored in `.agent/review_baseline.txt`, this file contains the OID (SHA)
/// for the baseline commit used for per-review-cycle diffs.
pub const REVIEW_BASELINE_FILE: &str = ".agent/review_baseline.txt";

/// Sentinel value for "baseline not set".
///
/// This is written to the baseline file when a baseline cannot be determined
/// (e.g., empty repository / unborn HEAD) or when explicitly cleared.
pub const BASELINE_NOT_SET: &str = "__BASELINE_NOT_SET__";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReviewBaseline {
    /// A concrete commit OID to diff from.
    Commit(git2::Oid),
    /// No baseline set; fall back to `start_commit`.
    NotSet,
}

/// Load the review baseline from the working directory.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn load_review_baseline() -> io::Result<ReviewBaseline> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    let repo_root = repo
        .workdir()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No workdir for repository"))?;
    let workspace = WorkspaceFs::new(repo_root.to_path_buf());
    load_review_baseline_with_workspace(&workspace)
}

/// Load the review baseline using the workspace abstraction.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn load_review_baseline_with_workspace(
    workspace: &dyn Workspace,
) -> io::Result<ReviewBaseline> {
    let path = Path::new(REVIEW_BASELINE_FILE);
    if !workspace.exists(path) {
        return Ok(ReviewBaseline::NotSet);
    }

    let content = workspace.read(path)?;
    let raw = content.trim();

    if raw.is_empty() || raw == BASELINE_NOT_SET {
        return Ok(ReviewBaseline::NotSet);
    }

    let oid = git2::Oid::from_str(raw).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Invalid baseline OID in {REVIEW_BASELINE_FILE}: '{raw}'"),
        )
    })?;

    Ok(ReviewBaseline::Commit(oid))
}

/// Update the review baseline to the current HEAD.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn update_review_baseline() -> io::Result<()> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    let repo_root = repo
        .workdir()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No workdir for repository"))?;
    let workspace = WorkspaceFs::new(repo_root.to_path_buf());
    update_review_baseline_with_workspace(&workspace)
}

/// Update the review baseline using the workspace abstraction.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn update_review_baseline_with_workspace(workspace: &dyn Workspace) -> io::Result<()> {
    let path = Path::new(REVIEW_BASELINE_FILE);
    match get_current_head_oid() {
        Ok(oid) => workspace.write(path, oid.trim()),
        Err(e) if e.kind() == io::ErrorKind::NotFound => workspace.write(path, BASELINE_NOT_SET),
        Err(e) => Err(e),
    }
}

/// Get review baseline info: (`baseline_oid`, `commits_since`, `is_stale`).
///
/// If no baseline is set, returns `(None, 0, false)`.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_review_baseline_info() -> io::Result<(Option<String>, usize, bool)> {
    let repo = git2::Repository::discover(".").map_err(|e| to_io_error(&e))?;
    match load_review_baseline()? {
        ReviewBaseline::Commit(oid) => {
            let oid_str = oid.to_string();
            let commits_since = count_commits_since(&repo, &oid_str)?;
            let is_stale = commits_since > 10;
            Ok((Some(oid_str), commits_since, is_stale))
        }
        ReviewBaseline::NotSet => Ok((None, 0, false)),
    }
}

fn count_commits_since(repo: &git2::Repository, baseline_oid: &str) -> io::Result<usize> {
    let baseline = git2::Oid::from_str(baseline_oid).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("Invalid baseline OID: {baseline_oid}"),
        )
    })?;

    let head_oid = match repo.head() {
        Ok(head) => head.peel_to_commit().map_err(|e| to_io_error(&e))?.id(),
        Err(ref e) if e.code() == git2::ErrorCode::UnbornBranch => return Ok(0),
        Err(e) => return Err(to_io_error(&e)),
    };

    // Prefer libgit2 graph calculation when possible.
    if let Ok((ahead, _behind)) = repo.graph_ahead_behind(head_oid, baseline) {
        return Ok(ahead);
    }

    // Fallback: count commits reachable from HEAD excluding those reachable from baseline.
    let mut walk = repo.revwalk().map_err(|e| to_io_error(&e))?;
    walk.push(head_oid).map_err(|e| to_io_error(&e))?;
    walk.hide(baseline).map_err(|e| to_io_error(&e))?;
    let commits: Vec<_> = walk.collect();
    Ok(commits.len())
}

fn to_io_error(err: &git2::Error) -> io::Error {
    io::Error::other(err.to_string())
}
