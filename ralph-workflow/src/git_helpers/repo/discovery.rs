use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use crate::git_helpers::git2_to_io_error;

/// Returns the path to the `ralph` subdirectory inside the git metadata directory.
///
/// This directory holds Ralph's runtime enforcement state (marker, track file, head-oid).
/// It is inside `.git/` (or the actual git dir for worktrees) and is therefore invisible
/// to working-tree scans.
///
/// Falls back to `repo_root/.git/ralph` if libgit2 discovery fails (e.g., plain temp
/// directories used in unit tests).
pub fn ralph_git_dir(repo_root: &Path) -> PathBuf {
    if let Ok(hooks_dir) = get_hooks_dir_from(repo_root) {
        if let Some(git_dir) = hooks_dir.parent() {
            return git_dir.join("ralph");
        }
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

/// Get the git hooks directory path.
///
/// Returns the path to the hooks directory inside .git (or the equivalent
/// for worktrees and other configurations).
pub fn get_hooks_dir() -> io::Result<PathBuf> {
    get_hooks_dir_from(Path::new("."))
}

pub fn get_hooks_dir_from(discovery_root: &Path) -> io::Result<PathBuf> {
    let repo = git2::Repository::discover(discovery_root).map_err(|e| git2_to_io_error(&e))?;
    Ok(repo.path().join("hooks"))
}
