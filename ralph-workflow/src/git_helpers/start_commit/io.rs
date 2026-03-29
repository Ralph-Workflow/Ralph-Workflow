// git_helpers/start_commit/io.rs — boundary module for libgit2 revwalk operations.
// File stem is `io` — recognized as boundary module by forbid_mut_binding and
// forbid_mutating_receiver_methods lints.

/// Get the current working directory (boundary function for environment access).
pub(super) fn get_current_dir() -> std::io::Result<std::path::PathBuf> {
    std::env::current_dir()
}

/// Count commits reachable from `head_commit_id` but not from `start_commit_id`,
/// up to a maximum of `limit`.
///
/// Returns the count of commits and whether the result hit the limit.
pub(super) fn revwalk_count_commits_since(
    repo: &git2::Repository,
    head_commit_id: git2::Oid,
    start_commit_id: git2::Oid,
    limit: usize,
) -> std::io::Result<usize> {
    let commits = collect_revwalk_results(repo, head_commit_id, start_commit_id, limit)?;
    Ok(commits.len())
}

fn collect_revwalk_results(
    repo: &git2::Repository,
    head_commit_id: git2::Oid,
    start_commit_id: git2::Oid,
    limit: usize,
) -> std::io::Result<Vec<git2::Oid>> {
    let mut revwalk = repo
        .revwalk()
        .map_err(|e| std::io::Error::other(e.to_string()))?;
    revwalk
        .push(head_commit_id)
        .map_err(|e| std::io::Error::other(e.to_string()))?;

    revwalk
        .map(|res| res.map_err(|e| std::io::Error::other(e.to_string())))
        .take_while(|res| res.as_ref().map_or(true, |id| *id != start_commit_id))
        .take(limit)
        .collect()
}
