// git_helpers/review_baseline/io.rs — boundary module for libgit2 revwalk operations.
// File stem is `io` — recognized as boundary module by forbid_mut_binding and
// forbid_mutating_receiver_methods lints.

/// Get the current working directory (boundary function for environment access).
pub(super) fn get_current_dir() -> std::io::Result<std::path::PathBuf> {
    std::env::current_dir()
}

/// Count commits reachable from `head_oid` but not from `baseline` using a revwalk.
///
/// This is a fallback for when `repo.graph_ahead_behind` is unavailable.
pub(super) fn revwalk_count_commits(
    repo: &git2::Repository,
    head_oid: git2::Oid,
    baseline: git2::Oid,
) -> std::io::Result<usize> {
    let mut walk = repo
        .revwalk()
        .map_err(|e| std::io::Error::other(e.to_string()))?;
    walk.push(head_oid)
        .map_err(|e| std::io::Error::other(e.to_string()))?;
    walk.hide(baseline)
        .map_err(|e| std::io::Error::other(e.to_string()))?;
    let commits: Vec<_> = walk.collect();
    Ok(commits.len())
}
