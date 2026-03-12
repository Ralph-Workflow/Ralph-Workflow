use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use crate::git_helpers::git2_to_io_error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtectionScope {
    pub repo_root: PathBuf,
    pub git_dir: PathBuf,
    pub common_git_dir: PathBuf,
    pub hooks_dir: PathBuf,
    pub ralph_dir: PathBuf,
    pub is_linked_worktree: bool,
    pub uses_worktree_scoped_hooks: bool,
    pub worktree_config_path: Option<PathBuf>,
}

/// Resolve the active git-protection scope for the current repository context.
///
/// # Errors
///
/// Returns an error when the current directory is not inside a git worktree or
/// when the repository workdir cannot be determined.
pub fn resolve_protection_scope() -> io::Result<ProtectionScope> {
    resolve_protection_scope_from(Path::new("."))
}

/// Resolve the active git-protection scope for an explicit discovery root.
///
/// # Errors
///
/// Returns an error when `discovery_root` is not inside a git worktree or when
/// the repository workdir cannot be determined.
pub fn resolve_protection_scope_from(discovery_root: &Path) -> io::Result<ProtectionScope> {
    let repo = git2::Repository::discover(discovery_root).map_err(|e| git2_to_io_error(&e))?;
    let repo_root = repo
        .workdir()
        .map(PathBuf::from)
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No workdir for repository"))?;
    let git_dir = repo.path().to_path_buf();
    let common_git_dir = common_git_dir(&repo);
    let is_linked_worktree = repo.is_worktree() && git_dir != common_git_dir;
    let has_linked_worktrees = common_git_dir.join("worktrees").is_dir();
    let uses_worktree_scoped_hooks = is_linked_worktree || has_linked_worktrees;
    let worktree_config_path = uses_worktree_scoped_hooks.then(|| {
        if is_linked_worktree {
            git_dir.join("config.worktree")
        } else {
            common_git_dir.join("config.worktree")
        }
    });
    let ralph_dir = git_dir.join("ralph");
    let hooks_dir = if uses_worktree_scoped_hooks {
        ralph_dir.join("hooks")
    } else {
        git_dir.join("hooks")
    };

    Ok(ProtectionScope {
        repo_root,
        git_dir,
        common_git_dir,
        hooks_dir,
        ralph_dir,
        is_linked_worktree,
        uses_worktree_scoped_hooks,
        worktree_config_path,
    })
}

/// Returns the path to the `ralph` subdirectory inside the git metadata directory.
///
/// This directory holds Ralph's runtime enforcement state (marker, track file, head-oid).
/// It is inside the active git dir for the current repository context and is therefore
/// invisible to working-tree scans.
///
/// Falls back to `repo_root/.git/ralph` if libgit2 discovery fails (e.g., plain temp
/// directories used in unit tests).
pub fn ralph_git_dir(repo_root: &Path) -> PathBuf {
    if let Ok(scope) = resolve_protection_scope_from(repo_root) {
        return scope.ralph_dir;
    }
    // Fallback: assume standard .git directory layout.
    repo_root.join(".git").join("ralph")
}

pub fn quarantine_path_in_place(path: &Path, label: &str) -> io::Result<PathBuf> {
    let parent = path.parent().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "path has no parent directory")
    })?;
    let file_name = path
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no file name"))?;

    let suffix = format!(
        "ralph.tampered.{label}.{}.{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    );
    let tampered_name = format!("{}.{}", file_name.to_string_lossy(), suffix);
    let tampered_path = parent.join(tampered_name);

    match fs::rename(path, &tampered_path) {
        Ok(()) => Ok(tampered_path),
        Err(e) => {
            let is_empty_dir = fs::symlink_metadata(path).ok().is_some_and(|m| m.is_dir())
                && fs::read_dir(path)
                    .ok()
                    .is_some_and(|mut it| it.next().is_none());
            if is_empty_dir {
                fs::remove_dir(path)?;
                Ok(path.to_path_buf())
            } else {
                Err(e)
            }
        }
    }
}

fn prepare_ralph_git_dir_internal(ralph_dir: &Path, create_if_missing: bool) -> io::Result<bool> {
    match fs::symlink_metadata(ralph_dir) {
        Ok(meta) => {
            let ft = meta.file_type();
            if ft.is_symlink() || !meta.is_dir() {
                quarantine_path_in_place(ralph_dir, "dir")?;
                if !create_if_missing {
                    return Ok(false);
                }
            } else {
                return Ok(true);
            }
        }
        Err(e) if e.kind() == io::ErrorKind::NotFound => {
            if !create_if_missing {
                return Ok(false);
            }
        }
        Err(e) => return Err(e),
    }

    fs::create_dir_all(ralph_dir)?;
    let meta = fs::symlink_metadata(ralph_dir)?;
    let ft = meta.file_type();
    if ft.is_symlink() || !meta.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "ralph git dir is not a regular directory",
        ));
    }

    Ok(true)
}

pub fn ensure_ralph_git_dir(repo_root: &Path) -> io::Result<PathBuf> {
    let ralph_dir = ralph_git_dir(repo_root);
    prepare_ralph_git_dir_internal(&ralph_dir, true)?;
    Ok(ralph_dir)
}

pub fn sanitize_ralph_git_dir_at(ralph_dir: &Path) -> io::Result<bool> {
    prepare_ralph_git_dir_internal(ralph_dir, false)
}

/// Check if we're in a git repository.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn require_git_repo() -> io::Result<()> {
    git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    Ok(())
}

/// Get the git repository root.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_repo_root() -> io::Result<PathBuf> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    repo.workdir()
        .map(PathBuf::from)
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No workdir for repository"))
}

pub fn get_hooks_dir_from(discovery_root: &Path) -> io::Result<PathBuf> {
    Ok(resolve_protection_scope_from(discovery_root)?.hooks_dir)
}

/// Returns the common git directory for a repository.
///
/// For main worktrees, this is the same as `repo.path()`.
/// For linked worktrees, this navigates from `.git/worktrees/<name>/`
/// up to the shared `.git/` directory.
///
/// This is needed because git2 0.18 does not expose `Repository::commondir()`.
fn common_git_dir(repo: &git2::Repository) -> PathBuf {
    let path = repo.path();
    if repo.is_worktree() {
        // For linked worktrees, path() returns .git/worktrees/<name>/
        // Common dir is the grandparent: .git/
        if let Some(worktrees_dir) = path.parent() {
            if worktrees_dir.file_name().and_then(|n| n.to_str()) == Some("worktrees") {
                if let Some(common) = worktrees_dir.parent() {
                    return common.to_path_buf();
                }
            }
        }
    }
    path.to_path_buf()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: create a git repo with an initial commit (required for worktree creation).
    fn init_repo_with_commit(path: &Path) -> git2::Repository {
        let repo = git2::Repository::init(path).unwrap();
        {
            let mut index = repo.index().unwrap();
            let tree_oid = index.write_tree().unwrap();
            let tree = repo.find_tree(tree_oid).unwrap();
            let sig = git2::Signature::now("test", "test@test.com").unwrap();
            repo.commit(Some("HEAD"), &sig, &sig, "initial", &tree, &[])
                .unwrap();
        }
        repo
    }

    /// Canonicalize a path, tolerating non-existent trailing components.
    ///
    /// On macOS `/var` is a symlink to `/private/var`, so tempdir paths
    /// and libgit2-resolved paths may differ. Canonicalize the parent
    /// (which must exist) and re-append the leaf to get a comparable path.
    fn canon(path: &Path) -> PathBuf {
        if let Ok(c) = fs::canonicalize(path) {
            return c;
        }
        // Path doesn't exist yet (e.g., .git/ralph/hooks before creation).
        // Canonicalize the nearest existing ancestor and append the remainder.
        let mut existing_ancestor = path;
        while !existing_ancestor.exists() {
            let Some(parent) = existing_ancestor.parent() else {
                return path.to_path_buf();
            };
            existing_ancestor = parent;
        }
        if let Ok(canon_ancestor) = fs::canonicalize(existing_ancestor) {
            let suffix = path
                .strip_prefix(existing_ancestor)
                .unwrap_or_else(|_| Path::new(""));
            return canon_ancestor.join(suffix);
        }
        path.to_path_buf()
    }

    #[test]
    fn resolve_protection_scope_for_regular_repo_uses_main_git_dir_for_all_paths() {
        let tmp = tempfile::tempdir().unwrap();
        let repo = git2::Repository::init(tmp.path()).unwrap();

        let scope = resolve_protection_scope_from(tmp.path()).unwrap();

        assert!(!scope.is_linked_worktree);
        assert_eq!(canon(&scope.git_dir), canon(repo.path()));
        assert_eq!(canon(&scope.common_git_dir), canon(repo.path()));
        assert_eq!(
            canon(&scope.hooks_dir),
            canon(&tmp.path().join(".git/hooks"))
        );
        assert_eq!(
            canon(&scope.ralph_dir),
            canon(&tmp.path().join(".git/ralph"))
        );
        assert!(!scope.uses_worktree_scoped_hooks);
        assert_eq!(scope.worktree_config_path, None);
    }

    #[test]
    fn resolve_protection_scope_for_linked_worktree_keeps_common_and_active_git_dirs_distinct() {
        let tmp = tempfile::tempdir().unwrap();
        let main_repo = init_repo_with_commit(tmp.path());
        let wt_path = tmp.path().join("wt-test");
        let _wt = main_repo.worktree("wt-test", &wt_path, None).unwrap();
        let wt_repo = git2::Repository::open(&wt_path).unwrap();

        let scope = resolve_protection_scope_from(&wt_path).unwrap();

        assert!(scope.is_linked_worktree);
        assert!(scope.uses_worktree_scoped_hooks);
        assert_eq!(canon(&scope.git_dir), canon(wt_repo.path()));
        assert_eq!(canon(&scope.common_git_dir), canon(main_repo.path()));
        assert_ne!(canon(&scope.git_dir), canon(&scope.common_git_dir));
        assert_eq!(
            scope.worktree_config_path.as_deref().map(canon),
            Some(canon(&wt_repo.path().join("config.worktree")))
        );
    }

    #[test]
    fn resolve_protection_scope_for_linked_worktree_uses_worktree_local_hook_and_ralph_dirs() {
        let tmp = tempfile::tempdir().unwrap();
        let main_repo = init_repo_with_commit(tmp.path());
        let wt_path = tmp.path().join("wt-test");
        let _wt = main_repo.worktree("wt-test", &wt_path, None).unwrap();
        let wt_repo = git2::Repository::open(&wt_path).unwrap();

        let scope = resolve_protection_scope_from(&wt_path).unwrap();

        assert_eq!(
            canon(&scope.hooks_dir),
            canon(&wt_repo.path().join("ralph/hooks"))
        );
        assert_eq!(
            canon(&scope.ralph_dir),
            canon(&wt_repo.path().join("ralph"))
        );
        assert_ne!(
            canon(&scope.hooks_dir),
            canon(&tmp.path().join(".git/hooks"))
        );
        assert_ne!(
            canon(&scope.ralph_dir),
            canon(&tmp.path().join(".git/ralph"))
        );
    }

    #[test]
    fn resolve_protection_scope_for_main_worktree_with_linked_siblings_uses_main_worktree_config() {
        let tmp = tempfile::tempdir().unwrap();
        let main_repo = init_repo_with_commit(tmp.path());
        let wt_path = tmp.path().join("wt-test");
        let _wt = main_repo.worktree("wt-test", &wt_path, None).unwrap();

        let scope = resolve_protection_scope_from(tmp.path()).unwrap();

        assert!(!scope.is_linked_worktree);
        assert!(scope.uses_worktree_scoped_hooks);
        assert_eq!(canon(&scope.git_dir), canon(&scope.common_git_dir));
        assert_eq!(
            canon(&scope.hooks_dir),
            canon(&tmp.path().join(".git/ralph/hooks"))
        );
        assert_eq!(
            scope.worktree_config_path.as_deref().map(canon),
            Some(canon(&tmp.path().join(".git/config.worktree")))
        );
    }
}
