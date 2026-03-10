//! Git wrapper for blocking commits during agent phase.
//!
//! This module provides safety mechanisms to prevent accidental commits while
//! an AI agent is actively modifying files. It works through two mechanisms:
//!
//! - **Marker file**: Creates `.no_agent_commit` in the repo root during agent
//!   execution. Both the git wrapper and hooks check for this file.
//! - **PATH wrapper**: Installs a temporary `git` wrapper script that intercepts
//!   `commit`, `push`, and `tag` commands when the marker file exists.
//!
//! The wrapper is automatically cleaned up when the agent phase ends, even on
//! unexpected exits (Ctrl+C, panics) via [`cleanup_agent_phase_silent`].

use super::hooks::{install_hooks, reinstall_hooks_if_tampered, uninstall_hooks_silent};
use super::repo::get_repo_root;
use crate::logger::Logger;
use crate::workspace::Workspace;
use std::env;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use tempfile::TempDir;
use which::which;

const WRAPPER_DIR_TRACK_FILE: &str = ".agent/git-wrapper-dir.txt";

/// Marker file path for blocking commits during agent phase.
const MARKER_FILE: &str = ".no_agent_commit";

/// Git helper state.
pub struct GitHelpers {
    real_git: Option<PathBuf>,
    wrapper_dir: Option<TempDir>,
    wrapper_repo_root: Option<PathBuf>,
}

impl GitHelpers {
    pub(crate) const fn new() -> Self {
        Self {
            real_git: None,
            wrapper_dir: None,
            wrapper_repo_root: None,
        }
    }

    /// Find the real git binary path.
    fn init_real_git(&mut self) {
        if self.real_git.is_none() {
            self.real_git = which("git").ok();
        }
    }
}

impl Default for GitHelpers {
    fn default() -> Self {
        Self::new()
    }
}

/// Escape a path for safe use in a POSIX shell single-quoted string.
///
/// Single quotes in POSIX shells cannot contain literal single quotes.
/// The standard workaround is to end the quote, add an escaped quote, and restart the quote.
/// This function rejects paths with newlines since they can't be safely handled.
fn escape_shell_single_quoted(path: &str) -> io::Result<String> {
    // Reject newlines - they cannot be safely handled in shell scripts
    if path.contains('\n') || path.contains('\r') {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "git path contains newline characters, cannot create safe shell wrapper",
        ));
    }
    // Replace ' with '\' (end literal string, escaped quote, restart literal string)
    Ok(path.replace('\'', "'\\''"))
}

/// Generate the git wrapper script content.
///
/// The script intercepts destructive git subcommands when `.no_agent_commit`
/// is present. It iterates through arguments to skip git global flags
/// (e.g. `-C /path`, `--git-dir=.git`) before identifying the subcommand,
/// so patterns like `git -C /path commit` are blocked correctly.
///
/// Blocked commands fall into three categories:
/// - **Unconditionally blocked**: commit, push, tag, merge, rebase, reset,
///   cherry-pick, revert, am, apply, clean, restore
/// - **Conditionally blocked**: stash (pop/drop/apply/push/store/create/clear
///   blocked, list allowed), branch (-d/-D/--delete blocked, list allowed),
///   checkout (-- blocked to prevent discarding changes)
///
/// `git_path_escaped` must already be shell-single-quote-escaped.
fn make_wrapper_content(git_path_escaped: &str) -> String {
    format!(
        r#"#!/usr/bin/env sh
set -eu
repo_root="$('{git_path_escaped}' rev-parse --show-toplevel 2>/dev/null || pwd)"
if [ -f "$repo_root/.no_agent_commit" ]; then
  subcmd=""
  skip_next=0
  for arg in "$@"; do
    if [ "$skip_next" = "1" ]; then
      skip_next=0
      continue
    fi
    case "$arg" in
      -C|--git-dir|--work-tree|--namespace|-c|--exec-path)
        skip_next=1
        ;;
      --git-dir=*|--work-tree=*|--namespace=*|--exec-path=*|-c=*)
        ;;
      -*)
        ;;
      *)
        subcmd="$arg"
        break
        ;;
    esac
  done
  case "$subcmd" in
    commit|push|tag|merge|rebase|reset|cherry-pick|revert|am|apply|clean|restore)
      echo "Blocked: git $subcmd disabled during agent phase (.no_agent_commit present)." >&2
      exit 1
      ;;
    stash)
      # Allow 'stash list' and bare 'stash', block destructive stash subcommands
      stash_sub=""
      found_stash=0
      for a2 in "$@"; do
        if [ "$found_stash" = "1" ]; then
          case "$a2" in
            -*) ;;
            *) stash_sub="$a2"; break ;;
          esac
        fi
        if [ "$a2" = "stash" ]; then found_stash=1; fi
      done
      case "$stash_sub" in
        pop|drop|apply|push|store|create|clear)
          echo "Blocked: git stash $stash_sub disabled during agent phase (.no_agent_commit present)." >&2
          exit 1
          ;;
      esac
      ;;
    branch)
      # Allow 'branch' (list), block 'branch -d/-D/--delete'
      for a2 in "$@"; do
        case "$a2" in
          -d|-D|--delete)
            echo "Blocked: git branch delete disabled during agent phase (.no_agent_commit present)." >&2
            exit 1
            ;;
        esac
      done
      ;;
    checkout)
      # Block 'checkout -- <path>' which discards uncommitted changes
      for a2 in "$@"; do
        if [ "$a2" = "--" ]; then
          echo "Blocked: git checkout -- disabled during agent phase (.no_agent_commit present)." >&2
          exit 1
        fi
      done
      ;;
  esac
fi
exec '{git_path_escaped}' "$@"
"#
    )
}

/// Enable git wrapper that blocks commits during agent phase.
pub fn enable_git_wrapper(helpers: &mut GitHelpers) -> io::Result<()> {
    helpers.init_real_git();
    let Some(real_git) = helpers.real_git.as_ref() else {
        // Ralph's git operations use libgit2 and should work without the `git` CLI installed.
        // The wrapper is only a safety feature for intercepting `git commit/push/tag`.
        // If no `git` binary is available, there's nothing to wrap, so we no-op.
        return Ok(());
    };

    // Validate git path is valid UTF-8 for shell script generation.
    // On Unix systems, paths are typically valid UTF-8, but some filesystems
    // may contain invalid UTF-8 sequences. In such cases, we cannot safely
    // generate a shell wrapper and should return an error.
    let git_path_str = real_git.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "git binary path contains invalid UTF-8 characters; cannot create wrapper script",
        )
    })?;

    // Validate that the git path is an absolute path.
    // This prevents potential issues with relative paths and ensures
    // we're using a known, trusted git binary location.
    if !real_git.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "git binary path is not absolute: '{git_path_str}'. \
                 Using absolute paths prevents potential security issues."
            ),
        ));
    }

    // Additional validation: ensure the git binary exists and is executable.
    // This prevents following symlinks to non-executable files or directories.
    if !real_git.exists() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!("git binary does not exist at path: '{git_path_str}'"),
        ));
    }

    // On Unix systems, verify it's not a directory (a directory is not executable as a binary).
    // Note: fs::metadata() follows symlinks, so this correctly validates the resolved target.
    // Many package managers (Homebrew, apt) install git as a symlink; that is fine.
    #[cfg(unix)]
    {
        match fs::metadata(real_git) {
            Ok(metadata) => {
                if metadata.file_type().is_dir() {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        format!("git binary path is a directory, not a file: '{git_path_str}'"),
                    ));
                }
            }
            Err(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    format!("cannot access git binary metadata at path: '{git_path_str}'"),
                ));
            }
        }
    }

    let wrapper_dir = tempfile::tempdir()?;
    let wrapper_path = wrapper_dir.path().join("git");

    // Escape the git path for shell script to prevent command injection.
    // Use a helper function to properly handle edge cases and reject unsafe paths.
    let git_path_escaped = escape_shell_single_quoted(git_path_str)?;

    let wrapper_content = make_wrapper_content(&git_path_escaped);

    let mut file = File::create(&wrapper_path)?;
    file.write_all(wrapper_content.as_bytes())?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&wrapper_path)?.permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&wrapper_path, perms)?;
    }

    // Prepend wrapper dir to PATH.
    let current_path = env::var("PATH").unwrap_or_default();
    env::set_var(
        "PATH",
        format!("{}:{}", wrapper_dir.path().display(), current_path),
    );

    let repo_root = get_repo_root()?;
    helpers.wrapper_repo_root = Some(repo_root.clone());

    fs::create_dir_all(repo_root.join(".agent"))?;
    fs::write(
        repo_root.join(WRAPPER_DIR_TRACK_FILE),
        wrapper_dir.path().display().to_string(),
    )?;

    helpers.wrapper_dir = Some(wrapper_dir);
    Ok(())
}

/// Disable git wrapper.
///
/// # Thread Safety
///
/// This function modifies the process-wide `PATH` environment variable, which is
/// inherently not thread-safe. If multiple threads were concurrently modifying PATH,
/// there could be a TOCTOU (time-of-check-time-of-use) race condition. However,
/// in Ralph's usage, this function is only called from the main thread during
/// controlled shutdown sequences, so this is acceptable in practice.
pub fn disable_git_wrapper(helpers: &mut GitHelpers) {
    if let Some(wrapper_dir) = helpers.wrapper_dir.take() {
        let wrapper_dir_path = wrapper_dir.path().to_path_buf();
        let _ = fs::remove_dir_all(&wrapper_dir_path);
        // Remove from PATH.
        // Note: This read-modify-write sequence on PATH has a theoretical TOCTOU race,
        // but in practice it's safe because Ralph only calls this from the main thread
        // during controlled shutdown.
        if let Ok(path) = env::var("PATH") {
            let wrapper_str = wrapper_dir_path.to_string_lossy();
            let new_path: String = path
                .split(':')
                .filter(|p| !p.contains(wrapper_str.as_ref()))
                .collect::<Vec<_>>()
                .join(":");
            env::set_var("PATH", new_path);
        }
    }

    // IMPORTANT: remove the tracking file using an absolute repo root path.
    // The process CWD may not be the repo root (e.g., tests or effects that change CWD).
    if let Some(repo_root) = helpers.wrapper_repo_root.take() {
        let _ = fs::remove_file(repo_root.join(WRAPPER_DIR_TRACK_FILE));
    } else {
        let _ = fs::remove_file(WRAPPER_DIR_TRACK_FILE);
    }
}

/// Start agent phase (creates marker file, installs hooks, enables wrapper).
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn start_agent_phase(helpers: &mut GitHelpers) -> io::Result<()> {
    File::create(".no_agent_commit")?;
    // Make marker read-only (0o444) to deter agent deletion.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(".no_agent_commit")?.permissions();
        perms.set_mode(0o444);
        fs::set_permissions(".no_agent_commit", perms)?;
    }
    install_hooks()?;
    enable_git_wrapper(helpers)?;
    Ok(())
}

/// End agent phase (removes marker file).
pub fn end_agent_phase() {
    let Ok(repo_root) = crate::git_helpers::get_repo_root() else {
        return;
    };
    let marker_path = repo_root.join(".no_agent_commit");
    // Make writable before removal (marker is created as read-only 0o444).
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = fs::metadata(&marker_path) {
            let mut perms = meta.permissions();
            perms.set_mode(perms.mode() | 0o200);
            let _ = fs::set_permissions(&marker_path, perms);
        }
    }
    let _ = fs::remove_file(marker_path);
}

/// Verify and restore agent-phase commit protections before each agent invocation.
///
/// This is the composite integrity check that self-heals against a prior agent
/// that deleted the `.no_agent_commit` marker or tampered with git hooks during
/// its run. It is designed to be called from `run_with_prompt` before every
/// agent spawn.
///
/// The function is a no-op when neither the marker file nor hooks exist, which
/// indicates the agent phase has ended normally (e.g., during the commit phase).
///
/// # Limitations
///
/// This check protects the *next* agent invocation. If an agent deletes both
/// the marker and hooks within a single invocation, the PATH wrapper is the
/// only remaining defense until this check runs again.
///
/// Errors are logged as warnings only — a missing git repo (e.g., in tests
/// without a real repo) should not crash the pipeline.
pub fn ensure_agent_phase_protections(logger: &Logger) {
    let Ok(repo_root) = get_repo_root() else {
        return;
    };

    let marker_path = repo_root.join(MARKER_FILE);
    let marker_exists = marker_path.exists();

    // Check if hooks exist (any Ralph hook present means we're in agent phase).
    let hooks_present = super::repo::get_hooks_dir_from(&repo_root)
        .ok()
        .is_some_and(|hooks_dir| {
            ["pre-commit", "pre-push"].iter().any(|name| {
                let path = hooks_dir.join(name);
                path.exists()
                    && matches!(
                        crate::files::file_contains_marker(&path, super::hooks::HOOK_MARKER),
                        Ok(true)
                    )
            })
        });

    // If neither marker nor hooks exist, we're not in the agent phase — no-op.
    if !marker_exists && !hooks_present {
        return;
    }

    // Recreate marker if missing (but hooks exist → agent phase is active).
    if !marker_exists {
        logger.warn(".no_agent_commit marker missing — recreating");
        if let Err(e) = File::create(&marker_path) {
            logger.warn(&format!("Failed to recreate .no_agent_commit: {e}"));
        }
    }

    // Verify/restore marker permissions (read-only 0o444).
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = fs::metadata(&marker_path) {
            let mode = meta.permissions().mode() & 0o777;
            if mode != 0o444 {
                logger.warn(&format!(
                    ".no_agent_commit permissions loosened ({mode:#o}) — restoring to 0o444"
                ));
                let mut perms = meta.permissions();
                perms.set_mode(0o444);
                let _ = fs::set_permissions(&marker_path, perms);
            }
        }
    }

    // Reinstall hooks if tampered (best-effort).
    if let Err(e) = reinstall_hooks_if_tampered(logger) {
        logger.warn(&format!("Failed to verify/reinstall hooks: {e}"));
    }

    // Verify/restore hook permissions (read-only executable 0o555).
    #[cfg(unix)]
    super::hooks::enforce_hook_permissions(logger);
}

fn cleanup_git_wrapper_dir_silent() {
    let Ok(repo_root) = crate::git_helpers::get_repo_root() else {
        return;
    };
    let track_file = repo_root.join(WRAPPER_DIR_TRACK_FILE);
    let wrapper_dir = match fs::read_to_string(&track_file) {
        Ok(path) => PathBuf::from(path.trim()),
        Err(_) => return,
    };

    if !wrapper_dir.as_os_str().is_empty() {
        let _ = fs::remove_dir_all(&wrapper_dir);
    }
    let _ = fs::remove_file(track_file);
}

/// Best-effort cleanup for unexpected exits (Ctrl+C, early-return, panics).
pub fn cleanup_agent_phase_silent() {
    end_agent_phase();
    cleanup_git_wrapper_dir_silent();
    uninstall_hooks_silent();
    cleanup_generated_files_silent();
}

/// Cleanup generated files silently without workspace.
///
/// This is a minimal implementation for cleanup in signal handlers where
/// workspace context is not available. Uses `std::fs` directly which is
/// acceptable for this emergency cleanup scenario.
fn cleanup_generated_files_silent() {
    let Ok(repo_root) = crate::git_helpers::get_repo_root() else {
        return;
    };
    for file in crate::files::io::agent_files::GENERATED_FILES {
        let absolute_path = repo_root.join(file);
        let _ = std::fs::remove_file(absolute_path);
    }
}

/// Clean up orphaned .`no_agent_commit` marker.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn cleanup_orphaned_marker(logger: &Logger) -> io::Result<()> {
    let repo_root = get_repo_root()?;
    let marker_path = repo_root.join(".no_agent_commit");

    if marker_path.exists() {
        // Make writable before removal (marker is created as read-only 0o444).
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Ok(meta) = fs::metadata(&marker_path) {
                let mut perms = meta.permissions();
                perms.set_mode(perms.mode() | 0o200);
                let _ = fs::set_permissions(&marker_path, perms);
            }
        }
        fs::remove_file(&marker_path)?;
        logger.success("Removed orphaned .no_agent_commit marker");
    } else {
        logger.info("No orphaned marker found");
    }

    Ok(())
}

// ============================================================================
// Workspace-aware variants
// ============================================================================

/// Create the agent phase marker file using workspace abstraction.
///
/// This is a workspace-aware version of the marker file creation that uses
/// the Workspace trait for file I/O, making it testable with `MemoryWorkspace`.
///
/// # Arguments
///
/// * `workspace` - The workspace to write to
///
/// # Returns
///
/// Returns `Ok(())` on success, or an error if the file cannot be created.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn create_marker_with_workspace(workspace: &dyn Workspace) -> io::Result<()> {
    workspace.write(Path::new(MARKER_FILE), "")
}

/// Remove the agent phase marker file using workspace abstraction.
///
/// This is a workspace-aware version of the marker file removal that uses
/// the Workspace trait for file I/O, making it testable with `MemoryWorkspace`.
///
/// # Arguments
///
/// * `workspace` - The workspace to operate on
///
/// # Returns
///
/// Returns `Ok(())` on success (including if file doesn't exist).
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn remove_marker_with_workspace(workspace: &dyn Workspace) -> io::Result<()> {
    workspace.remove_if_exists(Path::new(MARKER_FILE))
}

/// Check if the agent phase marker file exists using workspace abstraction.
///
/// This is a workspace-aware version that uses the Workspace trait for file I/O,
/// making it testable with `MemoryWorkspace`.
///
/// # Arguments
///
/// * `workspace` - The workspace to check
///
/// # Returns
///
/// Returns `true` if the marker file exists, `false` otherwise.
pub fn marker_exists_with_workspace(workspace: &dyn Workspace) -> bool {
    workspace.exists(Path::new(MARKER_FILE))
}

/// Clean up orphaned marker file using workspace abstraction.
///
/// This is a workspace-aware version of `cleanup_orphaned_marker` that uses
/// the Workspace trait for file I/O, making it testable with `MemoryWorkspace`.
///
/// # Arguments
///
/// * `workspace` - The workspace to operate on
/// * `logger` - Logger for output messages
///
/// # Returns
///
/// Returns `Ok(())` on success.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn cleanup_orphaned_marker_with_workspace(
    workspace: &dyn Workspace,
    logger: &Logger,
) -> io::Result<()> {
    let marker_path = Path::new(MARKER_FILE);

    if workspace.exists(marker_path) {
        workspace.remove(marker_path)?;
        logger.success("Removed orphaned .no_agent_commit marker");
    } else {
        logger.info("No orphaned marker found");
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_wrapper_script_handles_c_flag_before_subcommand() {
        // Verify the wrapper script iterates through arguments to skip global flags
        // like `-C /path`, `--git-dir=.git`, etc. before identifying the subcommand.
        // This ensures `git -C /path commit` is correctly blocked, not just `git commit`.
        let content = make_wrapper_content("git");

        assert!(
            content.contains("skip_next"),
            "wrapper must implement skip_next logic for global flags; got:\n{content}"
        );
        assert!(
            content.contains("-C|--git-dir|--work-tree"),
            "wrapper must recognize -C and --git-dir global flags; got:\n{content}"
        );
        assert!(
            content.contains("for arg in"),
            "wrapper must iterate arguments to find subcommand; got:\n{content}"
        );
    }

    #[test]
    fn test_create_marker_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        // Marker should not exist initially
        assert!(!marker_exists_with_workspace(&workspace));

        // Create marker
        create_marker_with_workspace(&workspace).unwrap();

        // Marker should now exist
        assert!(marker_exists_with_workspace(&workspace));
    }

    #[test]
    fn test_remove_marker_with_workspace() {
        let workspace = MemoryWorkspace::new_test();

        // Create marker first
        create_marker_with_workspace(&workspace).unwrap();
        assert!(marker_exists_with_workspace(&workspace));

        // Remove marker
        remove_marker_with_workspace(&workspace).unwrap();

        // Marker should no longer exist
        assert!(!marker_exists_with_workspace(&workspace));
    }

    #[test]
    fn test_remove_marker_with_workspace_nonexistent() {
        let workspace = MemoryWorkspace::new_test();

        // Removing non-existent marker should succeed silently
        remove_marker_with_workspace(&workspace).unwrap();
        assert!(!marker_exists_with_workspace(&workspace));
    }

    #[test]
    fn test_cleanup_orphaned_marker_with_workspace_exists() {
        let workspace = MemoryWorkspace::new_test();
        let logger = Logger::new(crate::logger::Colors { enabled: false });

        // Create an orphaned marker
        create_marker_with_workspace(&workspace).unwrap();
        assert!(marker_exists_with_workspace(&workspace));

        // Clean up should remove it
        cleanup_orphaned_marker_with_workspace(&workspace, &logger).unwrap();
        assert!(!marker_exists_with_workspace(&workspace));
    }

    #[test]
    fn test_cleanup_orphaned_marker_with_workspace_not_exists() {
        let workspace = MemoryWorkspace::new_test();
        let logger = Logger::new(crate::logger::Colors { enabled: false });

        // No marker exists
        assert!(!marker_exists_with_workspace(&workspace));

        // Clean up should succeed without error
        cleanup_orphaned_marker_with_workspace(&workspace, &logger).unwrap();
        assert!(!marker_exists_with_workspace(&workspace));
    }

    #[test]
    fn test_marker_file_constant() {
        // Verify the constant matches expected value
        assert_eq!(MARKER_FILE, ".no_agent_commit");
    }

    #[test]
    fn test_wrapper_script_blocks_merge_rebase_reset() {
        let content = make_wrapper_content("git");
        for cmd in &["merge", "rebase", "reset"] {
            assert!(
                content.contains(cmd),
                "wrapper must block '{cmd}' subcommand; got:\n{content}"
            );
        }
    }

    #[test]
    fn test_wrapper_script_blocks_cherry_pick_revert_am_apply() {
        let content = make_wrapper_content("git");
        for cmd in &["cherry-pick", "revert", "am", "apply"] {
            assert!(
                content.contains(cmd),
                "wrapper must block '{cmd}' subcommand; got:\n{content}"
            );
        }
    }

    #[test]
    fn test_wrapper_script_blocks_clean_restore() {
        let content = make_wrapper_content("git");
        for cmd in &["clean", "restore"] {
            assert!(
                content.contains(cmd),
                "wrapper must block '{cmd}' subcommand; got:\n{content}"
            );
        }
    }

    #[test]
    fn test_wrapper_script_blocks_stash_mutations() {
        let content = make_wrapper_content("git");
        // Wrapper must handle stash subcommands: block pop/drop/apply, allow list
        assert!(
            content.contains("stash"),
            "wrapper must handle 'stash' subcommand; got:\n{content}"
        );
        for sub in &["pop", "drop"] {
            assert!(
                content.contains(sub),
                "wrapper must block 'stash {sub}'; got:\n{content}"
            );
        }
    }

    #[test]
    fn test_wrapper_script_blocks_branch_delete() {
        let content = make_wrapper_content("git");
        // Wrapper must block branch -d/-D while allowing branch (list)
        assert!(
            content.contains("-D"),
            "wrapper must block 'branch -D'; got:\n{content}"
        );
        assert!(
            content.contains("--delete"),
            "wrapper must block 'branch --delete'; got:\n{content}"
        );
    }

    #[test]
    fn test_wrapper_script_blocks_checkout_double_dash() {
        let content = make_wrapper_content("git");
        // Wrapper must block 'checkout -- <path>' (discard changes)
        assert!(
            content.contains("checkout"),
            "wrapper must handle checkout subcommand; got:\n{content}"
        );
    }
}
