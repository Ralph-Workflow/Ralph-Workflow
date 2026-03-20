use std::fs;
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
pub fn resolve_protection_scope() -> std::io::Result<ProtectionScope> {
    resolve_protection_scope_from(Path::new("."))
}

/// Resolve the active git-protection scope for an explicit discovery root.
///
/// # Errors
///
/// Returns an error when `discovery_root` is not inside a git worktree or when
/// the repository workdir cannot be determined.
pub fn resolve_protection_scope_from(discovery_root: &Path) -> std::io::Result<ProtectionScope> {
    let repo = git2::Repository::discover(discovery_root).map_err(|e| git2_to_io_error(&e))?;
    let repo_root = repo.workdir().map(PathBuf::from).ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "No workdir for repository")
    })?;
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

pub fn normalize_protection_scope_path(path: &Path) -> PathBuf {
    if let Ok(canonical) = fs::canonicalize(path) {
        return canonical;
    }

    let existing_ancestor = find_existing_ancestor(path);
    if existing_ancestor == path {
        return path.to_path_buf();
    }

    build_normalized_path(path, &existing_ancestor)
}

fn find_existing_ancestor(path: &Path) -> PathBuf {
    path.ancestors()
        .find(|ancestor| ancestor.exists())
        .map(PathBuf::from)
        .unwrap_or_else(|| path.to_path_buf())
}

fn build_normalized_path(path: &Path, existing_ancestor: &Path) -> PathBuf {
    let Ok(canonical_ancestor) = fs::canonicalize(existing_ancestor) else {
        return path.to_path_buf();
    };

    let suffix = path
        .strip_prefix(existing_ancestor)
        .unwrap_or_else(|_| Path::new(""));
    canonical_ancestor.join(suffix)
}

pub fn quarantine_path_in_place(path: &Path, label: &str) -> std::io::Result<PathBuf> {
    let parent = path.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "path has no parent directory",
        )
    })?;
    let file_name = path.file_name().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "path has no file name")
    })?;

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
                && fs::read_dir(path).ok().is_some_and(|it| it.count() == 0);
            if is_empty_dir {
                fs::remove_dir(path)?;
                Ok(path.to_path_buf())
            } else {
                Err(e)
            }
        }
    }
}

fn prepare_ralph_git_dir_internal(
    ralph_dir: &Path,
    create_if_missing: bool,
) -> std::io::Result<bool> {
    match fs::symlink_metadata(ralph_dir) {
        Ok(meta) => handle_existing_ralph_dir(ralph_dir, &meta, create_if_missing),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            if !create_if_missing {
                return Ok(false);
            }
            fs::create_dir_all(ralph_dir)?;
            verify_created_ralph_dir(ralph_dir)
        }
        Err(e) => Err(e),
    }
}

fn handle_existing_ralph_dir(
    ralph_dir: &Path,
    meta: &fs::Metadata,
    create_if_missing: bool,
) -> std::io::Result<bool> {
    let ft = meta.file_type();
    if ft.is_symlink() || !meta.is_dir() {
        quarantine_path_in_place(ralph_dir, "dir")?;
        if !create_if_missing {
            return Ok(false);
        }
        fs::create_dir_all(ralph_dir)?;
        verify_created_ralph_dir(ralph_dir)
    } else {
        Ok(true)
    }
}

fn verify_created_ralph_dir(ralph_dir: &Path) -> std::io::Result<bool> {
    let meta = fs::symlink_metadata(ralph_dir)?;
    let ft = meta.file_type();
    if ft.is_symlink() || !meta.is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "ralph git dir is not a regular directory",
        ));
    }
    Ok(true)
}

pub fn ensure_ralph_git_dir(repo_root: &Path) -> std::io::Result<PathBuf> {
    let ralph_dir = ralph_git_dir(repo_root);
    prepare_ralph_git_dir_internal(&ralph_dir, true)?;
    Ok(ralph_dir)
}

pub fn sanitize_ralph_git_dir_at(ralph_dir: &Path) -> std::io::Result<bool> {
    prepare_ralph_git_dir_internal(ralph_dir, false)
}

/// Check if we're in a git repository.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn require_git_repo() -> std::io::Result<()> {
    git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    Ok(())
}

/// Get the git repository root.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_repo_root() -> std::io::Result<PathBuf> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    repo.workdir().map(PathBuf::from).ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "No workdir for repository")
    })
}

pub fn get_hooks_dir_from(discovery_root: &Path) -> std::io::Result<PathBuf> {
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

    fn canon(path: &Path) -> PathBuf {
        normalize_protection_scope_path(path)
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

    #[cfg(unix)]
    #[test]
    fn normalize_protection_scope_path_collapses_symlink_aliases_for_scope_comparison() {
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let repo_path = tmp.path().join("repo");
        fs::create_dir_all(&repo_path).unwrap();

        let alias_parent = tmp.path().join("aliases");
        fs::create_dir_all(&alias_parent).unwrap();
        let alias_path = alias_parent.join("repo-link");
        symlink(&repo_path, &alias_path).unwrap();

        assert_eq!(
            normalize_protection_scope_path(&repo_path),
            normalize_protection_scope_path(&alias_path),
            "scope comparison should treat symlink aliases as the same repository path"
        );

        let real_git_dir = repo_path.join(".git");
        let alias_git_dir = alias_path.join(".git");
        assert_eq!(
            normalize_protection_scope_path(&real_git_dir),
            normalize_protection_scope_path(&alias_git_dir),
            "scope comparison should normalize git-dir aliases too"
        );
    }
}
