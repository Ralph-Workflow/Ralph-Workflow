// git_helpers/verify/io.rs — boundary module for hook verification and monitoring logic.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Hook verification and monitoring logic.

use crate::files::file_contains_marker;
use crate::git_helpers::config_state;
use crate::git_helpers::install::{HOOK_MARKER, RALPH_HOOK_NAMES};
use crate::git_helpers::repo::{get_hooks_dir_from, resolve_protection_scope_from};
use crate::logger::Logger;
#[cfg(any(test, feature = "test-utils"))]
use crate::workspace::Workspace;
use std::fs;
use std::path::Path;

mod io {
    pub(crate) type Result<T> = std::io::Result<T>;
}

pub fn verify_hooks_removed(repo_root: &Path) -> io::Result<Vec<&'static str>> {
    let hooks_dir = get_hooks_dir_from(repo_root)?;
    if !hooks_dir.exists() {
        return Ok(Vec::new());
    }

    let remaining = RALPH_HOOK_NAMES
        .iter()
        .filter(|name| {
            let path = hooks_dir.join(name);
            path.exists() && matches!(file_contains_marker(&path, HOOK_MARKER), Ok(true))
        })
        .copied()
        .collect();

    Ok(remaining)
}

fn any_hook_missing_or_tampered(hooks_dir: &std::path::Path) -> bool {
    RALPH_HOOK_NAMES.iter().any(|name| {
        let path = hooks_dir.join(name);
        if !path.exists() {
            return true;
        }
        !matches!(file_contains_marker(&path, HOOK_MARKER), Ok(true))
    })
}

pub fn reinstall_hooks_if_tampered(logger: &Logger) -> io::Result<bool> {
    let Ok(scope) = resolve_protection_scope_from(Path::new(".")) else {
        return Ok(false);
    };

    let hooks_missing_or_tampered = any_hook_missing_or_tampered(&scope.hooks_dir);
    let hooks_path_tampered =
        scope.uses_worktree_scoped_hooks && !config_state::hooks_path_matches_scope(&scope)?;

    if hooks_missing_or_tampered || hooks_path_tampered {
        logger.warn("Git hooks tampered with or missing — reinstalling");
        crate::git_helpers::install::install_hooks_in_repo(&scope.repo_root)?;
        Ok(true)
    } else {
        Ok(false)
    }
}

#[cfg(unix)]
pub fn enforce_hook_permissions(repo_root: &Path, logger: &Logger) {
    let Ok(hooks_dir) = get_hooks_dir_from(repo_root) else {
        return;
    };

    RALPH_HOOK_NAMES.iter().for_each(|hook_name| {
        let path = hooks_dir.join(hook_name);
        if !path.exists() {
            return;
        }
        if !matches!(file_contains_marker(&path, HOOK_MARKER), Ok(true)) {
            return;
        }
        if is_symlink_hook(&path, logger, hook_name) {
            return;
        }
        restore_hook_permissions_if_loose(&path, logger, hook_name);
    });
}

#[cfg(unix)]
fn is_symlink_hook(path: &Path, logger: &Logger, hook_name: &str) -> bool {
    if matches!(fs::symlink_metadata(path), Ok(m) if m.file_type().is_symlink()) {
        logger.warn(&format!(
            "{hook_name} is a symlink — refusing to chmod hook permissions"
        ));
        true
    } else {
        false
    }
}

#[cfg(unix)]
fn restore_hook_permissions_if_loose(path: &Path, logger: &Logger, hook_name: &str) {
    use std::os::unix::fs::PermissionsExt;

    if let Ok(meta) = fs::metadata(path) {
        let mode = meta.permissions().mode() & 0o777;
        if mode != 0o555 {
            logger.warn(&format!(
                "{hook_name} permissions loosened ({mode:#o}) — restoring to 0o555"
            ));
            let mut perms = meta.permissions();
            perms.set_mode(0o555);
            let _ = fs::set_permissions(path, perms);
        }
    }
}

#[cfg(not(unix))]
pub fn enforce_hook_permissions(_repo_root: &Path, _logger: &Logger) {}

#[cfg(any(test, feature = "test-utils"))]
pub fn file_contains_marker_with_workspace(
    workspace: &dyn Workspace,
    relative_path: &Path,
    marker: &str,
) -> io::Result<bool> {
    if !workspace.exists(relative_path) {
        return Ok(false);
    }

    let content = workspace.read(relative_path)?;
    let found = content.lines().any(|line| line.contains(marker));
    Ok(found)
}

#[cfg(any(test, feature = "test-utils"))]
pub fn verify_hook_integrity_with_workspace(
    workspace: &dyn Workspace,
    relative_path: &Path,
) -> io::Result<bool> {
    if !workspace.exists(relative_path) {
        return Ok(false);
    }
    file_contains_marker_with_workspace(workspace, relative_path, HOOK_MARKER)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_file_contains_marker_with_workspace_found() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "hooks/pre-commit",
            &format!("#!/bin/bash\n# {HOOK_MARKER}\nexit 0"),
        );

        let result = file_contains_marker_with_workspace(
            &workspace,
            Path::new("hooks/pre-commit"),
            HOOK_MARKER,
        );
        assert!(result.unwrap());
    }

    #[test]
    fn test_file_contains_marker_with_workspace_not_found() {
        let workspace =
            MemoryWorkspace::new_test().with_file("hooks/pre-commit", "#!/bin/bash\nexit 0");

        let result = file_contains_marker_with_workspace(
            &workspace,
            Path::new("hooks/pre-commit"),
            HOOK_MARKER,
        );
        assert!(!result.unwrap());
    }

    #[test]
    fn test_file_contains_marker_with_workspace_missing_file() {
        let workspace = MemoryWorkspace::new_test();

        let result = file_contains_marker_with_workspace(
            &workspace,
            Path::new("hooks/pre-commit"),
            HOOK_MARKER,
        );
        assert!(!result.unwrap());
    }

    #[test]
    fn test_verify_hook_integrity_with_workspace_missing() {
        let workspace = MemoryWorkspace::new_test();

        let result =
            verify_hook_integrity_with_workspace(&workspace, Path::new("hooks/pre-commit"));
        assert!(!result.unwrap());
    }

    #[test]
    fn test_verify_hook_integrity_with_workspace_valid_ralph_hook() {
        let hook_content =
            format!("#!/usr/bin/env bash\n# {HOOK_MARKER} - generated by ralph\nexit 0\n");
        let workspace = MemoryWorkspace::new_test().with_file("hooks/pre-commit", &hook_content);

        let result =
            verify_hook_integrity_with_workspace(&workspace, Path::new("hooks/pre-commit"));
        assert!(result.unwrap());
    }

    #[test]
    fn test_verify_hook_integrity_with_workspace_tampered_hook() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "hooks/pre-commit",
            "#!/usr/bin/env bash\necho \"Custom hook\"\nexit 0\n",
        );

        let result =
            verify_hook_integrity_with_workspace(&workspace, Path::new("hooks/pre-commit"));
        assert!(!result.unwrap());
    }

    #[test]
    fn test_verify_hook_integrity_with_workspace_modified_marker() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "hooks/pre-commit",
            "#!/usr/bin/env bash\n# NOT_RALPH_MARKER\nexit 0\n",
        );

        let result =
            verify_hook_integrity_with_workspace(&workspace, Path::new("hooks/pre-commit"));
        assert!(!result.unwrap());
    }
}
