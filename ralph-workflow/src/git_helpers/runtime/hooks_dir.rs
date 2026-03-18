//! Hooks directory validation.
//!
//! Handles validation of hooks directories for both traditional
//! (.git/hooks) and Ralph-scoped (worktree .git/ralph/hooks) modes.

use crate::git_helpers::repo::ProtectionScope;
use std::fs;
use std::io;

pub(crate) fn ensure_scoped_hooks_dir_is_owned(scope: &ProtectionScope) -> io::Result<()> {
    validate_hooks_dir_for_scope(scope, true)
}

pub(crate) fn validate_hooks_dir_for_scope(
    scope: &ProtectionScope,
    create_if_missing: bool,
) -> io::Result<()> {
    if scope.uses_worktree_scoped_hooks {
        return validate_ralph_scoped_hooks_dir(scope, create_if_missing);
    }

    validate_traditional_hooks_dir(scope, create_if_missing)
}

fn validate_traditional_hooks_dir(
    scope: &ProtectionScope,
    create_if_missing: bool,
) -> io::Result<()> {
    let expected_hooks_dir = scope.git_dir.join("hooks");
    if scope.hooks_dir != expected_hooks_dir {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "refusing to use unexpected hooks dir for repository scope: {}",
                scope.hooks_dir.display()
            ),
        ));
    }

    match fs::symlink_metadata(&scope.hooks_dir) {
        Ok(meta) => {
            if meta.file_type().is_symlink() || !meta.is_dir() {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    format!(
                        "refusing to use non-directory hooks dir: {}",
                        scope.hooks_dir.display()
                    ),
                ));
            }
        }
        Err(err) if err.kind() == io::ErrorKind::NotFound => {
            if !create_if_missing {
                return Ok(());
            }
            fs::create_dir_all(&scope.hooks_dir)?;
        }
        Err(err) => return Err(err),
    }

    let resolved_hooks_dir = fs::canonicalize(&scope.hooks_dir)?;
    let resolved_git_dir = fs::canonicalize(&scope.git_dir)?;
    if resolved_hooks_dir.parent() != Some(resolved_git_dir.as_path()) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "refusing to use hook dir outside repository git dir: {}",
                scope.hooks_dir.display()
            ),
        ));
    }

    Ok(())
}

fn validate_ralph_scoped_hooks_dir(
    scope: &ProtectionScope,
    create_if_missing: bool,
) -> io::Result<()> {
    if scope.hooks_dir.parent() != Some(scope.ralph_dir.as_path()) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "refusing to install hooks outside Ralph's scoped metadata dir: {}",
                scope.hooks_dir.display()
            ),
        ));
    }

    match fs::symlink_metadata(&scope.hooks_dir) {
        Ok(meta) => {
            if meta.file_type().is_symlink() || !meta.is_dir() {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    format!(
                        "refusing to use non-directory scoped hooks dir: {}",
                        scope.hooks_dir.display()
                    ),
                ));
            }
        }
        Err(err) if err.kind() == io::ErrorKind::NotFound => {
            if !create_if_missing {
                return Ok(());
            }
        }
        Err(err) => return Err(err),
    }

    if create_if_missing {
        fs::create_dir_all(&scope.hooks_dir)?;
    }

    let resolved_hooks_dir = fs::canonicalize(&scope.hooks_dir)?;
    let resolved_ralph_dir = fs::canonicalize(&scope.ralph_dir)?;
    if resolved_hooks_dir.parent() != Some(resolved_ralph_dir.as_path()) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "refusing to use hook dir outside Ralph's scoped metadata dir: {}",
                scope.hooks_dir.display()
            ),
        ));
    }

    Ok(())
}
