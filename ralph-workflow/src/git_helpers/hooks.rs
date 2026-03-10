//! Git hook installation and management.
//!
//! This module handles the lifecycle of Ralph-managed git hooks, including:
//!
//! - **Installation**: Creating `pre-commit` and `pre-push` hooks that block
//!   git operations during the agent phase (when `.no_agent_commit` exists)
//! - **Backup**: Preserving existing hooks as `.ralph.orig` files before overwriting
//! - **Restoration**: Restoring original hooks when uninstalling Ralph hooks
//!
//! Hooks are identified by a marker string (`RALPH_RUST_MANAGED_HOOK`) embedded
//! in the hook script, allowing safe detection and removal of Ralph-managed hooks
//! without affecting user-created hooks.
//!
//! Note: This module uses libgit2 (via the repo module) for locating the hooks
//! directory, avoiding CLI dependencies.
//!
//! # Architecture Note
//!
//! Hook installation uses `std::fs` directly rather than the `Workspace` trait.
//! This is acceptable per AGENTS.md because:
//!
//! 1. `.git/hooks/` is managed by git, not the workspace abstraction
//! 2. Hook installation is a bootstrap operation that occurs before pipeline execution
//! 3. Tests that need hook behavior use workspace-aware test utilities
//!    (`file_contains_marker_with_workspace`, `verify_hook_integrity_with_workspace`)
//!
//! The workspace abstraction is designed for files within the repository working
//! tree, not for git internals.

use super::repo::get_hooks_dir;
use crate::files::file_contains_marker;
use crate::logger::Logger;
#[cfg(any(test, feature = "test-utils"))]
use crate::workspace::Workspace;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

/// Uninstall all Ralph-managed hooks in an explicit repository.
///
/// This is used for startup cleanup where the process current working directory
/// may differ from the repo root we're operating on.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn uninstall_hooks_in_repo(repo_root: &Path, logger: &Logger) -> io::Result<()> {
    let hooks_dir = super::repo::get_hooks_dir_from(repo_root)?;
    if !hooks_dir.exists() {
        return Ok(());
    }

    let mut restored = 0;
    for hook_name in RALPH_HOOK_NAMES {
        let hook_path = hooks_dir.join(hook_name);
        if hook_path.exists() && uninstall_hook(&hook_path, logger)? {
            restored += 1;
        }
    }

    if restored > 0 {
        logger.success(&format!("Uninstalled {restored} Ralph hook(s)"));
    } else {
        logger.info("No Ralph hooks were restored (hooks may not have been installed)");
    }

    Ok(())
}

fn bash_single_quote_literal(s: &str) -> String {
    // Bash-safe single-quoted string literal.
    // In bash, single quotes cannot be escaped within single quotes, so the standard
    // pattern is: 'foo'\''bar' to represent foo'bar.
    let mut out = String::with_capacity(s.len() + 2);
    out.push('\'');
    for ch in s.chars() {
        if ch == '\'' {
            out.push_str("'\\''");
        } else {
            out.push(ch);
        }
    }
    out.push('\'');
    out
}

/// Marker string for Ralph-managed hooks.
pub const HOOK_MARKER: &str = "RALPH_RUST_MANAGED_HOOK";

/// All hook names managed by Ralph.
///
/// This constant is the single source of truth for which hooks Ralph installs,
/// uninstalls, monitors, and enforces permissions on. Adding a new hook here
/// automatically propagates to all lifecycle operations.
pub const RALPH_HOOK_NAMES: &[&str] = &["pre-commit", "pre-push", "pre-merge-commit"];

/// Make a file writable before removal (hooks are installed as 0o555).
#[cfg(unix)]
fn make_writable_for_removal(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(meta) = fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_mode(perms.mode() | 0o200); // add owner write
        let _ = fs::set_permissions(path, perms);
    }
}

/// Install a git hook.
///
/// The hook script embeds the repository root path directly, avoiding any
/// dependency on the git CLI being installed. The repo root is resolved at
/// installation time using libgit2.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn install_hook(hook_name: &str, hook_path: &Path) -> io::Result<()> {
    use super::repo::get_repo_root;

    // Get the repo root using libgit2 and embed it in the hook script.
    // This avoids any dependency on git CLI being installed.
    let repo_root = get_repo_root()?;
    let repo_root_str = repo_root.display().to_string();

    // Render paths as bash-safe literals (single-quoted) to avoid path parsing issues.
    // This tolerates spaces and most odd characters, including embedded double-quotes.
    let repo_root_bash = bash_single_quote_literal(&repo_root_str);

    // Create hooks directory if needed.
    //
    // IMPORTANT: we must do this BEFORE canonicalize(), otherwise canonicalize() fails
    // when the hooks directory is missing.
    if let Some(parent) = hook_path.parent() {
        fs::create_dir_all(parent)?;
    }

    // Use absolute path for orig backup.
    // Handle the case where hook_path has no parent or file_name gracefully.
    let hook_dir = hook_path.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "Hook path has no parent directory",
        )
    })?;
    let hook_file_name = hook_path
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Hook path has no file name"))?;
    let hook_path_abs = fs::canonicalize(hook_dir)?.join(hook_file_name);
    let orig_path_abs = PathBuf::from(format!("{}.ralph.orig", hook_path_abs.display()));

    // Store the orig path as a bash-safe single-quoted literal.
    let orig_path_bash = bash_single_quote_literal(&orig_path_abs.display().to_string());

    // Backup existing hook if not already managed by Ralph.
    if hook_path.exists() && !file_contains_marker(hook_path, HOOK_MARKER)? {
        fs::copy(hook_path, &orig_path_abs)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&orig_path_abs)?.permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&orig_path_abs, perms)?;
        }
    }

    // Make writable before overwriting (hooks may be installed as read-only 0o555).
    #[cfg(unix)]
    if hook_path.exists() {
        make_writable_for_removal(hook_path);
    }

    // Write new hook with repo root embedded directly (no git CLI dependency).
    let hook_content = format!(
        r#"#!/usr/bin/env bash
set -euo pipefail
# {HOOK_MARKER} - generated by ralph
# Repository root (embedded at install time using libgit2): {repo_root_str}

repo_root={repo_root_bash}

if [[ -f "$repo_root/.no_agent_commit" ]]; then
  echo "✋ {hook_name} blocked (agent phase): .no_agent_commit exists."
  exit 1
fi

orig={orig_path_bash}
if [[ -f "$orig" ]]; then
  exec "$orig" "$@"
fi

exit 0
"#
    );

    let mut file = File::create(hook_path)?;
    file.write_all(hook_content.as_bytes())?;

    // Make read-only executable (0o555) to deter agent overwriting.
    // Agents would need to explicitly chmod before overwriting, which is an
    // additional barrier reinforced by prompt instructions.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(hook_path)?.permissions();
        perms.set_mode(0o555);
        fs::set_permissions(hook_path, perms)?;
    }

    Ok(())
}

/// Install pre-commit and pre-push hooks.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn install_hooks() -> io::Result<()> {
    let hooks_dir = get_hooks_dir()?;
    fs::create_dir_all(&hooks_dir)?;

    for hook_name in RALPH_HOOK_NAMES {
        // Use a human-readable label for the blocking message.
        let label = match *hook_name {
            "pre-commit" => "Commit",
            "pre-push" => "Push",
            "pre-merge-commit" => "Merge commit",
            _ => hook_name,
        };
        install_hook(label, &hooks_dir.join(hook_name))?;
    }

    Ok(())
}

/// Uninstall a single hook by restoring original or removing.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn uninstall_hook(hook_path: &Path, logger: &Logger) -> io::Result<bool> {
    let hook_path_abs = if hook_path.is_absolute() {
        hook_path.to_path_buf()
    } else {
        // Handle the case where hook_path has no parent or file_name gracefully.
        let hook_dir = hook_path.parent().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "Hook path has no parent directory",
            )
        })?;
        let hook_file_name = hook_path.file_name().ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidInput, "Hook path has no file name")
        })?;
        fs::canonicalize(hook_dir)?.join(hook_file_name)
    };
    let orig_path = PathBuf::from(format!("{}.ralph.orig", hook_path_abs.display()));

    // Check if this is a Ralph-managed hook.
    if hook_path.exists() && file_contains_marker(hook_path, HOOK_MARKER)? {
        // Make writable before removal (hooks are installed as read-only 0o555).
        #[cfg(unix)]
        make_writable_for_removal(hook_path);

        if orig_path.exists() {
            // Restore original hook.
            fs::rename(&orig_path, hook_path)?;
            let name = hook_path.file_name().unwrap_or_default().to_string_lossy();
            logger.info(&format!("Restored original hook: {name}"));
        } else {
            // No original to restore, just remove.
            fs::remove_file(hook_path)?;
            let name = hook_path.file_name().unwrap_or_default().to_string_lossy();
            logger.info(&format!("Removed hook: {name}"));
        }
        Ok(true)
    } else {
        Ok(false)
    }
}

/// Uninstall all Ralph-managed hooks.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn uninstall_hooks(logger: &Logger) -> io::Result<()> {
    let hooks_dir = get_hooks_dir()?;
    if !hooks_dir.exists() {
        return Ok(());
    }

    let mut restored = 0;
    for hook_name in RALPH_HOOK_NAMES {
        let hook_path = hooks_dir.join(hook_name);
        if hook_path.exists() && uninstall_hook(&hook_path, logger)? {
            restored += 1;
        }
    }

    if restored > 0 {
        logger.success(&format!("Uninstalled {restored} Ralph hook(s)"));
    } else {
        // Best-effort flows (hooks never installed, install failed, test mode) can
        // legitimately result in no hooks being restored. Avoid logging a message that
        // implies an exceptional termination path.
        logger.info("No Ralph hooks were restored (hooks may not have been installed)");
    }

    Ok(())
}

/// Silently uninstall all Ralph-managed hooks (for signal handlers).
///
/// Does not log output; safe to call during cleanup.
pub fn uninstall_hooks_silent() {
    let Ok(hooks_dir) = get_hooks_dir() else {
        return;
    };
    if !hooks_dir.exists() {
        return;
    }

    for hook_name in RALPH_HOOK_NAMES {
        let hook_path = hooks_dir.join(hook_name);
        if hook_path.exists() && matches!(file_contains_marker(&hook_path, HOOK_MARKER), Ok(true)) {
            // Make writable before removal (hooks are installed as read-only 0o555).
            #[cfg(unix)]
            make_writable_for_removal(&hook_path);

            let hook_path_abs = fs::canonicalize(&hook_path).unwrap_or_else(|_| hook_path.clone());
            let orig_path = PathBuf::from(format!("{}.ralph.orig", hook_path_abs.display()));

            if orig_path.exists() {
                let _ = fs::rename(&orig_path, &hook_path);
            } else {
                let _ = fs::remove_file(&hook_path);
            }
        }
    }
}

/// Reinstall hooks if they have been tampered with or removed.
///
/// Checks each Ralph-managed hook (`pre-commit`, `pre-push`) for the presence
/// of [`HOOK_MARKER`]. If any hook is missing or lacks the marker, all hooks
/// are reinstalled.
///
/// This function is designed to be called before each agent invocation to
/// self-heal against a prior agent that deleted or modified the hooks.
///
/// # Errors
///
/// Returns error if hook reinstallation fails.
/// Returns `Ok(true)` if hooks were reinstalled (tampering detected),
/// `Ok(false)` if hooks were intact.
pub fn reinstall_hooks_if_tampered(logger: &Logger) -> io::Result<bool> {
    let Ok(hooks_dir) = get_hooks_dir() else {
        return Ok(false); // No git repo — nothing to protect
    };

    let needs_reinstall = RALPH_HOOK_NAMES.iter().any(|name| {
        let path = hooks_dir.join(name);
        if !path.exists() {
            return true;
        }
        !matches!(file_contains_marker(&path, HOOK_MARKER), Ok(true))
    });

    if needs_reinstall {
        logger.warn("Git hooks tampered with or missing — reinstalling");
        install_hooks()?;
        Ok(true)
    } else {
        Ok(false)
    }
}

/// Verify and restore read-only executable permissions on Ralph-managed hooks.
///
/// Checks each Ralph-managed hook (`pre-commit`, `pre-push`) for the expected
/// permission mode (0o555). If any hook has loosened permissions (e.g., an agent
/// ran `chmod 755`), this function restores the restrictive permissions.
///
/// This is called from `ensure_agent_phase_protections()` after
/// `reinstall_hooks_if_tampered()`.
#[cfg(unix)]
pub fn enforce_hook_permissions(logger: &Logger) {
    use std::os::unix::fs::PermissionsExt;

    let Ok(hooks_dir) = get_hooks_dir() else {
        return;
    };

    for hook_name in RALPH_HOOK_NAMES {
        let path = hooks_dir.join(hook_name);
        if !path.exists() {
            continue;
        }
        if !matches!(file_contains_marker(&path, HOOK_MARKER), Ok(true)) {
            continue;
        }
        if let Ok(meta) = fs::metadata(&path) {
            let mode = meta.permissions().mode() & 0o777;
            if mode != 0o555 {
                logger.warn(&format!(
                    "{hook_name} permissions loosened ({mode:#o}) — restoring to 0o555"
                ));
                let mut perms = meta.permissions();
                perms.set_mode(0o555);
                let _ = fs::set_permissions(&path, perms);
            }
        }
    }
}

/// Check if a file contains a specific marker string using the Workspace trait.
///
/// This is a workspace-aware version of `file_contains_marker` that uses the
/// Workspace abstraction for file I/O, making it testable with `MemoryWorkspace`.
///
/// # Arguments
///
/// * `workspace` - The workspace to read from
/// * `relative_path` - Path to the file relative to the workspace root
/// * `marker` - String to search for
///
/// # Returns
///
/// `Ok(true)` if the marker is found, `Ok(false)` if not found or file doesn't exist.
///
/// # Errors
///
/// Returns an error if the file cannot be read.
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
    for line in content.lines() {
        if line.contains(marker) {
            return Ok(true);
        }
    }

    Ok(false)
}

/// Check if a hook file has been tampered with or bypassed using the Workspace trait.
///
/// This is a workspace-aware version of `verify_hook_integrity` that uses the
/// Workspace abstraction for file I/O, making it testable with `MemoryWorkspace`.
///
/// Returns `Ok(true)` if the hook appears intact and is managed by Ralph,
/// `Ok(false)` if the hook is missing or has been tampered with, or `Err` on
/// filesystem errors.
///
/// # Arguments
///
/// * `workspace` - The workspace to read from
/// * `relative_path` - Path to the hook file relative to the workspace root
///
/// # Errors
///
/// Returns an error if the file cannot be read.
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

    // =========================================================================
    // Tests using MemoryWorkspace (workspace-aware)
    // =========================================================================

    #[test]
    fn test_ralph_hook_names_contains_all_hooks() {
        assert_eq!(RALPH_HOOK_NAMES.len(), 3);
        assert!(RALPH_HOOK_NAMES.contains(&"pre-commit"));
        assert!(RALPH_HOOK_NAMES.contains(&"pre-push"));
        assert!(RALPH_HOOK_NAMES.contains(&"pre-merge-commit"));
    }

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
