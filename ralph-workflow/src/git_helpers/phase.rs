//! Agent phase lifecycle management.
//!
//! Coordinates the start, self-heal check, and end of the agent phase.
//! Handles marker creation, wrapper installation, hooks setup, and cleanup.

use super::marker;
use super::marker::set_readonly_mode_if_not_symlink;
use super::path_wrapper::{
    self, is_safe_existing_dir, prepend_wrapper_dir_to_path, read_tracked_wrapper_dir,
    track_file_path_for_ralph_dir, write_track_file_atomic,
};
use super::script::{escape_shell_single_quoted, make_wrapper_content};
use crate::git_helpers::repo::{normalize_protection_scope_path, ralph_git_dir};
use crate::logger::Logger;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use which::which;

const HEAD_OID_FILE_NAME: &str = "head-oid.txt";
const WRAPPER_DIR_PREFIX: &str = "ralph-git-wrapper-";

/// Escape a path for safe use in a POSIX shell single-quoted string.
pub(crate) fn escape_shell_path(path: &str) -> io::Result<String> {
    escape_shell_single_quoted(path)
}

/// Find the real git binary in PATH, excluding the given wrapper directory.
pub(crate) fn find_real_git_excluding(exclude_dir: &Path) -> Option<PathBuf> {
    let path_var = env::var("PATH").ok()?;
    let wrapper_path = exclude_dir.join("git");
    find_git_in_path(path_var, exclude_dir, &wrapper_path)
}

fn find_git_in_path(path_var: String, exclude_dir: &Path, wrapper_path: &Path) -> Option<PathBuf> {
    for entry in path_var.split(':') {
        if entry.is_empty() || entry == exclude_dir.to_string_lossy() {
            continue;
        }
        let candidate = Path::new(entry).join("git");
        if candidate == *wrapper_path || !candidate.exists() {
            continue;
        }
        if !is_executable_git(&candidate) {
            continue;
        }
        return Some(candidate);
    }
    None
}

fn is_executable_git(candidate: &Path) -> bool {
    matches!(
        fs::metadata(candidate),
        Ok(meta) if meta.file_type().is_file()
    ) && {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Ok(meta) = fs::metadata(candidate) {
                let mode = meta.permissions().mode() & 0o777;
                if (mode & 0o111) == 0 {
                    return false;
                }
            }
        }
        true
    }
}

/// Verify and restore agent-phase commit protections before each agent invocation.
///
/// This is the composite integrity check that self-heals against a prior agent
/// that deleted the enforcement marker or tampered with git hooks during
/// its run. It is designed to be called from `run_with_prompt` before every
/// agent spawn.
#[derive(Debug, Clone, Default)]
pub struct ProtectionCheckResult {
    pub tampering_detected: bool,
    pub details: Vec<String>,
}

pub(crate) fn check_marker_integrity(
    ralph_dir: &Path,
    _repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    use crate::git_helpers::repo::quarantine_path_in_place;

    let marker_path = marker::marker_path_from_ralph_dir(ralph_dir);

    if let Ok(meta) = fs::symlink_metadata(&marker_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            logger.warn("Enforcement marker is not a regular file — quarantining and recreating");
            result.tampering_detected = true;
            result
                .details
                .push("Enforcement marker was not a regular file — quarantined".to_string());
            if let Err(e) = quarantine_path_in_place(&marker_path, "marker") {
                logger.warn(&format!("Failed to quarantine marker path: {e}"));
                result
                    .details
                    .push("Marker path quarantine failed".to_string());
            }
        }
    }
}

pub(crate) fn check_track_file_integrity(
    ralph_dir: &Path,
    _repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    use crate::git_helpers::repo::quarantine_path_in_place;

    let track_file_path = track_file_path_for_ralph_dir(ralph_dir);

    if let Ok(meta) = fs::symlink_metadata(&track_file_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            logger.warn("Git wrapper tracking path is not a regular file — quarantining");
            result.tampering_detected = true;
            result
                .details
                .push("Git wrapper tracking path was not a regular file — quarantined".to_string());
            if let Err(e) = quarantine_path_in_place(&track_file_path, "track") {
                logger.warn(&format!("Failed to quarantine wrapper tracking path: {e}"));
                result
                    .details
                    .push("Wrapper tracking path quarantine failed".to_string());
            }
        }
    }
}

pub(crate) fn check_and_repair_marker_symlink(
    marker_path: &Path,
    repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    let marker_meta = fs::symlink_metadata(marker_path).ok();
    let marker_is_symlink = marker_meta
        .as_ref()
        .is_some_and(|meta| meta.file_type().is_symlink());
    let marker_exists = marker_meta
        .as_ref()
        .is_some_and(|meta| meta.file_type().is_file() && !meta.file_type().is_symlink());

    if marker_is_symlink {
        logger.warn("Enforcement marker is a symlink — removing and recreating");
        let _ = fs::remove_file(marker_path);
        result.tampering_detected = true;
        result
            .details
            .push("Enforcement marker was a symlink — removed".to_string());
    }
    if !marker_exists {
        logger.warn("Enforcement marker missing — recreating");
        if let Err(e) = marker::create_marker_in_repo_root(repo_root) {
            logger.warn(&format!("Failed to recreate enforcement marker: {e}"));
        } else {
            #[cfg(unix)]
            set_readonly_mode_if_not_symlink(marker_path, 0o444);
        }
        result.tampering_detected = true;
        result
            .details
            .push("Enforcement marker was missing — recreated".to_string());
    }
}

pub(crate) fn check_and_repair_marker_permissions(
    marker_path: &Path,
    repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if matches!(
            fs::symlink_metadata(marker_path),
            Ok(meta) if meta.file_type().is_symlink()
        ) {
            return;
        }
        if let Ok(meta) = fs::metadata(marker_path) {
            if meta.is_file() {
                let mode = meta.permissions().mode() & 0o777;
                if mode != 0o444 {
                    logger.warn(&format!(
                        "Enforcement marker permissions loosened ({mode:#o}) — restoring to 0o444"
                    ));
                    let mut perms = meta.permissions();
                    perms.set_mode(0o444);
                    let _ = fs::set_permissions(marker_path, perms);
                    result.tampering_detected = true;
                    result.details.push(format!(
                        "Enforcement marker permissions loosened ({mode:#o}) — restored to 0o444"
                    ));
                }
            } else {
                logger.warn("Enforcement marker is not a regular file — quarantining");
                result.tampering_detected = true;
                result
                    .details
                    .push("Enforcement marker was not a regular file — quarantined".to_string());
                if let Err(e) =
                    crate::git_helpers::repo::quarantine_path_in_place(marker_path, "marker-perms")
                {
                    logger.warn(&format!("Failed to quarantine marker path: {e}"));
                } else if let Err(e) = marker::create_marker_in_repo_root(repo_root) {
                    logger.warn(&format!(
                        "Failed to recreate enforcement marker after quarantine: {e}"
                    ));
                } else {
                    #[cfg(unix)]
                    set_readonly_mode_if_not_symlink(marker_path, 0o444);
                }
            }
        }
    }
}

pub(crate) fn check_track_file_permissions(
    track_file_path: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if matches!(
            fs::symlink_metadata(track_file_path),
            Ok(m) if m.file_type().is_symlink()
        ) {
            logger.warn("Track file path is a symlink — refusing to chmod and attempting repair");
            result.tampering_detected = true;
            result
                .details
                .push("Track file was a symlink — refused chmod".to_string());
            let _ = fs::remove_file(track_file_path);
            if let Some(dir) =
                path_wrapper::find_wrapper_dir_on_path().filter(|p| is_safe_existing_dir(p))
            {
                let _ = write_track_file_atomic(&std::path::PathBuf::from("."), &dir);
            }
        } else if let Ok(meta) = fs::metadata(track_file_path) {
            if meta.is_dir() {
                logger.warn("Track file path is a directory — quarantining");
                result.tampering_detected = true;
                result
                    .details
                    .push("Track file was a directory — quarantined".to_string());
                if let Err(e) = crate::git_helpers::repo::quarantine_path_in_place(
                    track_file_path,
                    "track-perms",
                ) {
                    logger.warn(&format!("Failed to quarantine track file path: {e}"));
                }
            }
            if meta.is_file() {
                let mode = meta.permissions().mode() & 0o777;
                if mode != 0o444 {
                    logger.warn(&format!(
                        "Track file permissions loosened ({mode:#o}) — restoring to 0o444"
                    ));
                    let mut perms = meta.permissions();
                    perms.set_mode(0o444);
                    let _ = fs::set_permissions(track_file_path, perms);
                    result.tampering_detected = true;
                    result.details.push(format!(
                        "Track file permissions loosened ({mode:#o}) — restored to 0o444"
                    ));
                }
            }
        }
    }
}

pub(crate) fn check_and_install_wrapper(
    repo_root: &Path,
    ralph_dir: &Path,
    marker_path: &Path,
    track_file_path: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    let tracked_wrapper_dir = read_tracked_wrapper_dir(ralph_dir);
    let path_wrapper_dir =
        path_wrapper::find_wrapper_dir_on_path().filter(|p| is_safe_existing_dir(p));
    let wrapper_dir = tracked_wrapper_dir.clone().or(path_wrapper_dir);

    if let Some(ref dir) = wrapper_dir {
        prepend_wrapper_dir_to_path(dir);
    }

    if tracked_wrapper_dir.is_none() {
        if let Some(ref dir) = wrapper_dir {
            logger.warn("Git wrapper tracking file missing or invalid — restoring");
            result.tampering_detected = true;
            result
                .details
                .push("Git wrapper tracking file missing or invalid — restored".to_string());
            if let Err(e) = write_track_file_atomic(repo_root, dir) {
                logger.warn(&format!("Failed to restore wrapper tracking file: {e}"));
            }
        }
    }

    if let Some(wrapper_dir) = wrapper_dir {
        let wrapper_path = wrapper_dir.join("git");
        let wrapper_needs_restore = fs::read_to_string(&wrapper_path).map_or(true, |content| {
            !content.contains("RALPH_AGENT_PHASE_GIT_WRAPPER")
                || !content.contains("unset GIT_EXEC_PATH")
        });

        if wrapper_needs_restore {
            logger.warn("Git wrapper script missing or tampered — restoring");
            result.tampering_detected = true;
            result
                .details
                .push("Git wrapper script missing or tampered — restored".to_string());

            let real_git = find_real_git_excluding(&wrapper_dir).or_else(|| which("git").ok());

            match real_git {
                Some(real_git_path) => {
                    let Some(real_git_str) = real_git_path.to_str() else {
                        logger.warn(
                            "Resolved git binary path is not valid UTF-8; cannot restore wrapper",
                        );
                        return;
                    };
                    let Ok(git_path_escaped) = escape_shell_path(real_git_str) else {
                        logger.warn("Failed to generate safe wrapper script (git path)");
                        return;
                    };
                    let Some(marker_str) = marker_path.to_str() else {
                        logger.warn("Marker path is not valid UTF-8; cannot restore wrapper");
                        return;
                    };
                    let Some(track_str) = track_file_path.to_str() else {
                        logger.warn("Track file path is not valid UTF-8; cannot restore wrapper");
                        return;
                    };
                    let Ok(marker_escaped) = escape_shell_path(marker_str) else {
                        logger.warn("Failed to generate safe wrapper script (marker path)");
                        return;
                    };
                    let Ok(track_escaped) = escape_shell_path(track_str) else {
                        logger.warn("Failed to generate safe wrapper script (track file path)");
                        return;
                    };
                    let scope =
                        match crate::git_helpers::repo::resolve_protection_scope_from(repo_root) {
                            Ok(s) => s,
                            Err(_) => return,
                        };
                    let normalized_repo_root = normalize_protection_scope_path(&scope.repo_root);
                    let normalized_git_dir = normalize_protection_scope_path(&scope.git_dir);
                    let Some(repo_root_str) = normalized_repo_root.to_str() else {
                        logger.warn("Repo root is not valid UTF-8; cannot restore wrapper");
                        return;
                    };
                    let Some(git_dir_str) = normalized_git_dir.to_str() else {
                        logger.warn("Git dir is not valid UTF-8; cannot restore wrapper");
                        return;
                    };
                    let Ok(repo_root_escaped) = escape_shell_path(repo_root_str) else {
                        logger.warn("Failed to generate safe wrapper script (repo root)");
                        return;
                    };
                    let Ok(git_dir_escaped) = escape_shell_path(git_dir_str) else {
                        logger.warn("Failed to generate safe wrapper script (git dir)");
                        return;
                    };

                    let wrapper_content = make_wrapper_content(
                        &git_path_escaped,
                        &marker_escaped,
                        &track_escaped,
                        &repo_root_escaped,
                        &git_dir_escaped,
                    );

                    let tmp_path = wrapper_dir.join(format!(
                        ".git-wrapper.tmp.{}.{}",
                        std::process::id(),
                        std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_nanos()
                    ));

                    match open_wrapper_tmp(&tmp_path, &wrapper_content) {
                        Ok(()) => {
                            #[cfg(unix)]
                            set_wrapper_permissions(&tmp_path, 0o555);
                            #[cfg(windows)]
                            set_wrapper_permissions_windows(&tmp_path);

                            if let Err(e) = fs::rename(&tmp_path, &wrapper_path) {
                                let _ = fs::remove_file(&tmp_path);
                                logger.warn(&format!("Failed to restore wrapper script: {e}"));
                            }
                        }
                        Err(e) => {
                            logger.warn(&format!("Failed to write wrapper temp file: {e}"));
                        }
                    }

                    if real_git_path == wrapper_path {
                        logger.warn("Resolved git binary points to wrapper; wrapper restore may be incomplete");
                    }
                }
                None => {
                    logger.warn("Failed to resolve real git binary; cannot restore wrapper");
                }
            }
        }

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Ok(meta) = fs::metadata(&wrapper_path) {
                let mode = meta.permissions().mode() & 0o777;
                if mode != 0o555 {
                    logger.warn(&format!(
                        "Git wrapper permissions loosened ({mode:#o}) — restoring to 0o555"
                    ));
                    let mut perms = meta.permissions();
                    perms.set_mode(0o555);
                    let _ = fs::set_permissions(&wrapper_path, perms);
                    result.tampering_detected = true;
                    result.details.push(format!(
                        "Git wrapper permissions loosened ({mode:#o}) — restored to 0o555"
                    ));
                }
            }
        }
    } else {
        logger.warn("Git wrapper missing — reinstalling");
        result.tampering_detected = true;
        result
            .details
            .push("Git wrapper missing before agent spawn — reinstalling".to_string());

        let wrapper_dir = match tempfile::Builder::new()
            .prefix(WRAPPER_DIR_PREFIX)
            .tempdir()
        {
            Ok(d) => d.keep(),
            Err(e) => {
                logger.warn(&format!("Failed to create wrapper dir: {e}"));
                return;
            }
        };
        prepend_wrapper_dir_to_path(&wrapper_dir);

        let real_git = find_real_git_excluding(&wrapper_dir).or_else(|| which("git").ok());
        if let Some(real_git_path) = real_git {
            if let Some(real_git_str) = real_git_path.to_str() {
                let marker_p = marker_path;
                let track_p = track_file_path;
                if let (Ok(git_path_escaped), Some(marker_str), Some(track_str)) = (
                    escape_shell_path(real_git_str),
                    marker_p.to_str(),
                    track_p.to_str(),
                ) {
                    if let (Ok(marker_escaped), Ok(track_escaped)) =
                        (escape_shell_path(marker_str), escape_shell_path(track_str))
                    {
                        let scope = match crate::git_helpers::repo::resolve_protection_scope_from(
                            repo_root,
                        ) {
                            Ok(s) => s,
                            Err(_) => return,
                        };
                        let normalized_repo_root =
                            normalize_protection_scope_path(&scope.repo_root);
                        let normalized_git_dir = normalize_protection_scope_path(&scope.git_dir);
                        let Some(repo_root_str) = normalized_repo_root.to_str() else {
                            logger.warn("Repo root is not valid UTF-8; cannot restore wrapper");
                            return;
                        };
                        let Some(git_dir_str) = normalized_git_dir.to_str() else {
                            logger.warn("Git dir is not valid UTF-8; cannot restore wrapper");
                            return;
                        };
                        let Ok(repo_root_escaped) = escape_shell_path(repo_root_str) else {
                            logger.warn("Failed to generate safe wrapper script (repo root)");
                            return;
                        };
                        let Ok(git_dir_escaped) = escape_shell_path(git_dir_str) else {
                            logger.warn("Failed to generate safe wrapper script (git dir)");
                            return;
                        };
                        let wrapper_content = make_wrapper_content(
                            &git_path_escaped,
                            &marker_escaped,
                            &track_escaped,
                            &repo_root_escaped,
                            &git_dir_escaped,
                        );
                        let wrapper_path = wrapper_dir.join("git");
                        if OpenOptions::new()
                            .write(true)
                            .create_new(true)
                            .open(&wrapper_path)
                            .and_then(|mut f| {
                                f.write_all(wrapper_content.as_bytes())?;
                                f.flush()?;
                                let _ = f.sync_all();
                                Ok(())
                            })
                            .is_ok()
                        {
                            #[cfg(unix)]
                            set_wrapper_permissions(&wrapper_path, 0o555);
                        }
                    }
                }
            }
        }

        if let Err(e) = write_track_file_atomic(repo_root, &wrapper_dir) {
            logger.warn(&format!("Failed to write wrapper tracking file: {e}"));
        }
    }
}

#[cfg(unix)]
fn set_wrapper_permissions(path: &Path, mode: u32) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(meta) = fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_mode(mode);
        let _ = fs::set_permissions(path, perms);
    }
}

#[cfg(windows)]
fn set_wrapper_permissions_windows(path: &Path) {
    if let Ok(meta) = fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_readonly(true);
        let _ = fs::set_permissions(path, perms);
        if path.exists() {
            let _ = fs::remove_file(path);
        }
    }
}

fn open_wrapper_tmp(tmp_path: &Path, content: &str) -> io::Result<()> {
    let open_tmp = {
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(tmp_path)
        }
        #[cfg(not(unix))]
        {
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(tmp_path)
        }
    };

    open_tmp.and_then(|mut f| {
        f.write_all(content.as_bytes())?;
        f.flush()?;
        let _ = f.sync_all();
        Ok(())
    })
}

/// Capture the current HEAD OID and write it to `<git-dir>/ralph/head-oid.txt`.
pub(crate) fn capture_head_oid(repo_root: &Path) {
    let Ok(head_oid) = crate::git_helpers::get_current_head_oid_at(repo_root) else {
        return;
    };
    let _ = write_head_oid_file_atomic(repo_root, head_oid.trim());
}

fn write_head_oid_file_atomic(repo_root: &Path, oid: &str) -> io::Result<()> {
    let ralph_dir = crate::git_helpers::repo::ensure_ralph_git_dir(repo_root)?;
    let head_oid_path = ralph_dir.join(HEAD_OID_FILE_NAME);

    if matches!(
        fs::symlink_metadata(&head_oid_path),
        Ok(m) if m.file_type().is_symlink()
    ) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "head-oid path is a symlink; refusing to write baseline",
        ));
    }

    let tmp_path = ralph_dir.join(format!(
        ".head-oid.tmp.{}.{}",
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
            .open(&tmp_path)?;
        tf.write_all(oid.as_bytes())?;
        tf.write_all(b"\n")?;
        tf.flush()?;
        let _ = tf.sync_all();
    }

    #[cfg(unix)]
    set_readonly_mode_if_not_symlink(&tmp_path, 0o444);
    #[cfg(windows)]
    {
        let mut perms = fs::metadata(&tmp_path)?.permissions();
        perms.set_readonly(true);
        fs::set_permissions(&tmp_path, perms)?;
    }

    #[cfg(windows)]
    {
        if head_oid_path.exists() {
            let _ = fs::remove_file(&head_oid_path);
        }
    }
    fs::rename(&tmp_path, &head_oid_path)
}

/// Detect unauthorized commits by comparing current HEAD against stored OID.
pub(crate) fn detect_unauthorized_commit(repo_root: &Path) -> bool {
    let head_oid_path = ralph_git_dir(repo_root).join(HEAD_OID_FILE_NAME);
    if matches!(
        fs::symlink_metadata(&head_oid_path),
        Ok(m) if m.file_type().is_symlink()
    ) {
        return false;
    }
    let Ok(stored_oid) = fs::read_to_string(&head_oid_path) else {
        return false;
    };
    let stored_oid = stored_oid.trim();
    if stored_oid.is_empty() {
        return false;
    }
    let Ok(current_oid) = crate::git_helpers::get_current_head_oid_at(repo_root) else {
        return false;
    };
    current_oid.trim() != stored_oid
}

pub(crate) const HEAD_OID_FILENAME: &str = HEAD_OID_FILE_NAME;
