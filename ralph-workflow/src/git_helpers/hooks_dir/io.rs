// git_helpers/hooks_dir/io.rs — boundary module for hooks directory validation.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Hooks directory validation.
//
// Handles validation of hooks directories for both traditional
// (.git/hooks) and Ralph-scoped (worktree .git/ralph/hooks) modes.

use crate::git_helpers::repo::ProtectionScope;
use std::fs;

pub(crate) fn ensure_scoped_hooks_dir_is_owned(scope: &ProtectionScope) -> std::io::Result<()> {
    validate_hooks_dir_for_scope(scope, true)
}

pub(crate) fn validate_hooks_dir_for_scope(
    scope: &ProtectionScope,
    create_if_missing: bool,
) -> std::io::Result<()> {
    if scope.uses_worktree_scoped_hooks {
        return validate_ralph_scoped_hooks_dir(scope, create_if_missing);
    }

    validate_traditional_hooks_dir(scope, create_if_missing)
}

fn check_traditional_hooks_dir_path(scope: &ProtectionScope) -> std::io::Result<()> {
    let expected_hooks_dir = scope.git_dir.join("hooks");
    if scope.hooks_dir == expected_hooks_dir {
        return Ok(());
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::PermissionDenied,
        format!(
            "refusing to use unexpected hooks dir for repository scope: {}",
            scope.hooks_dir.display()
        ),
    ))
}

fn traditional_hooks_dir_not_directory_error(scope: &ProtectionScope) -> std::io::Error {
    std::io::Error::new(
        std::io::ErrorKind::PermissionDenied,
        format!(
            "refusing to use non-directory hooks dir: {}",
            scope.hooks_dir.display()
        ),
    )
}

fn create_traditional_hooks_dir_if_needed(
    scope: &ProtectionScope,
    create_if_missing: bool,
) -> std::io::Result<()> {
    if create_if_missing {
        fs::create_dir_all(&scope.hooks_dir)?;
    }
    Ok(())
}

fn handle_traditional_hooks_dir_metadata(
    scope: &ProtectionScope,
    create_if_missing: bool,
) -> std::io::Result<()> {
    match fs::symlink_metadata(&scope.hooks_dir) {
        Ok(meta) if meta.file_type().is_symlink() || !meta.is_dir() => {
            Err(traditional_hooks_dir_not_directory_error(scope))
        }
        Ok(_) => Ok(()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            create_traditional_hooks_dir_if_needed(scope, create_if_missing)
        }
        Err(err) => Err(err),
    }
}

fn verify_traditional_hooks_dir_parent(scope: &ProtectionScope) -> std::io::Result<()> {
    let resolved_hooks_dir = fs::canonicalize(&scope.hooks_dir)?;
    let resolved_git_dir = fs::canonicalize(&scope.git_dir)?;
    if resolved_hooks_dir.parent() == Some(resolved_git_dir.as_path()) {
        return Ok(());
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::PermissionDenied,
        format!(
            "refusing to use hook dir outside repository git dir: {}",
            scope.hooks_dir.display()
        ),
    ))
}

fn validate_traditional_hooks_dir(
    scope: &ProtectionScope,
    create_if_missing: bool,
) -> std::io::Result<()> {
    check_traditional_hooks_dir_path(scope)?;
    handle_traditional_hooks_dir_metadata(scope, create_if_missing)?;
    if scope.hooks_dir.exists() {
        verify_traditional_hooks_dir_parent(scope)?;
    }
    Ok(())
}

fn check_scoped_hooks_dir_parent(scope: &ProtectionScope) -> std::io::Result<()> {
    if scope.hooks_dir.parent() == Some(scope.ralph_dir.as_path()) {
        return Ok(());
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::PermissionDenied,
        format!(
            "refusing to install hooks outside Ralph's scoped metadata dir: {}",
            scope.hooks_dir.display()
        ),
    ))
}

fn handle_scoped_hooks_dir_metadata(
    scope: &ProtectionScope,
    create_if_missing: bool,
) -> std::io::Result<bool> {
    match fs::symlink_metadata(&scope.hooks_dir) {
        Ok(meta) if meta.file_type().is_symlink() || !meta.is_dir() => Err(std::io::Error::new(
            std::io::ErrorKind::PermissionDenied,
            format!(
                "refusing to use non-directory scoped hooks dir: {}",
                scope.hooks_dir.display()
            ),
        )),
        Ok(_) => Ok(true),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(create_if_missing),
        Err(err) => Err(err),
    }
}

fn verify_scoped_hooks_dir_ownership(scope: &ProtectionScope) -> std::io::Result<()> {
    let resolved_hooks_dir = fs::canonicalize(&scope.hooks_dir)?;
    let resolved_ralph_dir = fs::canonicalize(&scope.ralph_dir)?;
    if resolved_hooks_dir.parent() == Some(resolved_ralph_dir.as_path()) {
        return Ok(());
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::PermissionDenied,
        format!(
            "refusing to use hook dir outside Ralph's scoped metadata dir: {}",
            scope.hooks_dir.display()
        ),
    ))
}

fn validate_ralph_scoped_hooks_dir(
    scope: &ProtectionScope,
    create_if_missing: bool,
) -> std::io::Result<()> {
    check_scoped_hooks_dir_parent(scope)?;
    let should_create = handle_scoped_hooks_dir_metadata(scope, create_if_missing)?;
    if should_create {
        fs::create_dir_all(&scope.hooks_dir)?;
        verify_scoped_hooks_dir_ownership(scope)?;
    }
    Ok(())
}
