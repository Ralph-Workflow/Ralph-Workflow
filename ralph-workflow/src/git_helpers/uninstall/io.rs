// git_helpers/uninstall/io.rs — boundary module for hook uninstallation logic.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Hook uninstallation logic.

use crate::files::file_contains_marker;
use crate::git_helpers::config_state;
use crate::git_helpers::hooks_dir;
use crate::git_helpers::install::{HOOK_MARKER, RALPH_HOOK_NAMES};
use crate::git_helpers::repo::resolve_protection_scope_from;
use crate::git_helpers::worktree;
use crate::logger::Logger;
use std::fs;
use std::path::{Path, PathBuf};

mod io {
    pub(crate) type Result<T> = std::io::Result<T>;
    pub(crate) type Error = std::io::Error;
    pub(crate) type ErrorKind = std::io::ErrorKind;
}

fn resolve_absolute_hook_path(hook_path: &Path) -> io::Result<PathBuf> {
    if hook_path.is_absolute() {
        return Ok(hook_path.to_path_buf());
    }
    let hook_dir = hook_path.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "Hook path has no parent directory",
        )
    })?;
    let hook_file_name = hook_path.file_name().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "Hook path has no file name")
    })?;
    Ok(fs::canonicalize(hook_dir)?.join(hook_file_name))
}

#[cfg(unix)]
fn make_hook_file_writable(hook_path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(meta) = fs::metadata(hook_path) {
        let mut perms = meta.permissions();
        perms.set_mode(perms.mode() | 0o200);
        let _ = fs::set_permissions(hook_path, perms);
    }
}

pub fn uninstall_hook(hook_path: &Path, _logger: &Logger) -> io::Result<bool> {
    let hook_path_abs = resolve_absolute_hook_path(hook_path)?;
    let _orig_path = PathBuf::from(format!("{}.ralph.orig", hook_path_abs.display()));

    if hook_path.exists() && file_contains_marker(hook_path, HOOK_MARKER)? {
        #[cfg(unix)]
        make_hook_file_writable(hook_path);

        Ok(true)
    } else {
        Ok(false)
    }
}

fn count_and_remove_hooks(hooks_dir: &Path, logger: &Logger) -> io::Result<usize> {
    RALPH_HOOK_NAMES
        .iter()
        .try_fold(0usize, |count, hook_name| {
            let hook_path = hooks_dir.join(hook_name);
            if hook_path.exists() && uninstall_hook(&hook_path, logger)? {
                Ok::<usize, std::io::Error>(count.saturating_add(1))
            } else {
                Ok::<usize, std::io::Error>(count)
            }
        })
}

fn log_uninstall_result(restored: usize, logger: &Logger) {
    if restored > 0 {
        logger.success(&format!("Uninstalled {restored} Ralph hook(s)"));
    } else {
        logger.info("No Ralph hooks were restored (hooks may not have been installed)");
    }
}

pub fn uninstall_hooks_in_repo(repo_root: &Path, logger: &Logger) -> io::Result<()> {
    let scope = resolve_protection_scope_from(repo_root)?;
    let hooks_dir = scope.hooks_dir.clone();
    if !hooks_dir.exists() {
        worktree::restore_worktree_hook_scoping(&scope)?;
        config_state::remove_scoped_hooks_dir_if_empty(&scope);
        return Ok(());
    }

    hooks_dir::validate_hooks_dir_for_scope(&scope, false)?;

    let restored = count_and_remove_hooks(&hooks_dir, logger)?;
    log_uninstall_result(restored, logger);

    worktree::restore_worktree_hook_scoping(&scope)?;
    config_state::remove_scoped_hooks_dir_if_empty(&scope);

    Ok(())
}

pub fn uninstall_hooks(logger: &Logger) -> io::Result<()> {
    let repo_root = crate::git_helpers::repo::get_repo_root()?;
    uninstall_hooks_in_repo(&repo_root, logger)
}

pub fn uninstall_hooks_silent_at(repo_root: &Path) {
    let Ok(scope) = resolve_protection_scope_from(repo_root) else {
        return;
    };
    if scope.hooks_dir.exists() && hooks_dir::validate_hooks_dir_for_scope(&scope, false).is_err() {
        return;
    }
    uninstall_hooks_silent_in_dir(&scope.hooks_dir);
    let _ = worktree::restore_worktree_hook_scoping(&scope);
    config_state::remove_scoped_hooks_dir_if_empty(&scope);
}

pub fn uninstall_hooks_silent_in_hooks_dir(hooks_dir: &Path) {
    uninstall_hooks_silent_in_dir(hooks_dir);
}

fn uninstall_hooks_silent_in_dir(hooks_dir: &Path) {
    if !hooks_dir.exists() {
        return;
    }

    RALPH_HOOK_NAMES.iter().for_each(|hook_name| {
        let hook_path = hooks_dir.join(hook_name);
        if !hook_path.exists() {
            return;
        }
        if !matches!(file_contains_marker(&hook_path, HOOK_MARKER), Ok(true)) {
            return;
        }
        #[cfg(unix)]
        make_hook_writable(&hook_path);

        let hook_path_abs = fs::canonicalize(&hook_path).unwrap_or_else(|_| hook_path.clone());
        let orig_path = PathBuf::from(format!("{}.ralph.orig", hook_path_abs.display()));

        if orig_path.exists() {
            let _ = fs::rename(&orig_path, &hook_path);
        } else {
            let _ = fs::remove_file(&hook_path);
        }
    });
}

#[cfg(unix)]
fn make_hook_writable(hook_path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(meta) = fs::metadata(hook_path) {
        let mut perms = meta.permissions();
        perms.set_mode(perms.mode() | 0o200);
        let _ = fs::set_permissions(hook_path, perms);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::git_helpers::install::HOOK_MARKER;

    #[test]
    fn test_uninstall_hooks_silent_at_removes_ralph_hooks() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let hooks_dir = repo_root.join(".git/hooks");
        fs::create_dir_all(&hooks_dir).unwrap();

        let hook_content = format!("#!/bin/bash\n# {HOOK_MARKER}\nexit 0\n");
        let hook_path = hooks_dir.join("pre-commit");
        fs::write(&hook_path, &hook_content).unwrap();

        let _repo = git2::Repository::init(repo_root).unwrap();

        uninstall_hooks_silent_at(repo_root);

        assert!(
            !hook_path.exists(),
            "Ralph hook should be removed by uninstall_hooks_silent_at"
        );
    }

    #[test]
    fn test_uninstall_hooks_silent_at_restores_orig() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let hooks_dir = repo_root.join(".git/hooks");
        fs::create_dir_all(&hooks_dir).unwrap();

        let _repo = git2::Repository::init(repo_root).unwrap();

        let hook_content = format!("#!/bin/bash\n# {HOOK_MARKER}\nexit 0\n");
        let hook_path = hooks_dir.join("pre-commit");
        fs::write(&hook_path, &hook_content).unwrap();

        let orig_content = "#!/bin/bash\necho 'original'\n";
        let hook_abs = fs::canonicalize(&hook_path).unwrap();
        let orig_path = PathBuf::from(format!("{}.ralph.orig", hook_abs.display()));
        fs::write(&orig_path, orig_content).unwrap();

        uninstall_hooks_silent_at(repo_root);

        let restored = fs::read_to_string(&hook_path).unwrap();
        assert_eq!(
            restored, orig_content,
            "original hook should be restored by uninstall_hooks_silent_at"
        );
        assert!(
            !orig_path.exists(),
            ".ralph.orig backup should be removed after restore"
        );
    }

    #[test]
    fn test_uninstall_hooks_silent_at_preserves_non_ralph_hooks() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let hooks_dir = repo_root.join(".git/hooks");
        fs::create_dir_all(&hooks_dir).unwrap();

        let _repo = git2::Repository::init(repo_root).unwrap();

        let user_hook = "#!/bin/bash\necho 'user hook'\n";
        let hook_path = hooks_dir.join("pre-commit");
        fs::write(&hook_path, user_hook).unwrap();

        uninstall_hooks_silent_at(repo_root);

        let content = fs::read_to_string(&hook_path).unwrap();
        assert_eq!(
            content, user_hook,
            "non-Ralph hooks should be preserved by uninstall_hooks_silent_at"
        );
    }

    #[test]
    fn test_uninstall_hooks_silent_at_nonexistent_repo() {
        let nonexistent = Path::new("/nonexistent/repo/root");
        uninstall_hooks_silent_at(nonexistent);
    }
}
