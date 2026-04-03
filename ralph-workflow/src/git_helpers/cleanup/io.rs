// git_helpers/cleanup/io.rs — boundary module for cleanup utilities for agent-phase protection artifacts.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Cleanup utilities for agent-phase protection artifacts.
//
// Handles cleanup of marker files, wrapper dirs, track files, HEAD OID files,
// and other agent-phase protection artifacts. Used for both graceful shutdown
// and crash recovery.

use super::config_state::HOOKS_PATH_STATE_FILE;
use super::marker::{
    add_owner_write_if_not_symlink, marker_path_from_ralph_dir, remove_legacy_marker,
};
use super::path_wrapper::{
    cleanup_stray_tmp_files, remove_wrapper_dir_and_entry, track_file_path_for_ralph_dir,
};
use crate::git_helpers::repo::{ralph_git_dir, sanitize_ralph_git_dir_at};
use crate::logger::Logger;
use std::fs;
use std::path::{Path, PathBuf};

const WORKTREE_CONFIG_STATE_FILE: &str = "worktree-config.previous";

fn cleanup_hook_state_files(ralph_dir: &Path) {
    [HOOKS_PATH_STATE_FILE, WORKTREE_CONFIG_STATE_FILE]
        .iter()
        .for_each(|file_name| {
            let path = ralph_dir.join(file_name);
            add_owner_write_if_not_symlink(&path);
            let _ = fs::remove_file(path);
        });
}

fn remove_scoped_hooks_dir(ralph_dir: &Path) {
    let _ = fs::remove_dir(ralph_dir.join("hooks"));
}

fn cleanup_fallback_ralph_dir(repo_root: &Path) {
    let fallback = repo_root.join(".git/ralph");
    cleanup_hook_state_files(&fallback);
    remove_scoped_hooks_dir(&fallback);
    cleanup_stray_tmp_files(&fallback);
    remove_ralph_dir_best_effort(&fallback);
}

fn remove_ralph_dir_best_effort(ralph_dir: &Path) {
    if fs::remove_dir(ralph_dir).is_ok() {
        return;
    }
    let Ok(meta) = fs::symlink_metadata(ralph_dir) else {
        return;
    };
    if meta.file_type().is_symlink() || !meta.is_dir() {
        return;
    }
    let _ = fs::remove_dir_all(ralph_dir);
}

fn end_agent_phase_at_ralph_dir(repo_root: &Path, ralph_dir: &Path) {
    remove_legacy_marker(repo_root);

    let ralph_dir_ok = sanitize_ralph_git_dir_at(ralph_dir).unwrap_or(false);

    let marker_path = marker_path_from_ralph_dir(ralph_dir);
    add_owner_write_if_not_symlink(&marker_path);
    let _ = fs::remove_file(&marker_path);

    if ralph_dir_ok {
        remove_head_oid_file(ralph_dir);
        cleanup_stray_tmp_files(ralph_dir);
        let _ = fs::remove_dir(ralph_dir);
    }
}

fn remove_head_oid_file(ralph_dir: &Path) {
    let head_oid_path = ralph_dir.join(super::phase::HEAD_OID_FILENAME);
    if fs::symlink_metadata(&head_oid_path).is_err() {
        return;
    }
    add_owner_write_if_not_symlink(&head_oid_path);
    let _ = fs::remove_file(&head_oid_path);
}

fn cleanup_git_wrapper_dir(ralph_dir: &Path) {
    let track_file = track_file_path_for_ralph_dir(ralph_dir);
    if let Ok(content) = fs::read_to_string(&track_file) {
        let wrapper_dir = PathBuf::from(content.trim());
        remove_wrapper_dir_and_entry(&wrapper_dir);
    }
    add_owner_write_if_not_symlink(&track_file);
    let _ = fs::remove_file(&track_file);
}

fn resolve_ralph_dir<'a>(
    stored_ralph_dir: Option<&'a Path>,
    computed: &'a mut Option<PathBuf>,
    repo_root: &Path,
) -> &'a Path {
    if let Some(dir) = stored_ralph_dir {
        return dir;
    }
    *computed = Some(ralph_git_dir(repo_root));
    computed.as_deref().unwrap()
}

fn resolve_hooks_dir_for_cleanup(
    repo_root: &Path,
    stored_hooks_dir: Option<&Path>,
) -> Option<PathBuf> {
    stored_hooks_dir.map(PathBuf::from).or_else(|| {
        crate::git_helpers::repo::resolve_protection_scope_from(repo_root)
            .ok()
            .map(|scope| scope.hooks_dir)
    })
}

fn uninstall_hooks_for_cleanup(repo_root: &Path, resolved_hooks_dir: &Option<PathBuf>) {
    if crate::git_helpers::repo::resolve_protection_scope_from(repo_root).is_ok() {
        crate::git_helpers::hooks::uninstall_hooks_silent_at(repo_root);
    } else if let Some(hooks_dir) = resolved_hooks_dir.as_deref() {
        crate::git_helpers::hooks::uninstall_hooks_silent_in_hooks_dir(hooks_dir);
    } else {
        crate::git_helpers::hooks::uninstall_hooks_silent_at(repo_root);
    }
}

pub(crate) fn cleanup_agent_phase_at(
    repo_root: &Path,
    stored_ralph_dir: Option<&Path>,
    stored_hooks_dir: Option<&Path>,
) {
    let mut computed_ralph_dir = None;
    let ralph_dir = resolve_ralph_dir(stored_ralph_dir, &mut computed_ralph_dir, repo_root);
    let resolved_hooks_dir = resolve_hooks_dir_for_cleanup(repo_root, stored_hooks_dir);

    end_agent_phase_at_ralph_dir(repo_root, ralph_dir);
    cleanup_git_wrapper_dir(ralph_dir);
    uninstall_hooks_for_cleanup(repo_root, &resolved_hooks_dir);

    cleanup_hook_state_files(ralph_dir);
    remove_scoped_hooks_dir(ralph_dir);
    cleanup_stray_tmp_files(ralph_dir);
    remove_ralph_dir_best_effort(ralph_dir);
    cleanup_fallback_ralph_dir(repo_root);
}

pub(crate) fn cleanup_prior_wrapper(repo_root: &Path) {
    let ralph_dir = ralph_git_dir(repo_root);
    let Ok(ralph_dir_exists) = sanitize_ralph_git_dir_at(&ralph_dir) else {
        return;
    };
    if !ralph_dir_exists {
        return;
    }

    let wrapper_dir = resolve_wrapper_dir_from_track_file(&ralph_dir);
    if let Some(_dir) = wrapper_dir {
        let _ = fs::remove_file(track_file_path_for_ralph_dir(&ralph_dir));
    }
}

fn resolve_wrapper_dir_from_track_file(ralph_dir: &Path) -> Option<PathBuf> {
    let track_file = track_file_path_for_ralph_dir(ralph_dir);
    let content = fs::read_to_string(&track_file).ok()?;
    let dir = PathBuf::from(content.trim());
    if remove_wrapper_dir_and_entry(&dir) {
        Some(dir)
    } else {
        None
    }
}

pub(crate) fn remove_ralph_dir(repo_root: &Path) -> bool {
    let ralph_dir = ralph_git_dir(repo_root);
    let Ok(ralph_dir_exists) = sanitize_ralph_git_dir_at(&ralph_dir) else {
        return !ralph_dir.exists();
    };
    if !ralph_dir_exists {
        return true;
    }

    cleanup_stray_tmp_files(&ralph_dir);
    remove_scoped_hooks_dir(&ralph_dir);
    match fs::remove_dir(&ralph_dir) {
        Ok(()) => true,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => true,
        Err(_) => !ralph_dir.exists(),
    }
}

fn build_dir_still_exists_issues(ralph_dir: &Path) -> Vec<String> {
    let mut issues = vec![format!("directory still exists: {}", ralph_dir.display())];
    issues.extend(inspect_ralph_dir_contents(ralph_dir));
    issues
}

pub(crate) fn verify_ralph_dir_removed(repo_root: &Path) -> Vec<String> {
    let ralph_dir = ralph_git_dir(repo_root);
    let Ok(ralph_dir_exists) = sanitize_ralph_git_dir_at(&ralph_dir) else {
        return vec![format!(
            "could not sanitize ralph directory before verification: {}",
            ralph_dir.display()
        )];
    };
    if !ralph_dir_exists || !ralph_dir.exists() {
        return Vec::new();
    }
    build_dir_still_exists_issues(&ralph_dir)
}

fn inspect_ralph_dir_contents(ralph_dir: &Path) -> Vec<String> {
    use itertools::Itertools;
    match fs::read_dir(ralph_dir) {
        Ok(entries) => {
            let names: Vec<_> = entries
                .filter_map(Result::ok)
                .map(|entry| entry.file_name().to_string_lossy().into_owned())
                .sorted()
                .collect();
            if names.is_empty() {
                Vec::new()
            } else {
                vec![format!("remaining entries: {}", names.join(", "))]
            }
        }
        Err(err) => vec![format!("could not inspect directory contents: {err}")],
    }
}

fn wrapper_dir_still_exists_issue(track_file: &Path) -> Option<String> {
    let content = fs::read_to_string(track_file).ok()?;
    let dir = PathBuf::from(content.trim());
    dir.exists()
        .then(|| format!("wrapper temp dir still exists: {}", dir.display()))
}

fn check_track_file_issues(track_file: &Path) -> Vec<String> {
    if !track_file.exists() {
        return Vec::new();
    }
    let mut issues = vec![format!("track file still exists: {}", track_file.display())];
    issues.extend(wrapper_dir_still_exists_issue(track_file));
    issues
}

pub(crate) fn verify_wrapper_cleaned(repo_root: &Path) -> Vec<String> {
    let track_file = track_file_path_for_ralph_dir(&ralph_git_dir(repo_root));
    check_track_file_issues(&track_file)
}

fn try_remove_legacy_marker(repo_root: &Path) -> std::io::Result<bool> {
    let legacy_marker = repo_root.join(".no_agent_commit");
    if fs::symlink_metadata(&legacy_marker).is_err() {
        return Ok(false);
    }
    add_owner_write_if_not_symlink(&legacy_marker);
    fs::remove_file(&legacy_marker)?;
    Ok(true)
}

fn try_remove_ralph_dir_marker(repo_root: &Path) -> std::io::Result<bool> {
    let ralph_dir = ralph_git_dir(repo_root);
    if !sanitize_ralph_git_dir_at(&ralph_dir)? {
        return Ok(false);
    }
    let marker_path = marker_path_from_ralph_dir(&ralph_dir);
    if fs::symlink_metadata(&marker_path).is_err() {
        return Ok(false);
    }
    add_owner_write_if_not_symlink(&marker_path);
    fs::remove_file(&marker_path)?;
    Ok(true)
}

pub(crate) fn cleanup_orphaned_marker(logger: &Logger) -> std::io::Result<()> {
    let repo_root = crate::git_helpers::get_repo_root()?;
    let removed = try_remove_legacy_marker(&repo_root)? || try_remove_ralph_dir_marker(&repo_root)?;
    if removed {
        logger.success("Removed orphaned enforcement marker");
    } else {
        logger.info("No orphaned marker found");
    }
    Ok(())
}
