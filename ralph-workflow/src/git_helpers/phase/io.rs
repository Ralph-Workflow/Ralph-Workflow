// git_helpers/phase/io.rs — boundary module for agent phase lifecycle management.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Agent phase lifecycle management.
//
// Coordinates the start, self-heal check, and end of the agent phase.
// Handles marker creation, wrapper installation, hooks setup, and cleanup.

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
use std::path::{Path, PathBuf};
use which::which;

const HEAD_OID_FILE_NAME: &str = "head-oid.txt";
const WRAPPER_DIR_PREFIX: &str = "ralph-git-wrapper-";

/// Escape a path for safe use in a POSIX shell single-quoted string.
pub(crate) fn escape_shell_path(path: &str) -> std::io::Result<String> {
    escape_shell_single_quoted(path)
}

/// Find the real git binary in PATH, excluding the given wrapper directory.
pub(crate) fn find_real_git_excluding(exclude_dir: &Path) -> Option<PathBuf> {
    let path_var = env::var("PATH").ok()?;
    let wrapper_path = exclude_dir.join("git");
    find_git_in_path(path_var, exclude_dir, &wrapper_path)
}

fn find_git_in_path(path_var: String, exclude_dir: &Path, wrapper_path: &Path) -> Option<PathBuf> {
    path_var.split(':').find_map(|entry| {
        if entry.is_empty() || entry == exclude_dir.to_string_lossy() {
            return None;
        }
        let candidate = Path::new(entry).join("git");
        if candidate == *wrapper_path || !candidate.exists() {
            return None;
        }
        if !is_executable_git(&candidate) {
            return None;
        }
        Some(candidate)
    })
}

#[cfg(unix)]
fn has_execute_bit(candidate: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    fs::metadata(candidate).map_or(true, |meta| {
        let mode = meta.permissions().mode() & 0o777;
        (mode & 0o111) != 0
    })
}

fn is_executable_git(candidate: &Path) -> bool {
    if !matches!(fs::metadata(candidate), Ok(meta) if meta.file_type().is_file()) {
        return false;
    }
    #[cfg(unix)]
    {
        has_execute_bit(candidate)
    }
    #[cfg(not(unix))]
    {
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

fn is_non_regular_file(meta: &fs::Metadata) -> bool {
    let ft = meta.file_type();
    !ft.is_file() || ft.is_symlink()
}

fn quarantine_path_tampered(
    path: &Path,
    kind: &str,
    warn_msg: &str,
    detail_msg: &str,
    fail_detail: &str,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    use crate::git_helpers::repo::quarantine_path_in_place;
    logger.warn(warn_msg);
    result.tampering_detected = true;
    result.details.push(detail_msg.to_string());
    if let Err(e) = quarantine_path_in_place(path, kind) {
        logger.warn(&format!("Failed to quarantine {kind} path: {e}"));
        result.details.push(fail_detail.to_string());
    }
}

fn check_path_is_regular_file(
    path: &Path,
    kind: &str,
    warn_msg: &str,
    detail_msg: &str,
    fail_detail: &str,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    if let Ok(meta) = fs::symlink_metadata(path) {
        if is_non_regular_file(&meta) {
            quarantine_path_tampered(path, kind, warn_msg, detail_msg, fail_detail, result, logger);
        }
    }
}

pub(crate) fn check_marker_integrity(
    ralph_dir: &Path,
    _repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    let marker_path = marker::marker_path_from_ralph_dir(ralph_dir);
    check_path_is_regular_file(
        &marker_path,
        "marker",
        "Enforcement marker is not a regular file — quarantining and recreating",
        "Enforcement marker was not a regular file — quarantined",
        "Marker path quarantine failed",
        result,
        logger,
    );
}

pub(crate) fn check_track_file_integrity(
    ralph_dir: &Path,
    _repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    let track_file_path = track_file_path_for_ralph_dir(ralph_dir);
    check_path_is_regular_file(
        &track_file_path,
        "track",
        "Git wrapper tracking path is not a regular file — quarantining",
        "Git wrapper tracking path was not a regular file — quarantined",
        "Wrapper tracking path quarantine failed",
        result,
        logger,
    );
}

fn remove_symlink_marker(
    marker_path: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    logger.warn("Enforcement marker is a symlink — removing and recreating");
    let _ = fs::remove_file(marker_path);
    result.tampering_detected = true;
    result
        .details
        .push("Enforcement marker was a symlink — removed".to_string());
}

fn recreate_missing_marker(
    marker_path: &Path,
    repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
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

fn read_marker_symlink_state(marker_path: &Path) -> (bool, bool) {
    let marker_meta = fs::symlink_metadata(marker_path).ok();
    let is_symlink = marker_meta
        .as_ref()
        .is_some_and(|meta| meta.file_type().is_symlink());
    let exists_as_file = marker_meta
        .as_ref()
        .is_some_and(|meta| meta.file_type().is_file() && !meta.file_type().is_symlink());
    (is_symlink, exists_as_file)
}

pub(crate) fn check_and_repair_marker_symlink(
    marker_path: &Path,
    repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    let (is_symlink, exists_as_file) = read_marker_symlink_state(marker_path);
    if is_symlink {
        remove_symlink_marker(marker_path, result, logger);
    }
    if !exists_as_file {
        recreate_missing_marker(marker_path, repo_root, result, logger);
    }
}

#[cfg(unix)]
fn restore_marker_perms(
    marker_path: &Path,
    mode: u32,
    meta: &fs::Metadata,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    use std::os::unix::fs::PermissionsExt;
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

#[cfg(unix)]
fn quarantine_marker_in_place(marker_path: &Path, logger: &Logger) -> bool {
    match crate::git_helpers::repo::quarantine_path_in_place(marker_path, "marker-perms") {
        Ok(_) => true,
        Err(e) => {
            logger.warn(&format!("Failed to quarantine marker path: {e}"));
            false
        }
    }
}

#[cfg(unix)]
fn recreate_marker_after_quarantine(
    marker_path: &Path,
    repo_root: &Path,
    logger: &Logger,
) {
    match marker::create_marker_in_repo_root(repo_root) {
        Ok(()) => set_readonly_mode_if_not_symlink(marker_path, 0o444),
        Err(e) => logger.warn(&format!(
            "Failed to recreate enforcement marker after quarantine: {e}"
        )),
    }
}

#[cfg(unix)]
fn quarantine_and_recreate_marker(
    marker_path: &Path,
    repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    logger.warn("Enforcement marker is not a regular file — quarantining");
    result.tampering_detected = true;
    result
        .details
        .push("Enforcement marker was not a regular file — quarantined".to_string());
    if quarantine_marker_in_place(marker_path, logger) {
        recreate_marker_after_quarantine(marker_path, repo_root, logger);
    }
}

#[cfg(unix)]
fn check_marker_file_perms(
    marker_path: &Path,
    repo_root: &Path,
    meta: &fs::Metadata,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    use std::os::unix::fs::PermissionsExt;
    if meta.is_file() {
        let mode = meta.permissions().mode() & 0o777;
        if mode != 0o444 {
            restore_marker_perms(marker_path, mode, meta, result, logger);
        }
    } else {
        quarantine_and_recreate_marker(marker_path, repo_root, result, logger);
    }
}

#[cfg(unix)]
fn check_and_repair_marker_permissions_unix(
    marker_path: &Path,
    repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    if matches!(
        fs::symlink_metadata(marker_path),
        Ok(meta) if meta.file_type().is_symlink()
    ) {
        return;
    }
    if let Ok(meta) = fs::metadata(marker_path) {
        check_marker_file_perms(marker_path, repo_root, &meta, result, logger);
    }
}

pub(crate) fn check_and_repair_marker_permissions(
    marker_path: &Path,
    repo_root: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    #[cfg(unix)]
    check_and_repair_marker_permissions_unix(marker_path, repo_root, result, logger);
}

#[cfg(unix)]
fn repair_symlink_track_file(
    track_file_path: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
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
}

#[cfg(unix)]
fn quarantine_dir_track_file(
    track_file_path: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
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

#[cfg(unix)]
fn restore_track_file_perms(
    track_file_path: &Path,
    mode: u32,
    meta: &fs::Metadata,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    use std::os::unix::fs::PermissionsExt;
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

#[cfg(unix)]
fn check_track_file_meta(
    track_file_path: &Path,
    meta: &fs::Metadata,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    use std::os::unix::fs::PermissionsExt;
    if meta.is_dir() {
        quarantine_dir_track_file(track_file_path, result, logger);
    }
    if meta.is_file() {
        let mode = meta.permissions().mode() & 0o777;
        if mode != 0o444 {
            restore_track_file_perms(track_file_path, mode, meta, result, logger);
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
        if matches!(
            fs::symlink_metadata(track_file_path),
            Ok(m) if m.file_type().is_symlink()
        ) {
            repair_symlink_track_file(track_file_path, result, logger);
        } else if let Ok(meta) = fs::metadata(track_file_path) {
            check_track_file_meta(track_file_path, &meta, result, logger);
        }
    }
}

fn restore_tracking_file(
    repo_root: &Path,
    dir: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    logger.warn("Git wrapper tracking file missing or invalid — restoring");
    result.tampering_detected = true;
    result
        .details
        .push("Git wrapper tracking file missing or invalid — restored".to_string());
    if let Err(e) = write_track_file_atomic(repo_root, dir) {
        logger.warn(&format!("Failed to restore wrapper tracking file: {e}"));
    }
}

fn check_wrapper_needs_restore(wrapper_path: &Path) -> bool {
    fs::read_to_string(wrapper_path).map_or(true, |content| {
        !content.contains("RALPH_AGENT_PHASE_GIT_WRAPPER")
            || !content.contains("unset GIT_EXEC_PATH")
    })
}

fn escape_git_path(real_git_str: &str, logger: &Logger) -> Option<String> {
    match escape_shell_path(real_git_str) {
        Ok(s) => Some(s),
        Err(_) => {
            logger.warn("Failed to generate safe wrapper script (git path)");
            None
        }
    }
}

fn path_to_escaped_str(path: &Path, label: &str, logger: &Logger) -> Option<String> {
    let s = path.to_str().or_else(|| {
        logger.warn(&format!("{label} is not valid UTF-8; cannot restore wrapper"));
        None
    })?;
    match escape_shell_path(s) {
        Ok(escaped) => Some(escaped),
        Err(_) => {
            logger.warn(&format!("Failed to generate safe wrapper script ({label})"));
            None
        }
    }
}

fn escape_wrapper_paths(
    real_git_str: &str,
    marker_path: &Path,
    track_file_path: &Path,
    logger: &Logger,
) -> Option<(String, String, String)> {
    let git_path_escaped = escape_git_path(real_git_str, logger)?;
    let marker_escaped = path_to_escaped_str(marker_path, "marker path", logger)?;
    let track_escaped = path_to_escaped_str(track_file_path, "track file path", logger)?;
    Some((git_path_escaped, marker_escaped, track_escaped))
}

fn resolve_scope_escaped_paths(
    repo_root: &Path,
    logger: &Logger,
) -> Option<(String, String)> {
    let scope = crate::git_helpers::repo::resolve_protection_scope_from(repo_root).ok()?;
    let repo_root_escaped = path_to_escaped_str(
        &normalize_protection_scope_path(&scope.repo_root),
        "repo root",
        logger,
    )?;
    let git_dir_escaped = path_to_escaped_str(
        &normalize_protection_scope_path(&scope.git_dir),
        "git dir",
        logger,
    )?;
    Some((repo_root_escaped, git_dir_escaped))
}

fn write_wrapper_script(
    wrapper_dir: &Path,
    wrapper_path: &Path,
    wrapper_content: &str,
    logger: &Logger,
) {
    let tmp_path = make_wrapper_tmp_path(wrapper_dir);
    match open_wrapper_tmp(&tmp_path, wrapper_content) {
        Ok(()) => {
            #[cfg(unix)]
            set_wrapper_permissions(&tmp_path, 0o555);
            #[cfg(windows)]
            set_wrapper_permissions_windows(&tmp_path);
            if let Err(e) = fs::rename(&tmp_path, wrapper_path) {
                let _ = fs::remove_file(&tmp_path);
                logger.warn(&format!("Failed to restore wrapper script: {e}"));
            }
        }
        Err(e) => {
            logger.warn(&format!("Failed to write wrapper temp file: {e}"));
        }
    }
}

fn build_and_write_wrapper(
    repo_root: &Path,
    wrapper_dir: &Path,
    wrapper_path: &Path,
    real_git_path: &Path,
    marker_path: &Path,
    track_file_path: &Path,
    logger: &Logger,
) {
    let Some(real_git_str) = real_git_path.to_str() else {
        logger.warn("Resolved git binary path is not valid UTF-8; cannot restore wrapper");
        return;
    };
    let Some((git_path_escaped, marker_escaped, track_escaped)) =
        escape_wrapper_paths(real_git_str, marker_path, track_file_path, logger)
    else {
        return;
    };
    let Some((repo_root_escaped, git_dir_escaped)) =
        resolve_scope_escaped_paths(repo_root, logger)
    else {
        return;
    };
    let wrapper_content = make_wrapper_content(
        &git_path_escaped,
        &marker_escaped,
        &track_escaped,
        &repo_root_escaped,
        &git_dir_escaped,
    );
    write_wrapper_script(wrapper_dir, wrapper_path, &wrapper_content, logger);
    if real_git_path == wrapper_path {
        logger.warn(
            "Resolved git binary points to wrapper; wrapper restore may be incomplete",
        );
    }
}

fn restore_wrapper_script(
    repo_root: &Path,
    wrapper_dir: &Path,
    marker_path: &Path,
    track_file_path: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    logger.warn("Git wrapper script missing or tampered — restoring");
    result.tampering_detected = true;
    result
        .details
        .push("Git wrapper script missing or tampered — restored".to_string());
    let real_git = find_real_git_excluding(wrapper_dir).or_else(|| which("git").ok());
    match real_git {
        Some(real_git_path) => {
            let wrapper_path = wrapper_dir.join("git");
            build_and_write_wrapper(
                repo_root,
                wrapper_dir,
                &wrapper_path,
                &real_git_path,
                marker_path,
                track_file_path,
                logger,
            );
        }
        None => {
            logger.warn("Failed to resolve real git binary; cannot restore wrapper");
        }
    }
}

#[cfg(unix)]
fn repair_wrapper_permissions(
    wrapper_path: &Path,
    mode: u32,
    meta: &fs::Metadata,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    use std::os::unix::fs::PermissionsExt;
    logger.warn(&format!(
        "Git wrapper permissions loosened ({mode:#o}) — restoring to 0o555"
    ));
    let mut perms = meta.permissions();
    perms.set_mode(0o555);
    let _ = fs::set_permissions(wrapper_path, perms);
    result.tampering_detected = true;
    result.details.push(format!(
        "Git wrapper permissions loosened ({mode:#o}) — restored to 0o555"
    ));
}

#[cfg(unix)]
fn check_wrapper_permissions(
    wrapper_path: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(meta) = fs::metadata(wrapper_path) {
        let mode = meta.permissions().mode() & 0o777;
        if mode != 0o555 {
            repair_wrapper_permissions(wrapper_path, mode, &meta, result, logger);
        }
    }
}

fn create_fresh_wrapper_dir(logger: &Logger) -> Option<PathBuf> {
    match tempfile::Builder::new()
        .prefix(WRAPPER_DIR_PREFIX)
        .tempdir()
    {
        Ok(d) => Some(d.keep()),
        Err(e) => {
            logger.warn(&format!("Failed to create wrapper dir: {e}"));
            None
        }
    }
}

fn write_wrapper_to_dir(
    repo_root: &Path,
    wrapper_dir: &Path,
    marker_path: &Path,
    track_file_path: &Path,
    logger: &Logger,
) {
    let real_git = find_real_git_excluding(wrapper_dir).or_else(|| which("git").ok());
    let Some(real_git_path) = real_git else {
        return;
    };
    let wrapper_path = wrapper_dir.join("git");
    build_and_write_wrapper(
        repo_root,
        wrapper_dir,
        &wrapper_path,
        &real_git_path,
        marker_path,
        track_file_path,
        logger,
    );
}

fn install_fresh_wrapper(
    repo_root: &Path,
    marker_path: &Path,
    track_file_path: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    logger.warn("Git wrapper missing — reinstalling");
    result.tampering_detected = true;
    result
        .details
        .push("Git wrapper missing before agent spawn — reinstalling".to_string());
    let Some(wrapper_dir) = create_fresh_wrapper_dir(logger) else {
        return;
    };
    prepend_wrapper_dir_to_path(&wrapper_dir);
    write_wrapper_to_dir(repo_root, &wrapper_dir, marker_path, track_file_path, logger);
    if let Err(e) = write_track_file_atomic(repo_root, &wrapper_dir) {
        logger.warn(&format!("Failed to write wrapper tracking file: {e}"));
    }
}

fn check_or_restore_existing_wrapper(
    repo_root: &Path,
    wrapper_dir: &Path,
    marker_path: &Path,
    track_file_path: &Path,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    let wrapper_path = wrapper_dir.join("git");
    if check_wrapper_needs_restore(&wrapper_path) {
        restore_wrapper_script(
            repo_root,
            wrapper_dir,
            marker_path,
            track_file_path,
            result,
            logger,
        );
    }
    #[cfg(unix)]
    check_wrapper_permissions(&wrapper_path, result, logger);
}

fn maybe_restore_tracking_file(
    repo_root: &Path,
    tracked_wrapper_dir: &Option<PathBuf>,
    wrapper_dir: &Option<PathBuf>,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    if tracked_wrapper_dir.is_none() {
        if let Some(ref dir) = wrapper_dir {
            restore_tracking_file(repo_root, dir, result, logger);
        }
    }
}

fn dispatch_wrapper_check_or_install(
    repo_root: &Path,
    marker_path: &Path,
    track_file_path: &Path,
    wrapper_dir: &Option<PathBuf>,
    result: &mut ProtectionCheckResult,
    logger: &Logger,
) {
    match wrapper_dir {
        Some(ref dir) => {
            check_or_restore_existing_wrapper(
                repo_root,
                dir,
                marker_path,
                track_file_path,
                result,
                logger,
            );
        }
        None => {
            install_fresh_wrapper(repo_root, marker_path, track_file_path, result, logger);
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
    maybe_restore_tracking_file(repo_root, &tracked_wrapper_dir, &wrapper_dir, result, logger);
    dispatch_wrapper_check_or_install(repo_root, marker_path, track_file_path, &wrapper_dir, result, logger);
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

fn make_wrapper_tmp_path(wrapper_dir: &Path) -> PathBuf {
    wrapper_dir.join(format!(
        ".git-wrapper.tmp.{}.{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ))
}

fn open_wrapper_tmp(tmp_path: &Path, content: &str) -> std::io::Result<()> {
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
        std::io::Write::write_all(&mut f, content.as_bytes())?;
        std::io::Write::flush(&mut f)?;
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

fn make_head_oid_tmp_path(ralph_dir: &Path) -> PathBuf {
    ralph_dir.join(format!(
        ".head-oid.tmp.{}.{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ))
}

fn write_head_oid_to_tmp(tmp_path: &Path, oid: &str) -> std::io::Result<()> {
    let mut tf = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(tmp_path)?;
    std::io::Write::write_all(&mut tf, oid.as_bytes())?;
    std::io::Write::write_all(&mut tf, b"\n")?;
    std::io::Write::flush(&mut tf)?;
    let _ = tf.sync_all();
    Ok(())
}

fn set_head_oid_tmp_readonly(tmp_path: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    set_readonly_mode_if_not_symlink(tmp_path, 0o444);
    #[cfg(windows)]
    {
        let mut perms = fs::metadata(tmp_path)?.permissions();
        perms.set_readonly(true);
        fs::set_permissions(tmp_path, perms)?;
    }
    Ok(())
}

fn guard_head_oid_not_symlink(head_oid_path: &Path) -> std::io::Result<()> {
    if matches!(
        fs::symlink_metadata(head_oid_path),
        Ok(m) if m.file_type().is_symlink()
    ) {
        Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "head-oid path is a symlink; refusing to write baseline",
        ))
    } else {
        Ok(())
    }
}

fn write_head_oid_file_atomic(repo_root: &Path, oid: &str) -> std::io::Result<()> {
    let ralph_dir = crate::git_helpers::repo::ensure_ralph_git_dir(repo_root)?;
    let head_oid_path = ralph_dir.join(HEAD_OID_FILE_NAME);
    guard_head_oid_not_symlink(&head_oid_path)?;
    let tmp_path = make_head_oid_tmp_path(&ralph_dir);
    write_head_oid_to_tmp(&tmp_path, oid)?;
    set_head_oid_tmp_readonly(&tmp_path)?;
    #[cfg(windows)]
    {
        if head_oid_path.exists() {
            let _ = fs::remove_file(&head_oid_path);
        }
    }
    fs::rename(&tmp_path, &head_oid_path)
}

fn is_head_oid_symlink(head_oid_path: &Path) -> bool {
    matches!(
        fs::symlink_metadata(head_oid_path),
        Ok(m) if m.file_type().is_symlink()
    )
}

fn read_stored_oid(head_oid_path: &Path) -> Option<String> {
    let stored = fs::read_to_string(head_oid_path).ok()?;
    let trimmed = stored.trim().to_string();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed)
    }
}

/// Detect unauthorized commits by comparing current HEAD against stored OID.
pub(crate) fn detect_unauthorized_commit(repo_root: &Path) -> bool {
    let head_oid_path = ralph_git_dir(repo_root).join(HEAD_OID_FILE_NAME);
    if is_head_oid_symlink(&head_oid_path) {
        return false;
    }
    let Some(stored_oid) = read_stored_oid(&head_oid_path) else {
        return false;
    };
    let Ok(current_oid) = crate::git_helpers::get_current_head_oid_at(repo_root) else {
        return false;
    };
    current_oid.trim() != stored_oid
}

pub(crate) const HEAD_OID_FILENAME: &str = HEAD_OID_FILE_NAME;
