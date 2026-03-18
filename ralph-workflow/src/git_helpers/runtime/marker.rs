//! Marker file creation and repair for agent-phase commit protection.
//!
//! Handles creation of `<git-dir>/ralph/no_agent_commit` marker files with
//! tamper detection and self-healing (quarantine of non-regular paths).
//!
//! Marker files are regular empty files marked read-only (0o444) on Unix.
//! Any non-regular path (symlink/directory/socket/FIFO/device) is treated
//! as tampering and quarantined.

use crate::git_helpers::repo::{ensure_ralph_git_dir, quarantine_path_in_place, ralph_git_dir};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::Path;

const MARKER_FILE_NAME: &str = "no_agent_commit";

fn legacy_marker_path(repo_root: &Path) -> std::path::PathBuf {
    repo_root.join(".no_agent_commit")
}

fn is_regular_file(meta: &std::fs::Metadata) -> bool {
    let ft = meta.file_type();
    ft.is_file() && !ft.is_symlink()
}

pub(crate) fn marker_path_from_ralph_dir(ralph_dir: &Path) -> std::path::PathBuf {
    ralph_dir.join(MARKER_FILE_NAME)
}

pub(crate) fn marker_path_for_repo_root(repo_root: &Path) -> std::path::PathBuf {
    marker_path_from_ralph_dir(&ralph_git_dir(repo_root))
}

fn quarantine_and_create_marker(marker_path: &Path, repo_root: &Path) -> io::Result<()> {
    quarantine_path_in_place(marker_path, "marker")?;
    create_marker_in_repo_root(repo_root)
}

fn marker_needs_creation(meta: Result<std::fs::Metadata, io::Error>) -> bool {
    match meta {
        Ok(meta) => !is_regular_file(&meta),
        Err(e) if e.kind() == io::ErrorKind::NotFound => true,
        Err(_) => true,
    }
}

pub(crate) fn ensure_marker_exists(repo_root: &Path) -> io::Result<()> {
    let ralph_dir = ensure_ralph_git_dir(repo_root)?;
    let marker_path = marker_path_from_ralph_dir(&ralph_dir);

    if marker_needs_creation(fs::symlink_metadata(&marker_path)) {
        quarantine_and_create_marker(&marker_path, repo_root)?;
    }

    let open_res = {
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(&marker_path)
        }
        #[cfg(not(unix))]
        {
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&marker_path)
        }
    };

    match open_res {
        Ok(mut f) => {
            f.write_all(b"")?;
            f.flush()?;
            let _ = f.sync_all();
        }
        Err(ref e) if e.kind() == io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e),
    }
    Ok(())
}

/// Repair the marker path if it is not a regular file.
///
/// Any non-regular marker path (symlink/directory/socket/FIFO/device) can bypass
/// hook/wrapper `-f` checks. This function quarantines such paths and recreates
/// a regular file marker.
pub(crate) fn repair_marker_if_tampered(repo_root: &Path) -> io::Result<()> {
    let ralph_dir = ralph_git_dir(repo_root);
    let marker_path = marker_path_from_ralph_dir(&ralph_dir);

    if let Ok(meta) = fs::symlink_metadata(&marker_path) {
        if !is_regular_file(&meta) {
            quarantine_path_in_place(&marker_path, "marker")?;
        }
    }

    ensure_marker_exists(repo_root)
}

/// Create a regular file marker at `<git-dir>/ralph/no_agent_commit`.
///
/// If the path already exists as a regular file, this is a no-op.
/// If the path exists as a non-regular file (symlink/directory/socket/FIFO/device),
/// it is quarantined and replaced with a regular file.
pub(crate) fn create_marker_in_repo_root(repo_root: &Path) -> io::Result<()> {
    let ralph_dir = ensure_ralph_git_dir(repo_root)?;
    let marker_path = marker_path_from_ralph_dir(&ralph_dir);

    if let Ok(meta) = fs::symlink_metadata(&marker_path) {
        if is_regular_file(&meta) {
            return Ok(());
        }
        quarantine_path_in_place(&marker_path, "marker")?;
    }

    let open_res = {
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(&marker_path)
        }
        #[cfg(not(unix))]
        {
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&marker_path)
        }
    };

    match open_res {
        Ok(mut f) => {
            f.write_all(b"")?;
            f.flush()?;
            let _ = f.sync_all();
        }
        Err(ref e) if e.kind() == io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e),
    }
    Ok(())
}

pub(crate) fn marker_exists_at_path(marker_path: &Path) -> bool {
    fs::symlink_metadata(marker_path).is_ok_and(|meta| is_regular_file(&meta))
}

pub(crate) fn remove_marker_at_path(marker_path: &Path) {
    #[cfg(unix)]
    add_owner_write_if_not_symlink(marker_path);
    let _ = fs::remove_file(marker_path);
}

pub(crate) fn remove_legacy_marker(repo_root: &Path) {
    let legacy_marker = legacy_marker_path(repo_root);
    #[cfg(unix)]
    add_owner_write_if_not_symlink(&legacy_marker);
    let _ = fs::remove_file(&legacy_marker);
}

/// Make a file writable by adding owner-write permission (Unix only).
///
/// This is used before removing read-only marker or wrapper files.
#[cfg(unix)]
pub(crate) fn add_owner_write_if_not_symlink(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    if matches!(
        fs::symlink_metadata(path),
        Ok(meta) if meta.file_type().is_symlink()
    ) {
        return;
    }
    if let Ok(meta) = fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_mode(perms.mode() | 0o200);
        let _ = fs::set_permissions(path, perms);
    }
}

#[cfg(not(unix))]
pub(crate) fn add_owner_write_if_not_symlink(_path: &Path) {}

/// Set a file to read-only mode (0o444 or specified mode) if it is not a symlink.
#[cfg(unix)]
pub(crate) fn set_readonly_mode_if_not_symlink(path: &Path, mode: u32) {
    use std::os::unix::fs::PermissionsExt;
    if matches!(
        fs::symlink_metadata(path),
        Ok(meta) if meta.file_type().is_symlink()
    ) {
        return;
    }
    if let Ok(meta) = fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_mode(mode);
        let _ = fs::set_permissions(path, perms);
    }
}

#[cfg(not(unix))]
pub(crate) fn set_readonly_mode_if_not_symlink(_path: &Path, _mode: u32) {}

pub(crate) const MARKER_FILENAME: &str = MARKER_FILE_NAME;
