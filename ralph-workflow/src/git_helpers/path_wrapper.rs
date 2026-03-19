//! PATH wrapper operations for git commit protection.
//!
//! Manages the temporary `git` wrapper script in a temp directory that is
//! prepended to PATH. The wrapper intercepts `git commit/push/tag` commands
//! and blocks them when the agent-phase marker file exists.

use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

const WRAPPER_TRACK_FILE_NAME: &str = "git-wrapper-dir.txt";
const WRAPPER_DIR_PREFIX: &str = "ralph-git-wrapper-";

pub(crate) fn track_file_path_for_ralph_dir(ralph_dir: &Path) -> PathBuf {
    ralph_dir.join(WRAPPER_TRACK_FILE_NAME)
}

fn path_has_parent_dir_component(path: &Path) -> bool {
    path.components()
        .any(|c| matches!(c, std::path::Component::ParentDir))
}

pub(crate) fn is_reasonable_temp_path(path: &Path) -> bool {
    if !path.is_absolute() {
        return false;
    }
    if path_has_parent_dir_component(path) {
        return false;
    }
    if !path_is_under_temp_dir(path) {
        return false;
    }
    let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
        return false;
    };
    name.starts_with(WRAPPER_DIR_PREFIX)
}

fn path_is_under_temp_dir(path: &Path) -> bool {
    let temp_dir = env::temp_dir();
    if path.starts_with(&temp_dir) {
        return true;
    }
    let Ok(temp_dir_canon) = fs::canonicalize(&temp_dir) else {
        return false;
    };
    path.starts_with(&temp_dir_canon)
}

pub(crate) fn is_safe_existing_dir(path: &Path) -> bool {
    if !is_reasonable_temp_path(path) {
        return false;
    }
    let Ok(meta) = fs::symlink_metadata(path) else {
        return false;
    };
    if meta.file_type().is_symlink() {
        return false;
    }
    meta.is_dir()
}

pub(crate) fn is_on_path(path: &Path) -> bool {
    let Ok(path_var) = env::var("PATH") else {
        return false;
    };
    path_var
        .split(':')
        .any(|entry| !entry.is_empty() && Path::new(entry) == path)
}

pub(crate) fn prepend_wrapper_dir_to_path(wrapper_dir: &Path) {
    let current_path = env::var("PATH").unwrap_or_default();
    if current_path
        .split(':')
        .next()
        .is_some_and(|first| !first.is_empty() && Path::new(first) == wrapper_dir)
    {
        return;
    }
    env::set_var(
        "PATH",
        format!("{}:{}", wrapper_dir.display(), current_path),
    );
}

pub(crate) fn remove_path_entry(path_to_remove: &Path) {
    if let Ok(path) = env::var("PATH") {
        let new_path: String = path
            .split(':')
            .filter(|p| !p.is_empty() && Path::new(p) != path_to_remove)
            .collect::<Vec<_>>()
            .join(":");
        env::set_var("PATH", new_path);
    }
}

pub(crate) fn make_wrapper_script_writable(wrapper_dir_path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let wrapper_path = wrapper_dir_path.join("git");
        if let Ok(meta) = fs::metadata(&wrapper_path) {
            let mut perms = meta.permissions();
            perms.set_mode(perms.mode() | 0o200);
            let _ = fs::set_permissions(&wrapper_path, perms);
        }
    }
}

pub(crate) fn remove_wrapper_dir_and_entry(wrapper_dir: &Path) -> bool {
    remove_path_entry(wrapper_dir);
    if is_safe_existing_dir(wrapper_dir) {
        make_wrapper_script_writable(wrapper_dir);
        let _ = fs::remove_dir_all(wrapper_dir);
    }
    !wrapper_dir.exists()
}

pub(crate) fn find_wrapper_dir_on_path() -> Option<PathBuf> {
    let path_var = env::var("PATH").ok()?;
    path_var.split(':').find_map(|entry| {
        if entry.is_empty() {
            return None;
        }
        let p = PathBuf::from(entry);
        if is_reasonable_temp_path(&p) {
            Some(p)
        } else {
            None
        }
    })
}

/// Read the wrapper directory path from the track file, if valid.
pub(crate) fn read_tracked_wrapper_dir(ralph_dir: &Path) -> Option<PathBuf> {
    let track_path = track_file_path_for_ralph_dir(ralph_dir);
    let content = fs::read_to_string(&track_path).ok()?;
    let path = PathBuf::from(content.trim());
    if is_safe_existing_dir(&path) && is_on_path(&path) {
        Some(path)
    } else {
        None
    }
}

/// Write the wrapper track file atomically.
pub(crate) fn write_track_file_atomic(repo_root: &Path, wrapper_dir: &Path) -> io::Result<()> {
    let ralph_dir = crate::git_helpers::repo::ensure_ralph_git_dir(repo_root)?;
    let track_file_path = track_file_path_for_ralph_dir(&ralph_dir);

    if let Ok(meta) = fs::symlink_metadata(&track_file_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            crate::git_helpers::repo::quarantine_path_in_place(&track_file_path, "track")?;
        }
    }

    let tmp_track = ralph_dir.join(format!(
        ".git-wrapper-dir.tmp.{}.{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));

    {
        let mut tf = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp_track)?;
        tf.write_all(wrapper_dir.display().to_string().as_bytes())?;
        tf.write_all(b"\n")?;
        tf.flush()?;
        let _ = tf.sync_all();
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&tmp_track)?.permissions();
        perms.set_mode(0o444);
        fs::set_permissions(&tmp_track, perms)?;
    }
    #[cfg(windows)]
    {
        let mut perms = fs::metadata(&tmp_track)?.permissions();
        perms.set_readonly(true);
        fs::set_permissions(&tmp_track, perms)?;
    }

    #[cfg(windows)]
    {
        if track_file_path.exists() {
            let _ = fs::remove_file(&track_file_path);
        }
    }
    fs::rename(&tmp_track, &track_file_path)
}

/// Relax permissions on a regular file to allow removal (0o444 → 0o644).
pub(crate) fn relax_temp_cleanup_permissions(path: &Path) {
    let Ok(meta) = fs::symlink_metadata(path) else {
        return;
    };
    let file_type = meta.file_type();
    if !file_type.is_file() || file_type.is_symlink() {
        return;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = meta.permissions();
        perms.set_mode(perms.mode() | 0o200);
        let _ = fs::set_permissions(path, perms);
    }
    #[cfg(windows)]
    {
        let mut perms = meta.permissions();
        perms.set_readonly(false);
        let _ = fs::set_permissions(path, perms);
    }
}

/// Clean up stray atomic-write temp files from the ralph git directory.
///
/// These files are created by `write_track_file_atomic` and
/// `write_head_oid_file_atomic` when the rename fails.
pub(crate) fn cleanup_stray_tmp_files(ralph_dir: &Path) {
    if let Ok(entries) = fs::read_dir(ralph_dir) {
        entries
            .flatten()
            .filter(|entry| is_stray_tmp_file(entry))
            .for_each(|entry| {
                cleanup_stray_tmp_entry(&entry);
            });
    }
}

fn is_stray_tmp_file(entry: &fs::DirEntry) -> bool {
    let name = entry.file_name();
    let name_str = name.to_string_lossy();
    if !name_str.starts_with(".head-oid.tmp.") && !name_str.starts_with(".git-wrapper-dir.tmp.") {
        return false;
    }
    let path = entry.path();
    let Ok(meta) = fs::symlink_metadata(&path) else {
        return false;
    };
    let file_type = meta.file_type();
    file_type.is_file() && !file_type.is_symlink()
}

fn cleanup_stray_tmp_entry(entry: &fs::DirEntry) {
    let path = entry.path();
    relax_temp_cleanup_permissions(&path);
    let _ = fs::remove_file(&path);
}

pub(crate) const TRACK_FILENAME: &str = WRAPPER_TRACK_FILE_NAME;
