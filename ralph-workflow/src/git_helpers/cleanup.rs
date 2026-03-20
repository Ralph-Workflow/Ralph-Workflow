//! Cleanup utilities for agent-phase protection artifacts.
//!
//! Handles cleanup of marker files, wrapper dirs, track files, HEAD OID files,
//! and other agent-phase protection artifacts. Used for both graceful shutdown
//! and crash recovery.

use super::config_state::HOOKS_PATH_STATE_FILE;
use super::marker::{
    add_owner_write_if_not_symlink, marker_path_from_ralph_dir, remove_legacy_marker,
};
use super::path_wrapper::{
    cleanup_stray_tmp_files, read_tracked_wrapper_dir, remove_wrapper_dir_and_entry,
    track_file_path_for_ralph_dir,
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
    if let Some(wrapper_dir) = read_tracked_wrapper_dir(ralph_dir) {
        remove_wrapper_dir_and_entry(&wrapper_dir);
    }
    add_owner_write_if_not_symlink(&track_file);
    let _ = fs::remove_file(&track_file);
}

pub(crate) fn cleanup_agent_phase_at(
    repo_root: &Path,
    stored_ralph_dir: Option<&Path>,
    stored_hooks_dir: Option<&Path>,
) {
    let computed_ralph_dir;
    let ralph_dir = if let Some(ralph_dir) = stored_ralph_dir {
        ralph_dir
    } else {
        computed_ralph_dir = ralph_git_dir(repo_root);
        &computed_ralph_dir
    };
    let resolved_hooks_dir = stored_hooks_dir.map(PathBuf::from).or_else(|| {
        crate::git_helpers::repo::resolve_protection_scope_from(repo_root)
            .ok()
            .map(|scope| scope.hooks_dir)
    });

    end_agent_phase_at_ralph_dir(repo_root, ralph_dir);
    cleanup_git_wrapper_dir(ralph_dir);

    if crate::git_helpers::repo::resolve_protection_scope_from(repo_root).is_ok() {
        crate::git_helpers::hooks::uninstall_hooks_silent_at(repo_root);
    } else if let Some(hooks_dir) = resolved_hooks_dir.as_deref() {
        crate::git_helpers::hooks::uninstall_hooks_silent_in_hooks_dir(hooks_dir);
    } else {
        crate::git_helpers::hooks::uninstall_hooks_silent_at(repo_root);
    }

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

pub(crate) fn verify_ralph_dir_removed(repo_root: &Path) -> Vec<String> {
    let ralph_dir = ralph_git_dir(repo_root);
    let Ok(ralph_dir_exists) = sanitize_ralph_git_dir_at(&ralph_dir) else {
        return vec![format!(
            "could not sanitize ralph directory before verification: {}",
            ralph_dir.display()
        )];
    };
    if !ralph_dir_exists {
        return Vec::new();
    }

    if !ralph_dir.exists() {
        return Vec::new();
    }

    let remaining = vec![format!("directory still exists: {}", ralph_dir.display())];
    let dir_issues = inspect_ralph_dir_contents(&ralph_dir);
    if dir_issues.is_empty() {
        remaining
    } else {
        remaining.into_iter().chain(dir_issues).collect()
    }
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

fn check_track_file_issues(track_file: &Path) -> Vec<String> {
    if !track_file.exists() {
        return Vec::new();
    }

    let mut issues = vec![format!("track file still exists: {}", track_file.display())];

    let content = match fs::read_to_string(track_file) {
        Ok(c) => c,
        Err(_) => return issues,
    };

    let dir = PathBuf::from(content.trim());
    if dir.exists() {
        issues.push(format!("wrapper temp dir still exists: {}", dir.display()));
    }

    issues
}

pub(crate) fn verify_wrapper_cleaned(repo_root: &Path) -> Vec<String> {
    let track_file = track_file_path_for_ralph_dir(&ralph_git_dir(repo_root));
    check_track_file_issues(&track_file)
}

pub(crate) fn cleanup_orphaned_marker(logger: &Logger) -> std::io::Result<()> {
    let repo_root = crate::git_helpers::get_repo_root()?;
    let legacy_marker = repo_root.join(".no_agent_commit");
    if fs::symlink_metadata(&legacy_marker).is_ok() {
        add_owner_write_if_not_symlink(&legacy_marker);
        fs::remove_file(&legacy_marker)?;
        logger.success("Removed orphaned enforcement marker");
        return Ok(());
    }

    let ralph_dir = ralph_git_dir(&repo_root);
    if !sanitize_ralph_git_dir_at(&ralph_dir)? {
        logger.info("No orphaned marker found");
        return Ok(());
    }
    let marker_path = marker_path_from_ralph_dir(&ralph_dir);

    if fs::symlink_metadata(&marker_path).is_ok() {
        add_owner_write_if_not_symlink(&marker_path);
        fs::remove_file(&marker_path)?;
        logger.success("Removed orphaned enforcement marker");
    } else {
        logger.info("No orphaned marker found");
    }
    Ok(())
}
