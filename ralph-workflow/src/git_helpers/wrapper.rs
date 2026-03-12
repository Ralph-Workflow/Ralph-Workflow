//! Git wrapper for blocking commits during agent phase.
//!
//! This module provides safety mechanisms to prevent accidental commits while
//! an AI agent is actively modifying files. It works through two mechanisms:
//!
//! - **Marker file**: Creates `<git-dir>/ralph/no_agent_commit` during agent
//!   execution. Both the git wrapper and hooks check for this file.
//! - **PATH wrapper**: Installs a temporary `git` wrapper script that intercepts
//!   `commit`, `push`, and `tag` commands when the marker file exists.
//!
//! All enforcement state files live inside the git metadata directory (`<git-dir>/ralph/`)
//! so they are invisible to working-tree scans and cannot be confused with product code.
//!
//! The wrapper is automatically cleaned up when the agent phase ends, even on
//! unexpected exits (Ctrl+C, panics) via [`cleanup_agent_phase_silent`].

use super::hooks::{reinstall_hooks_if_tampered, uninstall_hooks_silent_at};
use super::repo::{get_repo_root, normalize_protection_scope_path};
use crate::logger::Logger;
use crate::workspace::Workspace;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use which::which;

/// Filename (leaf only) for the enforcement marker inside the ralph git dir.
const MARKER_FILE_NAME: &str = "no_agent_commit";
/// Filename (leaf only) for the wrapper tracking file inside the ralph git dir.
const WRAPPER_TRACK_FILE_NAME: &str = "git-wrapper-dir.txt";
/// Filename (leaf only) for the HEAD OID baseline file inside the ralph git dir.
const HEAD_OID_FILE_NAME: &str = "head-oid.txt";
const WRAPPER_DIR_PREFIX: &str = "ralph-git-wrapper-";
const WRAPPER_MARKER: &str = "RALPH_AGENT_PHASE_GIT_WRAPPER";

/// Process-global repo root set during `start_agent_phase` for signal handler fallback.
///
/// The signal handler needs a reliable repo root when CWD-based discovery may fail.
/// This is set in `start_agent_phase` and cleared in `end_agent_phase_in_repo`.
/// The signal handler uses `try_lock` to avoid deadlock risk.
static AGENT_PHASE_REPO_ROOT: Mutex<Option<PathBuf>> = Mutex::new(None);

/// Process-global ralph git dir set during `start_agent_phase_in_repo`.
///
/// Signal handlers cannot call libgit2, so we pre-compute the ralph dir path
/// on the main thread and store it here. Signal handlers read via `try_lock`.
static AGENT_PHASE_RALPH_DIR: Mutex<Option<PathBuf>> = Mutex::new(None);

/// Process-global hooks dir set during `start_agent_phase_in_repo`.
///
/// Used by signal handler cleanup to avoid recomputation via libgit2.
/// For linked worktrees, hooks are worktree-scoped, so this ensures the signal
/// handler cleans the active worktree's hooks instead of touching siblings.
static AGENT_PHASE_HOOKS_DIR: Mutex<Option<PathBuf>> = Mutex::new(None);

/// Result of checking and self-healing agent-phase protections.
///
/// When `ensure_agent_phase_protections` detects and repairs tampering,
/// this struct records what was found so the caller can take action
/// (e.g., log a stronger warning or flag the agent run as compromised).
#[derive(Debug, Clone, Default)]
pub struct ProtectionCheckResult {
    /// Whether any tampering was detected and self-healed.
    pub tampering_detected: bool,
    /// Human-readable descriptions of each self-healing action taken.
    pub details: Vec<String>,
}

fn legacy_marker_path(repo_root: &Path) -> PathBuf {
    repo_root.join(".no_agent_commit")
}

fn repair_marker_path_if_tampered(repo_root: &Path) -> io::Result<()> {
    let ralph_dir = super::repo::ralph_git_dir(repo_root);
    let marker_path = ralph_dir.join(MARKER_FILE_NAME);

    if let Ok(meta) = fs::symlink_metadata(&marker_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            super::repo::quarantine_path_in_place(&marker_path, "marker")?;
        }
    }

    create_marker_in_repo_root(repo_root)
}

fn create_marker_in_repo_root(repo_root: &Path) -> io::Result<()> {
    let ralph_dir = super::repo::ensure_ralph_git_dir(repo_root)?;
    let marker_path = ralph_dir.join(MARKER_FILE_NAME);

    if let Ok(meta) = fs::symlink_metadata(&marker_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if is_regular_file {
            return Ok(());
        }

        // Any non-regular marker path (symlink/dir/socket/FIFO/device/etc) can bypass
        // hook/wrapper `-f` checks. Quarantine it and recreate a regular file marker.
        super::repo::quarantine_path_in_place(&marker_path, "marker")?;
    }

    let open_res = {
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(&marker_path)
        }
        #[cfg(not(unix))]
        {
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&marker_path)
        }
    };

    match open_res {
        Ok(mut f) => {
            f.write_all(b"")?;
            f.flush()?;
            let _ = f.sync_all();
        }
        Err(ref e) if e.kind() == io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e),
    }

    Ok(())
}

#[cfg(unix)]
fn add_owner_write_if_not_symlink(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    if matches!(fs::symlink_metadata(path), Ok(meta) if meta.file_type().is_symlink()) {
        return;
    }
    if let Ok(meta) = fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_mode(perms.mode() | 0o200);
        let _ = fs::set_permissions(path, perms);
    }
}

#[cfg(unix)]
fn set_readonly_mode_if_not_symlink(path: &Path, mode: u32) {
    use std::os::unix::fs::PermissionsExt;
    if matches!(fs::symlink_metadata(path), Ok(meta) if meta.file_type().is_symlink()) {
        return;
    }
    if let Ok(meta) = fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_mode(mode);
        let _ = fs::set_permissions(path, perms);
    }
}

fn relax_temp_cleanup_permissions_if_regular_file(path: &Path) {
    let Ok(meta) = fs::symlink_metadata(path) else {
        return;
    };
    let file_type = meta.file_type();
    if !file_type.is_file() || file_type.is_symlink() {
        return;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = meta.permissions();
        perms.set_mode(perms.mode() | 0o200);
        let _ = fs::set_permissions(path, perms);
    }

    #[cfg(windows)]
    {
        let mut perms = meta.permissions();
        perms.set_readonly(false);
        let _ = fs::set_permissions(path, perms);
    }
}

/// Git helper state.
pub struct GitHelpers {
    real_git: Option<PathBuf>,
    wrapper_dir: Option<PathBuf>,
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

fn find_git_in_path_excluding_dir(exclude_dir: &Path) -> Option<PathBuf> {
    let path_var = env::var("PATH").ok()?;
    let exclude = exclude_dir.to_string_lossy();
    let wrapper_path = exclude_dir.join("git");
    for entry in path_var.split(':') {
        if entry.is_empty() {
            continue;
        }
        // Exclude the wrapper dir so we don't resolve the wrapper as "real git".
        if entry == exclude {
            continue;
        }
        let candidate = Path::new(entry).join("git");
        if candidate == wrapper_path {
            continue;
        }
        if !candidate.exists() {
            continue;
        }
        if matches!(fs::metadata(&candidate), Ok(meta) if meta.file_type().is_file()) {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if let Ok(meta) = fs::metadata(&candidate) {
                    let mode = meta.permissions().mode() & 0o777;
                    if (mode & 0o111) == 0 {
                        continue;
                    }
                }
            }
            return Some(candidate);
        }
    }
    None
}

fn path_has_parent_dir_component(path: &Path) -> bool {
    path.components()
        .any(|c| matches!(c, std::path::Component::ParentDir))
}

fn wrapper_dir_is_reasonable_temp_path(path: &Path) -> bool {
    if !path.is_absolute() {
        return false;
    }
    if path_has_parent_dir_component(path) {
        return false;
    }
    let temp_dir = env::temp_dir();
    if !path.starts_with(&temp_dir) {
        // On macOS, `env::temp_dir()` can be under a symlinked prefix.
        // Accept the canonicalized temp dir prefix as well.
        let Ok(temp_dir_canon) = fs::canonicalize(&temp_dir) else {
            return false;
        };
        if !path.starts_with(&temp_dir_canon) {
            return false;
        }
    }
    let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
        return false;
    };
    name.starts_with(WRAPPER_DIR_PREFIX)
}

fn wrapper_dir_is_safe_existing_dir(path: &Path) -> bool {
    if !wrapper_dir_is_reasonable_temp_path(path) {
        return false;
    }
    let Ok(meta) = fs::symlink_metadata(path) else {
        return false;
    };
    if meta.file_type().is_symlink() {
        return false;
    }
    meta.is_dir()
}

fn wrapper_dir_is_on_path(path: &Path) -> bool {
    let Ok(path_var) = env::var("PATH") else {
        return false;
    };
    path_var
        .split(':')
        .any(|entry| !entry.is_empty() && Path::new(entry) == path)
}

fn find_wrapper_dir_on_path() -> Option<PathBuf> {
    let path_var = env::var("PATH").ok()?;
    for entry in path_var.split(':') {
        if entry.is_empty() {
            continue;
        }
        let p = PathBuf::from(entry);
        if wrapper_dir_is_reasonable_temp_path(&p) {
            return Some(p);
        }
    }
    None
}

fn ensure_wrapper_dir_prepended_to_path(wrapper_dir: &Path) {
    let current_path = env::var("PATH").unwrap_or_default();
    if current_path
        .split(':')
        .next()
        .is_some_and(|first| !first.is_empty() && Path::new(first) == wrapper_dir)
    {
        return;
    }
    env::set_var(
        "PATH",
        format!("{}:{}", wrapper_dir.display(), current_path),
    );
}

fn write_wrapper_track_file_atomic(repo_root: &Path, wrapper_dir: &Path) -> io::Result<()> {
    let ralph_dir = super::repo::ensure_ralph_git_dir(repo_root)?;

    let track_file_path = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);

    // If the track file path is a directory/symlink/special file, treat it as tampering.
    // Quarantine it so we can atomically replace it with a regular file.
    if let Ok(meta) = fs::symlink_metadata(&track_file_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            super::repo::quarantine_path_in_place(&track_file_path, "track")?;
        }
    }

    let tmp_track = ralph_dir.join(format!(
        ".git-wrapper-dir.tmp.{}.{}",
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
            .open(&tmp_track)?;
        tf.write_all(wrapper_dir.display().to_string().as_bytes())?;
        tf.write_all(b"\n")?;
        tf.flush()?;
        let _ = tf.sync_all();
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&tmp_track)?.permissions();
        perms.set_mode(0o444);
        fs::set_permissions(&tmp_track, perms)?;
    }
    #[cfg(windows)]
    {
        let mut perms = fs::metadata(&tmp_track)?.permissions();
        perms.set_readonly(true);
        fs::set_permissions(&tmp_track, perms)?;
    }

    // Rename is symlink-safe: it replaces the directory entry.
    #[cfg(windows)]
    {
        if track_file_path.exists() {
            let _ = fs::remove_file(&track_file_path);
        }
    }
    fs::rename(&tmp_track, &track_file_path)
}

/// Generate the git wrapper script content.
///
/// When protections are active, the wrapper enforces a strict allowlist of
/// read-only subcommands and blocks everything else.
///
/// Protections are considered active when either:
/// - `<git-dir>/ralph/no_agent_commit` exists (absolute path embedded at install time), OR
/// - `<git-dir>/ralph/git-wrapper-dir.txt` exists (defense-in-depth against marker deletion).
///
/// `git_path_escaped`, `marker_path_escaped`, and `track_file_path_escaped` must already be
/// shell-single-quote-escaped absolute paths.
fn make_wrapper_content(
    git_path_escaped: &str,
    marker_path_escaped: &str,
    track_file_path_escaped: &str,
    active_repo_root_escaped: &str,
    active_git_dir_escaped: &str,
) -> String {
    format!(
        r#"#!/usr/bin/env bash
 set -euo pipefail
  # {WRAPPER_MARKER} - generated by ralph
  # NOTE: `command git` still routes through this PATH wrapper because `command`
  # only skips shell functions and aliases, not PATH entries. This wrapper is a
  # real file in PATH, so it is always invoked for any `git` command.
  marker='{marker_path_escaped}'
  track_file='{track_file_path_escaped}'
  active_repo_root='{active_repo_root_escaped}'
  active_git_dir='{active_git_dir_escaped}'
  path_is_within() {{
    local candidate="$1"
    local scope_root="$2"
    [[ "$candidate" == "$scope_root" || "$candidate" == "$scope_root"/* ]]
  }}
  normalize_scope_dir() {{
    local candidate="$1"
    if [ -z "$candidate" ] || [ ! -d "$candidate" ]; then
      printf '%s\n' "$candidate"
      return
    fi
    if canonical=$(cd "$candidate" 2>/dev/null && pwd -P); then
      printf '%s\n' "$canonical"
    else
      printf '%s\n' "$candidate"
    fi
  }}
  # Treat either the marker or the wrapper track file as an active agent-phase signal.
  # This makes the wrapper resilient if an agent deletes the marker mid-run.
  if [ -f "$marker" ] || [ -f "$track_file" ]; then
    # Unset environment variables that could be used to bypass the wrapper
    # by pointing git at a different repository or exec path.
    unset GIT_DIR
    unset GIT_WORK_TREE
    unset GIT_EXEC_PATH
    subcmd=""
   repo_args=()
   repo_arg_pending=0
   skip_next=0
    for arg in "$@"; do
      if [ "$repo_arg_pending" = "1" ]; then
        repo_args+=("$arg")
        repo_arg_pending=0
        continue
      fi
      if [ "$skip_next" = "1" ]; then
        skip_next=0
        continue
      fi
      case "$arg" in
       -C|--git-dir|--work-tree)
         repo_args+=("$arg")
         repo_arg_pending=1
         ;;
      --git-dir=*|--work-tree=*|-C=*)
        repo_args+=("$arg")
        ;;
      --namespace|-c|--config|--exec-path)
        skip_next=1
        ;;
      --namespace=*|--exec-path=*|-c=*|--config=*)
        ;;
      -*)
        ;;
      *)
        subcmd="$arg"
        break
        ;;
     esac
    done
    target_repo_root=""
    target_git_dir=""
    if [ "${{#repo_args[@]}}" -gt 0 ] && \
       target_repo_root=$( '{git_path_escaped}' "${{repo_args[@]}}" rev-parse --path-format=absolute --show-toplevel 2>/dev/null ) && \
       target_git_dir=$( '{git_path_escaped}' "${{repo_args[@]}}" rev-parse --path-format=absolute --git-dir 2>/dev/null ); then
      :
    elif target_repo_root=$( '{git_path_escaped}' rev-parse --path-format=absolute --show-toplevel 2>/dev/null ) && \
         target_git_dir=$( '{git_path_escaped}' rev-parse --path-format=absolute --git-dir 2>/dev/null ); then
      :
    elif path_is_within "$PWD" "$active_repo_root"; then
      target_repo_root="$active_repo_root"
      target_git_dir="$active_git_dir"
    fi
    protection_scope_active=0
    normalized_target_repo_root=$(normalize_scope_dir "$target_repo_root")
    normalized_target_git_dir=$(normalize_scope_dir "$target_git_dir")
    if [ -n "$target_repo_root" ] && [ -n "$target_git_dir" ] && \
       [ "$normalized_target_repo_root" = "$active_repo_root" ] && [ "$normalized_target_git_dir" = "$active_git_dir" ]; then
      protection_scope_active=1
    fi
    if [ "$protection_scope_active" = "1" ]; then
    case "$subcmd" in
      "")
        # `git` with no subcommand is effectively help/version output.
       ;;
     status|log|diff|show|rev-parse|ls-files|describe)
       # Explicitly allowed read-only lookup commands.
       ;;
     stash)
       # Allow only `git stash list`.
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
       if [ "$stash_sub" != "list" ]; then
         echo "Blocked: git stash disabled during agent phase (only 'stash list' allowed)." >&2
         exit 1
       fi
       ;;
     branch)
       # Allow only explicit read-only `git branch` forms.
       found_branch=0
       branch_allows_value=0
       for a2 in "$@"; do
         if [ "$branch_allows_value" = "1" ]; then
           branch_allows_value=0
           continue
         fi
         if [ "$found_branch" = "1" ]; then
           case "$a2" in
             --list|-l|--all|-a|--remotes|-r|--verbose|-v|--vv|--show-current|--column|--no-column|--color|--no-color|--ignore-case|--omit-empty)
               ;;
             --contains|--no-contains|--merged|--no-merged|--points-at|--sort|--format|--abbrev)
               branch_allows_value=1
               ;;
             --contains=*|--no-contains=*|--merged=*|--no-merged=*|--points-at=*|--sort=*|--format=*|--abbrev=*)
               ;;
             *)
               echo "Blocked: git branch disabled during agent phase (read-only forms only; mutating flags like --unset-upstream are blocked)." >&2
               exit 1
               ;;
           esac
         fi
         if [ "$a2" = "branch" ]; then found_branch=1; fi
       done
       ;;
     remote)
       # Allow only list-only forms of `git remote` (no positional args).
       found_remote=0
       for a2 in "$@"; do
         if [ "$found_remote" = "1" ]; then
           case "$a2" in
             -*) ;;
             *)
               echo "Blocked: git remote <subcommand> disabled during agent phase (list-only allowed)." >&2
               exit 1
               ;;
           esac
         fi
         if [ "$a2" = "remote" ]; then found_remote=1; fi
       done
       ;;
      *)
        echo "Blocked: git $subcmd disabled during agent phase (read-only allowlist)." >&2
        exit 1
        ;;
    esac
    fi
  fi
  exec '{git_path_escaped}' "$@"
  "#
    )
}

/// Enable git wrapper that blocks commits during agent phase.
fn enable_git_wrapper_at(repo_root: &Path, helpers: &mut GitHelpers) -> io::Result<()> {
    // Clean up orphaned wrapper dir from a prior crashed run before creating a new one.
    // This prevents /tmp leaks on every crash-restart cycle.
    cleanup_prior_wrapper_from_track_file(repo_root);

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

    let wrapper_dir = tempfile::Builder::new()
        .prefix(WRAPPER_DIR_PREFIX)
        .tempdir()?;
    let wrapper_dir_path = wrapper_dir.keep();
    let wrapper_path = wrapper_dir_path.join("git");

    // Escape the git path for shell script to prevent command injection.
    // Use a helper function to properly handle edge cases and reject unsafe paths.
    let git_path_escaped = escape_shell_single_quoted(git_path_str)?;

    helpers.wrapper_repo_root = Some(repo_root.to_path_buf());

    let scope = super::repo::resolve_protection_scope_from(repo_root)?;
    let ralph_dir = scope.ralph_dir.clone();
    let marker_path = ralph_dir.join(MARKER_FILE_NAME);
    let track_file_path = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
    let normalized_repo_root = normalize_protection_scope_path(&scope.repo_root);
    let normalized_git_dir = normalize_protection_scope_path(&scope.git_dir);
    let repo_root_str = normalized_repo_root.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "repo root contains invalid UTF-8 characters; cannot create wrapper script",
        )
    })?;
    let git_dir_str = normalized_git_dir.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "git dir contains invalid UTF-8 characters; cannot create wrapper script",
        )
    })?;

    let marker_path_str = marker_path.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "marker path contains invalid UTF-8 characters; cannot create wrapper script",
        )
    })?;
    let track_file_path_str = track_file_path.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "track file path contains invalid UTF-8 characters; cannot create wrapper script",
        )
    })?;
    let marker_path_escaped = escape_shell_single_quoted(marker_path_str)?;
    let track_file_path_escaped = escape_shell_single_quoted(track_file_path_str)?;
    let repo_root_escaped = escape_shell_single_quoted(repo_root_str)?;
    let git_dir_escaped = escape_shell_single_quoted(git_dir_str)?;

    let wrapper_content = make_wrapper_content(
        &git_path_escaped,
        &marker_path_escaped,
        &track_file_path_escaped,
        &repo_root_escaped,
        &git_dir_escaped,
    );

    // Create wrapper file; wrapper dir is freshly created under temp.
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&wrapper_path)?;
    file.write_all(wrapper_content.as_bytes())?;

    // Make read-only executable (0o555) to deter agent overwriting, matching
    // the pattern used for hooks.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&wrapper_path)?.permissions();
        perms.set_mode(0o555);
        fs::set_permissions(&wrapper_path, perms)?;
    }

    // Prepend wrapper dir to PATH.
    let current_path = env::var("PATH").unwrap_or_default();
    env::set_var(
        "PATH",
        format!("{}:{}", wrapper_dir_path.display(), current_path),
    );

    // Persist wrapper dir location for best-effort cleanup and self-heal.
    write_wrapper_track_file_atomic(repo_root, &wrapper_dir_path)?;

    helpers.wrapper_dir = Some(wrapper_dir_path);
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
    let removed_wrapper_dir = helpers.wrapper_dir.take();
    removed_wrapper_dir.as_ref().inspect(|wrapper_dir_path| {
        remove_wrapper_dir_and_path_entry(wrapper_dir_path);
    });

    // IMPORTANT: remove the tracking file using an absolute repo root path.
    // The process CWD may not be the repo root (e.g., tests or effects that change CWD).
    let repo_root = helpers
        .wrapper_repo_root
        .take()
        .or_else(|| crate::git_helpers::get_repo_root().ok());

    let track_file = repo_root.as_ref().map_or_else(
        || {
            // Last-resort fallback when repo root is unknown: use CWD-relative guess.
            PathBuf::from(".git/ralph").join(WRAPPER_TRACK_FILE_NAME)
        },
        |r| super::repo::ralph_git_dir(r).join(WRAPPER_TRACK_FILE_NAME),
    );

    // If we didn't have in-memory wrapper state (or it was out of date), fall back
    // to the track file for best-effort cleanup of the wrapper dir in /tmp.
    if let Ok(content) = fs::read_to_string(&track_file) {
        let wrapper_dir = PathBuf::from(content.trim());
        let same_as_removed = removed_wrapper_dir
            .as_ref()
            .is_some_and(|p| p == &wrapper_dir);
        if !same_as_removed {
            remove_wrapper_dir_and_path_entry(&wrapper_dir);
        }
    }

    // ALWAYS remove the track file. Hooks check marker OR track_file with ||
    // logic, so a surviving track file blocks commits even after the marker is
    // removed. The wrapper dir in /tmp is harmless and will be cleaned by the OS.
    #[cfg(unix)]
    add_owner_write_if_not_symlink(&track_file);
    let _ = fs::remove_file(&track_file);
}

fn remove_path_entry(path_to_remove: &Path) {
    // Note: This read-modify-write sequence on PATH has a theoretical TOCTOU race,
    // but in practice it's safe because Ralph only calls this from the main thread
    // during controlled shutdown.
    if let Ok(path) = env::var("PATH") {
        let new_path: String = path
            .split(':')
            .filter(|p| !p.is_empty() && Path::new(p) != path_to_remove)
            .collect::<Vec<_>>()
            .join(":");
        env::set_var("PATH", new_path);
    }
}

fn remove_wrapper_dir_and_path_entry(wrapper_dir: &Path) -> bool {
    remove_path_entry(wrapper_dir);

    if wrapper_dir_is_safe_existing_dir(wrapper_dir) {
        make_wrapper_script_writable(wrapper_dir);
        let _ = fs::remove_dir_all(wrapper_dir);
    }

    !wrapper_dir.exists()
}

fn make_wrapper_script_writable(wrapper_dir_path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let wrapper_path = wrapper_dir_path.join("git");
        if let Ok(meta) = fs::metadata(&wrapper_path) {
            let mut perms = meta.permissions();
            perms.set_mode(perms.mode() | 0o200);
            let _ = fs::set_permissions(&wrapper_path, perms);
        }
    }
}

/// Start agent phase (creates marker file, installs hooks, enables wrapper).
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn start_agent_phase(helpers: &mut GitHelpers) -> io::Result<()> {
    let repo_root = get_repo_root()?;
    start_agent_phase_in_repo(&repo_root, helpers)
}

/// Start agent phase for an explicit repository root.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn start_agent_phase_in_repo(repo_root: &Path, helpers: &mut GitHelpers) -> io::Result<()> {
    helpers.wrapper_repo_root = Some(repo_root.to_path_buf());

    // Compute ralph dir once on the main thread (libgit2 is safe here).
    let ralph_dir = super::repo::ralph_git_dir(repo_root);

    // Store repo root and ralph dir for signal handler fallback.
    if let Ok(mut guard) = AGENT_PHASE_REPO_ROOT.lock() {
        *guard = Some(repo_root.to_path_buf());
    }
    if let Ok(mut guard) = AGENT_PHASE_RALPH_DIR.lock() {
        *guard = Some(ralph_dir.clone());
    }

    // Store hooks dir for signal handler cleanup reliability.
    if let Ok(hooks_dir) = super::repo::get_hooks_dir_from(repo_root) {
        if let Ok(mut guard) = AGENT_PHASE_HOOKS_DIR.lock() {
            *guard = Some(hooks_dir);
        }
    }

    // Self-heal: treat non-regular marker path as tampering and recover.
    repair_marker_path_if_tampered(repo_root)?;
    // Make marker read-only (0o444) to deter agent deletion.
    #[cfg(unix)]
    set_readonly_mode_if_not_symlink(&ralph_dir.join(MARKER_FILE_NAME), 0o444);
    super::hooks::install_hooks_in_repo(repo_root)?;
    enable_git_wrapper_at(repo_root, helpers)?;

    // Capture HEAD OID baseline for unauthorized commit detection.
    capture_head_oid(repo_root);
    Ok(())
}

/// End agent phase (removes marker file).
pub fn end_agent_phase() {
    let Ok(repo_root) = crate::git_helpers::get_repo_root() else {
        return;
    };
    end_agent_phase_in_repo(&repo_root);
}

/// End agent phase for an explicit repository.
///
/// This avoids relying on the process current working directory to locate the repo.
///
/// **Note:** This function does NOT clear the process-global mutexes
/// (`AGENT_PHASE_REPO_ROOT`, `AGENT_PHASE_RALPH_DIR`, `AGENT_PHASE_HOOKS_DIR`).
/// Callers must invoke [`clear_agent_phase_global_state`] after ALL cleanup steps
/// (wrapper removal, hook uninstallation) are complete. This prevents a race
/// where SIGINT arrives between mutex clearing and hook cleanup, causing the
/// signal handler to find empty mutexes and skip cleanup.
pub fn end_agent_phase_in_repo(repo_root: &Path) {
    let ralph_dir = super::repo::ralph_git_dir(repo_root);
    end_agent_phase_in_repo_at_ralph_dir(repo_root, &ralph_dir);
}

/// Clear the process-global agent-phase state mutexes.
///
/// Must be called after ALL cleanup steps (marker removal, wrapper removal,
/// hook uninstallation) are complete. Clearing earlier creates a race window
/// where SIGINT can arrive with empty mutexes, causing incomplete cleanup.
pub fn clear_agent_phase_global_state() {
    if let Ok(mut guard) = AGENT_PHASE_REPO_ROOT.lock() {
        *guard = None;
    }
    if let Ok(mut guard) = AGENT_PHASE_RALPH_DIR.lock() {
        *guard = None;
    }
    if let Ok(mut guard) = AGENT_PHASE_HOOKS_DIR.lock() {
        *guard = None;
    }
}

fn end_agent_phase_in_repo_at_ralph_dir(repo_root: &Path, ralph_dir: &Path) {
    // Legacy marker cleanup (always attempt).
    let legacy_marker = legacy_marker_path(repo_root);
    #[cfg(unix)]
    add_owner_write_if_not_symlink(&legacy_marker);
    let _ = fs::remove_file(&legacy_marker);

    // Always attempt marker removal regardless of sanitize result.
    // sanitize may fail due to transient metadata issues, but the
    // marker file itself may still be removable.
    let ralph_dir_ok = super::repo::sanitize_ralph_git_dir_at(ralph_dir).unwrap_or(false);

    let marker_path = ralph_dir.join(MARKER_FILE_NAME);
    // Make writable before removal (marker is created as read-only 0o444).
    #[cfg(unix)]
    add_owner_write_if_not_symlink(&marker_path);
    let _ = fs::remove_file(&marker_path);

    // Only attempt head-oid and dir cleanup if sanitize confirmed dir exists.
    if ralph_dir_ok {
        remove_head_oid_file_at(ralph_dir);
        cleanup_stray_tmp_files_in_ralph_dir(ralph_dir);
        let _ = fs::remove_dir(ralph_dir);
    }
}

/// Verify and restore agent-phase commit protections before each agent invocation.
///
/// This is the composite integrity check that self-heals against a prior agent
/// that deleted the enforcement marker or tampered with git hooks during
/// its run. It is designed to be called from `run_with_prompt` before every
/// agent spawn.
///
/// The `run_with_prompt` call site is authoritative that agent-phase protections
/// should be active. Missing protections are treated as tampering (or corruption)
/// and will be self-healed.
///
/// # Limitations
///
/// This check protects the *next* agent invocation.
///
/// Within a single invocation, defense-in-depth depends on multiple layers:
/// hooks and the PATH wrapper. The wrapper additionally treats the wrapper track
/// file as an agent-phase signal to remain effective if the marker is deleted.
///
/// If an agent deletes the marker, hooks, and wrapper track file and then invokes
/// a real `git` binary via an absolute path, protections can be bypassed until
/// this check runs again.
///
/// Errors are logged as warnings only — a missing git repo (e.g., in tests
/// without a real repo) should not crash the pipeline.
#[must_use]
pub fn ensure_agent_phase_protections(logger: &Logger) -> ProtectionCheckResult {
    let mut result = ProtectionCheckResult::default();

    let Ok(scope) = super::repo::resolve_protection_scope() else {
        return result;
    };
    let repo_root = scope.repo_root.clone();

    let ralph_dir = scope.ralph_dir.clone();
    let marker_path = ralph_dir.join(MARKER_FILE_NAME);
    if let Ok(meta) = fs::symlink_metadata(&marker_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            logger.warn("Enforcement marker is not a regular file — quarantining and recreating");
            result.tampering_detected = true;
            result
                .details
                .push("Enforcement marker was not a regular file — quarantined".to_string());
            if let Err(e) = super::repo::quarantine_path_in_place(&marker_path, "marker") {
                logger.warn(&format!("Failed to quarantine marker path: {e}"));
                result
                    .details
                    .push("Marker path quarantine failed".to_string());
            }
        }
    }

    let marker_meta = fs::symlink_metadata(&marker_path).ok();
    let marker_is_symlink = marker_meta
        .as_ref()
        .is_some_and(|m| m.file_type().is_symlink());
    let marker_exists = marker_meta
        .as_ref()
        .is_some_and(|m| m.file_type().is_file() && !m.file_type().is_symlink());

    // Ensure the PATH wrapper is present and intact.
    //
    // CRITICAL: Treat the track file as untrusted input.
    // We only use it if it points to a plausible temp directory AND that directory is
    // already present on PATH (meaning it was installed by Ralph).
    let track_file_path = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
    if let Ok(meta) = fs::symlink_metadata(&track_file_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            logger.warn("Git wrapper tracking path is not a regular file — quarantining");
            result.tampering_detected = true;
            result
                .details
                .push("Git wrapper tracking path was not a regular file — quarantined".to_string());
            if let Err(e) = super::repo::quarantine_path_in_place(&track_file_path, "track") {
                logger.warn(&format!("Failed to quarantine wrapper tracking path: {e}"));
                result
                    .details
                    .push("Wrapper tracking path quarantine failed".to_string());
            }
        }
    }

    let tracked_wrapper_dir = fs::read_to_string(&track_file_path).ok().and_then(|s| {
        let p = PathBuf::from(s.trim());
        if wrapper_dir_is_safe_existing_dir(&p) && wrapper_dir_is_on_path(&p) {
            Some(p)
        } else {
            None
        }
    });

    let path_wrapper_dir =
        find_wrapper_dir_on_path().filter(|p| wrapper_dir_is_safe_existing_dir(p));

    let wrapper_dir = tracked_wrapper_dir.clone().or(path_wrapper_dir);

    // Ensure the wrapper dir is first on PATH to defend against PATH reordering.
    if let Some(ref dir) = wrapper_dir {
        ensure_wrapper_dir_prepended_to_path(dir);
    }

    // If the track file is missing or points elsewhere, rewrite it to the PATH wrapper dir.
    if tracked_wrapper_dir.is_none() {
        if let Some(ref dir) = wrapper_dir {
            logger.warn("Git wrapper tracking file missing or invalid — restoring");
            result.tampering_detected = true;
            result
                .details
                .push("Git wrapper tracking file missing or invalid — restored".to_string());

            // Best-effort rewrite: failures here should not crash the pipeline.
            if let Err(e) = write_wrapper_track_file_atomic(&repo_root, dir) {
                logger.warn(&format!("Failed to restore wrapper tracking file: {e}"));
            }
        }
    }

    // Restore wrapper script content/permissions if missing or tampered.
    if let Some(wrapper_dir) = wrapper_dir {
        let wrapper_path = wrapper_dir.join("git");
        let wrapper_needs_restore = fs::read_to_string(&wrapper_path).map_or(true, |content| {
            !content.contains(WRAPPER_MARKER) || !content.contains("unset GIT_EXEC_PATH")
        });

        if wrapper_needs_restore {
            logger.warn("Git wrapper script missing or tampered — restoring");
            result.tampering_detected = true;
            result
                .details
                .push("Git wrapper script missing or tampered — restored".to_string());

            // Resolve the real git binary by searching PATH excluding the wrapper dir.
            let real_git =
                find_git_in_path_excluding_dir(&wrapper_dir).or_else(|| which("git").ok());

            match real_git {
                Some(real_git_path) => {
                    let Some(real_git_str) = real_git_path.to_str() else {
                        logger.warn(
                            "Resolved git binary path is not valid UTF-8; cannot restore wrapper",
                        );
                        return result;
                    };
                    let Ok(git_path_escaped) = escape_shell_single_quoted(real_git_str) else {
                        logger.warn("Failed to generate safe wrapper script (git path)");
                        return result;
                    };
                    let marker_p = ralph_dir.join(MARKER_FILE_NAME);
                    let track_p = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
                    let Some(marker_str) = marker_p.to_str() else {
                        logger.warn("Marker path is not valid UTF-8; cannot restore wrapper");
                        return result;
                    };
                    let Some(track_str) = track_p.to_str() else {
                        logger.warn("Track file path is not valid UTF-8; cannot restore wrapper");
                        return result;
                    };
                    let Ok(marker_escaped) = escape_shell_single_quoted(marker_str) else {
                        logger.warn("Failed to generate safe wrapper script (marker path)");
                        return result;
                    };
                    let Ok(track_escaped) = escape_shell_single_quoted(track_str) else {
                        logger.warn("Failed to generate safe wrapper script (track file path)");
                        return result;
                    };
                    let normalized_repo_root = normalize_protection_scope_path(&repo_root);
                    let normalized_git_dir = normalize_protection_scope_path(&scope.git_dir);
                    let Some(repo_root_str) = normalized_repo_root.to_str() else {
                        logger.warn("Repo root is not valid UTF-8; cannot restore wrapper");
                        return result;
                    };
                    let Some(git_dir_str) = normalized_git_dir.to_str() else {
                        logger.warn("Git dir is not valid UTF-8; cannot restore wrapper");
                        return result;
                    };
                    let Ok(repo_root_escaped) = escape_shell_single_quoted(repo_root_str) else {
                        logger.warn("Failed to generate safe wrapper script (repo root)");
                        return result;
                    };
                    let Ok(git_dir_escaped) = escape_shell_single_quoted(git_dir_str) else {
                        logger.warn("Failed to generate safe wrapper script (git dir)");
                        return result;
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

                    let open_tmp = {
                        #[cfg(unix)]
                        {
                            use std::os::unix::fs::OpenOptionsExt;
                            OpenOptions::new()
                                .write(true)
                                .create_new(true)
                                .custom_flags(libc::O_NOFOLLOW)
                                .open(&tmp_path)
                        }
                        #[cfg(not(unix))]
                        {
                            OpenOptions::new()
                                .write(true)
                                .create_new(true)
                                .open(&tmp_path)
                        }
                    };

                    match open_tmp.and_then(|mut f| {
                        f.write_all(wrapper_content.as_bytes())?;
                        f.flush()?;
                        let _ = f.sync_all();
                        Ok(())
                    }) {
                        Ok(()) => {
                            #[cfg(unix)]
                            {
                                use std::os::unix::fs::PermissionsExt;
                                if let Ok(meta) = fs::metadata(&tmp_path) {
                                    let mut perms = meta.permissions();
                                    perms.set_mode(0o555);
                                    let _ = fs::set_permissions(&tmp_path, perms);
                                }
                            }
                            #[cfg(windows)]
                            {
                                if let Ok(meta) = fs::metadata(&tmp_path) {
                                    let mut perms = meta.permissions();
                                    perms.set_readonly(true);
                                    let _ = fs::set_permissions(&tmp_path, perms);
                                }
                                if wrapper_path.exists() {
                                    let _ = fs::remove_file(&wrapper_path);
                                }
                            }
                            if let Err(e) = fs::rename(&tmp_path, &wrapper_path) {
                                let _ = fs::remove_file(&tmp_path);
                                logger.warn(&format!("Failed to restore wrapper script: {e}"));
                            }
                        }
                        Err(e) => {
                            logger.warn(&format!("Failed to write wrapper temp file: {e}"));
                        }
                    }

                    // Defense-in-depth: validate we didn't resolve the wrapper itself.
                    if real_git_path == wrapper_path {
                        logger.warn(
                            "Resolved git binary points to wrapper; wrapper restore may be incomplete",
                        );
                    }
                }
                None => {
                    logger.warn("Failed to resolve real git binary; cannot restore wrapper");
                }
            }
        }

        // Restore wrapper permissions (0o555) if loosened.
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
        // Wrapper missing from PATH and no valid track file — re-enable.
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
                // Continue with hooks/marker self-heal; wrapper is defense-in-depth.
                return result;
            }
        };
        ensure_wrapper_dir_prepended_to_path(&wrapper_dir);

        let real_git = find_git_in_path_excluding_dir(&wrapper_dir).or_else(|| which("git").ok());
        if let Some(real_git_path) = real_git {
            if let Some(real_git_str) = real_git_path.to_str() {
                let marker_p = ralph_dir.join(MARKER_FILE_NAME);
                let track_p = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
                if let (Ok(git_path_escaped), Some(marker_str), Some(track_str)) = (
                    escape_shell_single_quoted(real_git_str),
                    marker_p.to_str(),
                    track_p.to_str(),
                ) {
                    if let (Ok(marker_escaped), Ok(track_escaped)) = (
                        escape_shell_single_quoted(marker_str),
                        escape_shell_single_quoted(track_str),
                    ) {
                        let normalized_repo_root = normalize_protection_scope_path(&repo_root);
                        let normalized_git_dir = normalize_protection_scope_path(&scope.git_dir);
                        let Some(repo_root_str) = normalized_repo_root.to_str() else {
                            logger.warn("Repo root is not valid UTF-8; cannot restore wrapper");
                            return result;
                        };
                        let Some(git_dir_str) = normalized_git_dir.to_str() else {
                            logger.warn("Git dir is not valid UTF-8; cannot restore wrapper");
                            return result;
                        };
                        let Ok(repo_root_escaped) = escape_shell_single_quoted(repo_root_str)
                        else {
                            logger.warn("Failed to generate safe wrapper script (repo root)");
                            return result;
                        };
                        let Ok(git_dir_escaped) = escape_shell_single_quoted(git_dir_str) else {
                            logger.warn("Failed to generate safe wrapper script (git dir)");
                            return result;
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
                            {
                                use std::os::unix::fs::PermissionsExt;
                                if let Ok(meta) = fs::metadata(&wrapper_path) {
                                    let mut perms = meta.permissions();
                                    perms.set_mode(0o555);
                                    let _ = fs::set_permissions(&wrapper_path, perms);
                                }
                            }
                        }
                    }
                }
            }
        }

        // Best-effort track file write.
        if let Err(e) = write_wrapper_track_file_atomic(&repo_root, &wrapper_dir) {
            logger.warn(&format!("Failed to write wrapper tracking file: {e}"));
        }
    }

    // Check if hooks exist (any Ralph hook present means we're in agent phase).
    let hooks_present = super::repo::get_hooks_dir_from(&repo_root)
        .ok()
        .is_some_and(|hooks_dir| {
            super::hooks::RALPH_HOOK_NAMES.iter().any(|name| {
                let path = hooks_dir.join(name);
                path.exists()
                    && matches!(
                        crate::files::file_contains_marker(&path, super::hooks::HOOK_MARKER),
                        Ok(true)
                    )
            })
        });

    // Missing protections before an agent spawn is treated as tampering.
    if !marker_exists && !hooks_present {
        logger.warn("Agent-phase git protections missing — reinstalling");
        result.tampering_detected = true;
        result
            .details
            .push("Marker and hooks missing before agent spawn — reinstalling".to_string());
    }

    // Repair marker if missing or replaced with a symlink.
    if marker_is_symlink {
        logger.warn("Enforcement marker is a symlink — removing and recreating");
        let _ = fs::remove_file(&marker_path);
        result.tampering_detected = true;
        result
            .details
            .push("Enforcement marker was a symlink — removed".to_string());
    }
    if !marker_exists {
        logger.warn("Enforcement marker missing — recreating");
        if let Err(e) = create_marker_in_repo_root(&repo_root) {
            logger.warn(&format!("Failed to recreate enforcement marker: {e}"));
        } else {
            #[cfg(unix)]
            set_readonly_mode_if_not_symlink(&marker_path, 0o444);
        }
        result.tampering_detected = true;
        result
            .details
            .push("Enforcement marker was missing — recreated".to_string());
    }

    // Verify/restore marker permissions (read-only 0o444).
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if marker_is_symlink {
            // Never chmod through a symlink.
        } else if let Ok(meta) = fs::metadata(&marker_path) {
            if meta.is_file() {
                let mode = meta.permissions().mode() & 0o777;
                if mode != 0o444 {
                    logger.warn(&format!(
                        "Enforcement marker permissions loosened ({mode:#o}) — restoring to 0o444"
                    ));
                    let mut perms = meta.permissions();
                    perms.set_mode(0o444);
                    let _ = fs::set_permissions(&marker_path, perms);
                    result.tampering_detected = true;
                    result.details.push(format!(
                        "Enforcement marker permissions loosened ({mode:#o}) — restored to 0o444"
                    ));
                }
            } else {
                // A non-file marker path would bypass hook/wrapper `-f` checks.
                // Quarantine and recreate a file marker.
                logger.warn("Enforcement marker is not a regular file — quarantining");
                result.tampering_detected = true;
                result
                    .details
                    .push("Enforcement marker was not a regular file — quarantined".to_string());
                if let Err(e) = super::repo::quarantine_path_in_place(&marker_path, "marker-perms")
                {
                    logger.warn(&format!("Failed to quarantine marker path: {e}"));
                } else if let Err(e) = create_marker_in_repo_root(&repo_root) {
                    logger.warn(&format!(
                        "Failed to recreate enforcement marker after quarantine: {e}"
                    ));
                } else {
                    #[cfg(unix)]
                    set_readonly_mode_if_not_symlink(&marker_path, 0o444);
                }
            }
        }
    }

    // Reinstall hooks if tampered (best-effort).
    match reinstall_hooks_if_tampered(logger) {
        Ok(true) => {
            result.tampering_detected = true;
            result
                .details
                .push("Git hooks tampered with or missing — reinstalled".to_string());
        }
        Err(e) => {
            logger.warn(&format!("Failed to verify/reinstall hooks: {e}"));
        }
        Ok(false) => {}
    }

    // Verify/restore hook permissions (read-only executable 0o555).
    #[cfg(unix)]
    super::hooks::enforce_hook_permissions(&repo_root, logger);

    // Verify/restore track file permissions (read-only 0o444).
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if matches!(fs::symlink_metadata(&track_file_path), Ok(m) if m.file_type().is_symlink()) {
            logger.warn("Track file path is a symlink — refusing to chmod and attempting repair");
            result.tampering_detected = true;
            result
                .details
                .push("Track file was a symlink — refused chmod".to_string());
            let _ = fs::remove_file(&track_file_path);
            if let Some(dir) =
                find_wrapper_dir_on_path().filter(|p| wrapper_dir_is_safe_existing_dir(p))
            {
                let _ = write_wrapper_track_file_atomic(&repo_root, &dir);
            }
        } else if let Ok(meta) = fs::metadata(&track_file_path) {
            if meta.is_dir() {
                logger.warn("Track file path is a directory — quarantining");
                result.tampering_detected = true;
                result
                    .details
                    .push("Track file was a directory — quarantined".to_string());
                if let Err(e) =
                    super::repo::quarantine_path_in_place(&track_file_path, "track-perms")
                {
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
                    let _ = fs::set_permissions(&track_file_path, perms);
                    result.tampering_detected = true;
                    result.details.push(format!(
                        "Track file permissions loosened ({mode:#o}) — restored to 0o444"
                    ));
                }
            }
        }
    }

    // Detect unauthorized commits by comparing HEAD OID against baseline.
    if detect_unauthorized_commit(&repo_root) {
        logger.warn("CRITICAL: HEAD OID changed — unauthorized commit detected!");
        result.tampering_detected = true;
        result
            .details
            .push("HEAD OID changed since last check — unauthorized commit detected".to_string());
        // Update stored OID to current HEAD so Ralph's own subsequent commits
        // don't trigger false positives.
        capture_head_oid(&repo_root);
    }

    result
}

/// Remove the git wrapper temp directory using an explicit Ralph metadata dir.
fn cleanup_git_wrapper_dir_silent_at(ralph_dir: &Path) {
    let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);

    // Read the track file to find wrapper dir (best-effort).
    // Do not gate on sanitize — we want to clean up even if the ralph dir
    // has unexpected metadata (sanitize failure should not block cleanup).
    if let Some(wrapper_dir) = fs::read_to_string(&track_file)
        .ok()
        .map(|s| PathBuf::from(s.trim()))
    {
        // Treat track file as untrusted; only remove plausible wrapper dirs under temp.
        remove_wrapper_dir_and_path_entry(&wrapper_dir);
    }

    // ALWAYS remove the track file. Hooks check marker OR track_file with ||
    // logic, so a surviving track file blocks commits even after the marker is
    // removed. The wrapper dir in /tmp is harmless and will be cleaned by the OS.
    #[cfg(unix)]
    add_owner_write_if_not_symlink(&track_file);
    let _ = fs::remove_file(&track_file);
}

/// Clean up a prior wrapper dir tracked in the track file.
///
/// This prevents /tmp leaks when a prior run was SIGKILL'd and the
/// track file still points to an orphaned wrapper dir. It also removes
/// stale PATH entries for the old wrapper dir.
fn cleanup_prior_wrapper_from_track_file(repo_root: &Path) {
    let ralph_dir = super::repo::ralph_git_dir(repo_root);
    let Ok(ralph_dir_exists) = super::repo::sanitize_ralph_git_dir_at(&ralph_dir) else {
        return;
    };
    if !ralph_dir_exists {
        return;
    }

    let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
    let Ok(content) = fs::read_to_string(&track_file) else {
        return;
    };
    let wrapper_dir = PathBuf::from(content.trim());
    if remove_wrapper_dir_and_path_entry(&wrapper_dir) {
        let _ = fs::remove_file(&track_file);
    }
}

/// Clean up orphaned wrapper temp dir from a prior crashed run.
///
/// This is the public entry point for startup-time cleanup in
/// `prepare_agent_phase`. It delegates to `cleanup_prior_wrapper_from_track_file`.
pub fn cleanup_orphaned_wrapper_at(repo_root: &Path) {
    cleanup_prior_wrapper_from_track_file(repo_root);
}

/// Verify that the wrapper temp dir and track file have been cleaned up.
///
/// Returns a list of remaining artifacts for diagnostic purposes.
/// An empty list means cleanup was successful.
#[must_use]
pub fn verify_wrapper_cleaned(repo_root: &Path) -> Vec<String> {
    let mut remaining = Vec::new();
    let track_file = super::repo::ralph_git_dir(repo_root).join(WRAPPER_TRACK_FILE_NAME);
    if track_file.exists() {
        remaining.push(format!("track file still exists: {}", track_file.display()));
        // Also check if the tracked dir still exists.
        if let Ok(content) = fs::read_to_string(&track_file) {
            let dir = PathBuf::from(content.trim());
            if dir.exists() {
                remaining.push(format!("wrapper temp dir still exists: {}", dir.display()));
            }
        }
    }
    remaining
}

/// Best-effort cleanup for unexpected exits (Ctrl+C, early-return, panics).
///
/// Prefers the stored repo root (set during `start_agent_phase`) over CWD-based
/// discovery. Falls back to CWD-based `get_repo_root()` if the stored root is
/// unavailable.
pub fn cleanup_agent_phase_silent() {
    // Prefer stored repo root over CWD-based discovery.
    // Use try_lock to avoid deadlock if the main thread holds the lock
    // when SIGINT arrives.
    let repo_root = AGENT_PHASE_REPO_ROOT
        .try_lock()
        .ok()
        .and_then(|guard| guard.clone())
        .or_else(|| crate::git_helpers::get_repo_root().ok());

    let Some(repo_root) = repo_root else {
        return;
    };

    let stored_ralph_dir = AGENT_PHASE_RALPH_DIR
        .try_lock()
        .ok()
        .and_then(|guard| guard.clone());
    let stored_hooks_dir = AGENT_PHASE_HOOKS_DIR
        .try_lock()
        .ok()
        .and_then(|guard| guard.clone());
    cleanup_agent_phase_silent_at_internal(
        &repo_root,
        stored_ralph_dir.as_deref(),
        stored_hooks_dir.as_deref(),
    );
}

/// Best-effort cleanup using an explicit repo root.
///
/// This is the consolidated cleanup function that removes all agent-phase
/// artifacts. All sub-operations use the provided repo root instead of
/// CWD-based discovery, ensuring reliability even if CWD has changed.
///
/// Unlike [`cleanup_agent_phase_silent`], this function does NOT read the
/// process-global hooks dir mutex. It derives the hooks directory from the
/// repo root, making it safe to call from parallel tests without global
/// state interference.
pub fn cleanup_agent_phase_silent_at(repo_root: &Path) {
    cleanup_agent_phase_silent_at_internal(repo_root, None, None);
}

#[cfg(any(test, feature = "test-utils"))]
pub fn set_agent_phase_paths_for_test(
    repo_root: Option<PathBuf>,
    ralph_dir: Option<PathBuf>,
    hooks_dir: Option<PathBuf>,
) {
    if let Ok(mut guard) = AGENT_PHASE_REPO_ROOT.lock() {
        *guard = repo_root;
    }
    if let Ok(mut guard) = AGENT_PHASE_RALPH_DIR.lock() {
        *guard = ralph_dir;
    }
    if let Ok(mut guard) = AGENT_PHASE_HOOKS_DIR.lock() {
        *guard = hooks_dir;
    }
}

#[cfg(any(test, feature = "test-utils"))]
#[must_use]
pub fn get_agent_phase_paths_for_test() -> (Option<PathBuf>, Option<PathBuf>, Option<PathBuf>) {
    let repo_root = AGENT_PHASE_REPO_ROOT
        .lock()
        .ok()
        .and_then(|guard| guard.clone());
    let ralph_dir = AGENT_PHASE_RALPH_DIR
        .lock()
        .ok()
        .and_then(|guard| guard.clone());
    let hooks_dir = AGENT_PHASE_HOOKS_DIR
        .lock()
        .ok()
        .and_then(|guard| guard.clone());
    (repo_root, ralph_dir, hooks_dir)
}

#[cfg(any(test, feature = "test-utils"))]
#[must_use]
pub fn agent_phase_test_lock() -> &'static Mutex<()> {
    static TEST_LOCK: Mutex<()> = Mutex::new(());
    &TEST_LOCK
}

fn cleanup_agent_phase_silent_at_internal(
    repo_root: &Path,
    stored_ralph_dir: Option<&Path>,
    stored_hooks_dir: Option<&Path>,
) {
    let computed_ralph_dir;
    let ralph_dir = if let Some(ralph_dir) = stored_ralph_dir {
        ralph_dir
    } else {
        computed_ralph_dir = super::repo::ralph_git_dir(repo_root);
        &computed_ralph_dir
    };
    let resolved_hooks_dir = stored_hooks_dir.map(PathBuf::from).or_else(|| {
        super::repo::resolve_protection_scope_from(repo_root)
            .ok()
            .map(|scope| scope.hooks_dir)
    });

    end_agent_phase_in_repo_at_ralph_dir(repo_root, ralph_dir);
    cleanup_git_wrapper_dir_silent_at(ralph_dir);

    // Prefer repo-aware cleanup when discovery still works so worktree config
    // overrides and shared worktreeConfig state are restored alongside hooks.
    if super::repo::resolve_protection_scope_from(repo_root).is_ok() {
        super::hooks::uninstall_hooks_silent_at(repo_root);
    } else if let Some(hooks_dir) = resolved_hooks_dir.as_deref() {
        super::hooks::uninstall_hooks_silent_in_hooks_dir(hooks_dir);
    } else {
        uninstall_hooks_silent_at(repo_root);
    }

    // Clean up any stray tmp files not yet removed before attempting remove_dir.
    // This handles .git-wrapper-dir.tmp.* files that were not present during
    // the earlier end_agent_phase_in_repo_at_ralph_dir call.
    cleanup_hook_scoping_state_files(ralph_dir);
    remove_scoped_hooks_dir_if_empty(ralph_dir);
    cleanup_stray_tmp_files_in_ralph_dir(ralph_dir);
    // Best-effort: remove the ralph dir itself now that all artifacts are gone.
    // end_agent_phase_in_repo_at_ralph_dir removed marker + head-oid and tried
    // remove_dir too early (track file was still present). Now the track file has
    // been cleaned by cleanup_git_wrapper_dir_silent_at, so the dir is empty.
    remove_ralph_dir_best_effort(ralph_dir);

    cleanup_generated_files_silent_at(repo_root);
    cleanup_repo_root_ralph_dir_if_empty(repo_root);

    clear_agent_phase_global_state();
}

/// Best-effort removal of the ralph git directory after all artifacts are cleaned.
///
/// Called after [`end_agent_phase_in_repo`] and [`disable_git_wrapper`] have removed
/// all files from `.git/ralph/`. The directory should be empty at this point; this
/// call removes it so no empty directory is left behind.
///
/// Returns `true` when `.git/ralph` no longer exists after cleanup. Uses
/// [`fs::remove_dir`] (not `remove_dir_all`) — if the directory is non-empty
/// for any reason (e.g., a quarantine file from tamper detection), the call
/// leaves it in place for inspection and returns `false`.
#[must_use]
pub fn try_remove_ralph_dir(repo_root: &Path) -> bool {
    let ralph_dir = super::repo::ralph_git_dir(repo_root);
    let Ok(ralph_dir_exists) = super::repo::sanitize_ralph_git_dir_at(&ralph_dir) else {
        return !ralph_dir.exists();
    };
    if !ralph_dir_exists {
        return true;
    }

    // Clean up stray temp files from interrupted atomic writes before attempting
    // remove_dir. Without this, remove_dir silently fails and the directory is
    // left behind across restarts.
    cleanup_stray_tmp_files_in_sanitized_ralph_dir(&ralph_dir);
    remove_scoped_hooks_dir_if_empty(&ralph_dir);
    match fs::remove_dir(&ralph_dir) {
        Ok(()) => true,
        Err(err) if err.kind() == io::ErrorKind::NotFound => true,
        Err(_) => !ralph_dir.exists(),
    }
}

/// Verify that the Ralph metadata dir itself has been removed.
///
/// Returns a list of remaining artifacts for diagnostic purposes.
/// An empty list means cleanup was successful.
#[must_use]
pub fn verify_ralph_dir_removed(repo_root: &Path) -> Vec<String> {
    let ralph_dir = super::repo::ralph_git_dir(repo_root);
    let Ok(ralph_dir_exists) = super::repo::sanitize_ralph_git_dir_at(&ralph_dir) else {
        return vec![format!(
            "could not sanitize ralph directory before verification: {}",
            ralph_dir.display()
        )];
    };
    if !ralph_dir_exists {
        return Vec::new();
    }

    let mut remaining = vec![format!("directory still exists: {}", ralph_dir.display())];
    match fs::read_dir(&ralph_dir) {
        Ok(entries) => {
            let mut names = entries
                .filter_map(Result::ok)
                .map(|entry| entry.file_name().to_string_lossy().into_owned())
                .collect::<Vec<_>>();
            names.sort();
            if !names.is_empty() {
                remaining.push(format!("remaining entries: {}", names.join(", ")));
            }
        }
        Err(err) => remaining.push(format!("could not inspect directory contents: {err}")),
    }
    remaining
}

/// Remove generated files silently using an explicit repo root.
fn cleanup_generated_files_silent_at(repo_root: &Path) {
    for file in crate::files::io::agent_files::GENERATED_FILES {
        let absolute_path = repo_root.join(file);
        let _ = std::fs::remove_file(absolute_path);
    }
}

fn cleanup_hook_scoping_state_files(ralph_dir: &Path) {
    for file_name in ["hooks-path.previous", "worktree-config.previous"] {
        let path = ralph_dir.join(file_name);
        #[cfg(unix)]
        add_owner_write_if_not_symlink(&path);
        let _ = fs::remove_file(path);
    }
}

fn remove_scoped_hooks_dir_if_empty(ralph_dir: &Path) {
    let _ = fs::remove_dir(ralph_dir.join("hooks"));
}

fn cleanup_repo_root_ralph_dir_if_empty(repo_root: &Path) {
    let fallback_ralph_dir = repo_root.join(".git/ralph");
    cleanup_hook_scoping_state_files(&fallback_ralph_dir);
    remove_scoped_hooks_dir_if_empty(&fallback_ralph_dir);
    cleanup_stray_tmp_files_in_ralph_dir(&fallback_ralph_dir);
    remove_ralph_dir_best_effort(&fallback_ralph_dir);
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

/// Clean up orphaned enforcement marker.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn cleanup_orphaned_marker(logger: &Logger) -> io::Result<()> {
    let repo_root = get_repo_root()?;
    let legacy_marker = legacy_marker_path(&repo_root);
    if fs::symlink_metadata(&legacy_marker).is_ok() {
        #[cfg(unix)]
        {
            add_owner_write_if_not_symlink(&legacy_marker);
        }
        fs::remove_file(&legacy_marker)?;
        logger.success("Removed orphaned enforcement marker");
        return Ok(());
    }

    let ralph_dir = super::repo::ralph_git_dir(&repo_root);
    if !super::repo::sanitize_ralph_git_dir_at(&ralph_dir)? {
        logger.info("No orphaned marker found");
        return Ok(());
    }
    let marker_path = ralph_dir.join(MARKER_FILE_NAME);

    if fs::symlink_metadata(&marker_path).is_ok() {
        // Make writable before removal (marker is created as read-only 0o444).
        #[cfg(unix)]
        {
            add_owner_write_if_not_symlink(&marker_path);
        }
        fs::remove_file(&marker_path)?;
        logger.success("Removed orphaned enforcement marker");
    } else {
        logger.info("No orphaned marker found");
    }

    Ok(())
}

/// Capture the current HEAD OID and write it to `<git-dir>/ralph/head-oid.txt`.
///
/// This is called at agent-phase start and after each Ralph-orchestrated commit
/// to establish the baseline for unauthorized commit detection.
pub fn capture_head_oid(repo_root: &Path) {
    let Ok(head_oid) = crate::git_helpers::get_current_head_oid_at(repo_root) else {
        return; // No HEAD yet (empty repo) — nothing to capture
    };
    let _ = write_head_oid_file_atomic(repo_root, head_oid.trim());
}

fn write_head_oid_file_atomic(repo_root: &Path, oid: &str) -> io::Result<()> {
    let ralph_dir = super::repo::ensure_ralph_git_dir(repo_root)?;

    let head_oid_path = ralph_dir.join(HEAD_OID_FILE_NAME);
    if matches!(fs::symlink_metadata(&head_oid_path), Ok(m) if m.file_type().is_symlink()) {
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

    // Rename is symlink-safe: it replaces the directory entry.
    #[cfg(windows)]
    {
        if head_oid_path.exists() {
            let _ = fs::remove_file(&head_oid_path);
        }
    }
    fs::rename(&tmp_path, &head_oid_path)
}

/// Detect unauthorized commits by comparing current HEAD against stored OID.
///
/// Returns `true` if HEAD has changed (indicating an unauthorized commit),
/// `false` if HEAD matches or comparison is not possible.
#[must_use]
pub fn detect_unauthorized_commit(repo_root: &Path) -> bool {
    let head_oid_path = super::repo::ralph_git_dir(repo_root).join(HEAD_OID_FILE_NAME);
    if matches!(fs::symlink_metadata(&head_oid_path), Ok(m) if m.file_type().is_symlink()) {
        return false;
    }
    let Ok(stored_oid) = fs::read_to_string(&head_oid_path) else {
        return false; // No stored OID — cannot compare
    };
    let stored_oid = stored_oid.trim();
    if stored_oid.is_empty() {
        return false;
    }

    let Ok(current_oid) = crate::git_helpers::get_current_head_oid_at(repo_root) else {
        return false; // Cannot determine current HEAD
    };

    current_oid.trim() != stored_oid
}

/// Remove stray atomic-write temp files from the ralph git directory.
///
/// `write_head_oid_file_atomic` and `write_wrapper_track_file_atomic` create
/// temp files (`.head-oid.tmp.PID.NANOS` and `.git-wrapper-dir.tmp.PID.NANOS`)
/// and rename them atomically. If the process is killed or the rename fails the
/// temp file is left behind and blocks [`fs::remove_dir`] from removing the
/// directory.
///
/// Only files whose names start with the known temp prefixes are removed.
/// Quarantine files (`*.ralph.tampered.*`) and other unexpected files are
/// intentionally left in place.
fn cleanup_stray_tmp_files_in_ralph_dir(ralph_dir: &Path) {
    let Ok(ralph_dir_exists) = super::repo::sanitize_ralph_git_dir_at(ralph_dir) else {
        return;
    };
    if !ralph_dir_exists {
        return;
    }

    cleanup_stray_tmp_files_in_sanitized_ralph_dir(ralph_dir);
}

fn cleanup_stray_tmp_files_in_sanitized_ralph_dir(ralph_dir: &Path) {
    let Ok(entries) = fs::read_dir(ralph_dir) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        if name_str.starts_with(".head-oid.tmp.") || name_str.starts_with(".git-wrapper-dir.tmp.") {
            let path = entry.path();
            let Ok(meta) = fs::symlink_metadata(&path) else {
                continue;
            };
            let file_type = meta.file_type();
            if !file_type.is_file() || file_type.is_symlink() {
                continue;
            }

            // Make writable before removal; temp files may have been set read-only
            // by write_head_oid_file_atomic (0o444 / readonly) or
            // write_wrapper_track_file_atomic (0o444 / readonly).
            relax_temp_cleanup_permissions_if_regular_file(&path);
            let _ = fs::remove_file(&path);
        }
    }
}

/// Remove the head-oid tracking file, making it writable first if needed.
fn remove_head_oid_file_at(ralph_dir: &Path) {
    let head_oid_path = ralph_dir.join(HEAD_OID_FILE_NAME);
    if fs::symlink_metadata(&head_oid_path).is_err() {
        return;
    }
    #[cfg(unix)]
    {
        add_owner_write_if_not_symlink(&head_oid_path);
    }
    let _ = fs::remove_file(&head_oid_path);
}

// ============================================================================
// Workspace-aware variants
// ============================================================================

/// Relative path used by workspace-aware marker functions.
///
/// The marker lives at `<git-dir>/ralph/no_agent_commit` on the real filesystem.
/// The workspace abstraction represents this as a relative path so `MemoryWorkspace`
/// can test the create/remove/exists operations without a real git repository.
const MARKER_WORKSPACE_PATH: &str = ".git/ralph/no_agent_commit";
const LEGACY_MARKER_WORKSPACE_PATH: &str = ".no_agent_commit";

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
    workspace.write(Path::new(MARKER_WORKSPACE_PATH), "")
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
    workspace.remove_if_exists(Path::new(MARKER_WORKSPACE_PATH))
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
    workspace.exists(Path::new(MARKER_WORKSPACE_PATH))
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
    let marker_path = Path::new(MARKER_WORKSPACE_PATH);
    let legacy_marker_path = Path::new(LEGACY_MARKER_WORKSPACE_PATH);
    let removed_marker = if workspace.exists(marker_path) {
        workspace.remove(marker_path)?;
        true
    } else {
        false
    };
    let removed_legacy_marker = if workspace.exists(legacy_marker_path) {
        workspace.remove(legacy_marker_path)?;
        true
    } else {
        false
    };

    if removed_marker || removed_legacy_marker {
        logger.success("Removed orphaned enforcement marker");
    } else {
        logger.info("No orphaned marker found");
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    struct RestoreEnv {
        original_cwd: PathBuf,
        original_path: String,
    }

    impl Drop for RestoreEnv {
        fn drop(&mut self) {
            let _ = std::env::set_current_dir(&self.original_cwd);
            std::env::set_var("PATH", &self.original_path);
        }
    }

    fn test_wrapper_content() -> String {
        make_wrapper_content(
            "git",
            "/tmp/.git/ralph/no_agent_commit",
            "/tmp/.git/ralph/git-wrapper-dir.txt",
            "/tmp/repo",
            "/tmp/repo/.git",
        )
    }

    #[test]
    fn test_wrapper_script_handles_c_flag_before_subcommand() {
        // Verify the wrapper script iterates through arguments to skip global flags
        // like `-C /path`, `--git-dir=.git`, etc. before identifying the subcommand.
        // This ensures `git -C /path commit` is correctly blocked, not just `git commit`.
        let content = test_wrapper_content();

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
        let workspace = MemoryWorkspace::new_test().with_file(".no_agent_commit", "");
        let logger = Logger::new(crate::logger::Colors { enabled: false });

        // Create an orphaned marker
        create_marker_with_workspace(&workspace).unwrap();
        assert!(marker_exists_with_workspace(&workspace));
        assert!(workspace.exists(Path::new(".no_agent_commit")));

        // Clean up should remove it
        cleanup_orphaned_marker_with_workspace(&workspace, &logger).unwrap();
        assert!(!marker_exists_with_workspace(&workspace));
        assert!(!workspace.exists(Path::new(".no_agent_commit")));
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
        assert_eq!(MARKER_FILE_NAME, "no_agent_commit");
        assert_eq!(WRAPPER_TRACK_FILE_NAME, "git-wrapper-dir.txt");
        assert_eq!(HEAD_OID_FILE_NAME, "head-oid.txt");
    }

    #[test]
    fn test_wrapper_script_handles_config_flag_before_subcommand() {
        // Verify the wrapper script handles --config (git 2.46+ alias for -c)
        // as a flag that takes a value argument, so that
        // `git --config core.hooksPath=/dev/null commit` correctly identifies
        // "commit" as the subcommand (by skipping the --config value argument).
        let content = test_wrapper_content();

        assert!(
            content.contains("--config|"),
            "wrapper must recognize --config as a flag with separate value; got:\n{content}"
        );
        assert!(
            content.contains("--config=*"),
            "wrapper must handle --config=value syntax; got:\n{content}"
        );
    }

    #[test]
    fn test_wrapper_script_enforces_read_only_allowlist() {
        let content = test_wrapper_content();

        assert!(
            content.contains("read-only allowlist"),
            "wrapper should describe allowlist behavior; got:\n{content}"
        );
        assert!(
            content.contains("status|log|diff|show|rev-parse|ls-files|describe"),
            "wrapper should explicitly allow read-only lookup commands; got:\n{content}"
        );
    }

    #[test]
    fn test_wrapper_script_blocks_stash_except_list() {
        let content = test_wrapper_content();
        assert!(
            content.contains("only 'stash list' allowed"),
            "wrapper should only allow stash list; got:\n{content}"
        );
    }

    #[test]
    fn test_wrapper_script_blocks_branch_positional_args() {
        let content = test_wrapper_content();
        assert!(
            content.contains("git branch disabled during agent phase"),
            "wrapper should block positional git branch invocations via the branch allowlist; got:\n{content}"
        );
    }

    #[test]
    fn test_wrapper_script_blocks_flag_only_mutating_branch_forms() {
        let content = test_wrapper_content();
        assert!(
            content.contains("--unset-upstream"),
            "wrapper branch allowlist should explicitly reject mutating flag-only forms; got:\n{content}"
        );
    }

    #[test]
    fn test_wrapper_script_uses_absolute_marker_paths() {
        let content = test_wrapper_content();
        // Wrapper now embeds absolute paths to marker and track file, not a repo root variable.
        assert!(
            !content.contains("protected_repo_root"),
            "wrapper should not use protected_repo_root variable; got:\n{content}"
        );
        assert!(
            content.contains("marker='/tmp/.git/ralph/no_agent_commit'"),
            "wrapper should embed absolute marker path; got:\n{content}"
        );
        assert!(
            content.contains("track_file='/tmp/.git/ralph/git-wrapper-dir.txt'"),
            "wrapper should embed absolute track file path; got:\n{content}"
        );
    }

    #[test]
    fn test_protection_check_result_default_is_no_tampering() {
        let result = ProtectionCheckResult::default();
        assert!(!result.tampering_detected);
        assert!(result.details.is_empty());
    }

    #[test]
    fn test_wrapper_script_unsets_git_env_vars() {
        let content = test_wrapper_content();
        // Wrapper must unset GIT_DIR, GIT_WORK_TREE, and GIT_EXEC_PATH
        // when agent-phase protections are active to prevent env var bypass.
        for var in &["GIT_DIR", "GIT_WORK_TREE", "GIT_EXEC_PATH"] {
            assert!(
                content.contains(&format!("unset {var}")),
                "wrapper must unset {var} when marker exists; got:\n{content}"
            );
        }
    }

    #[test]
    fn test_wrapper_script_documents_command_builtin_behavior() {
        let content = test_wrapper_content();
        // Wrapper should document that `command git` still routes through
        // the PATH wrapper (command only skips shell functions/aliases, not PATH entries).
        assert!(
            content.contains("command") && content.contains("PATH"),
            "wrapper must document that `command` builtin still routes through PATH wrapper; got:\n{content}"
        );
    }

    // =========================================================================
    // HEAD OID comparison tests
    // =========================================================================

    #[test]
    fn test_detect_unauthorized_commit_no_stored_oid() {
        // When no head-oid.txt exists, detection should return false (no panic).
        let tmp = tempfile::tempdir().unwrap();
        assert!(!detect_unauthorized_commit(tmp.path()));
    }

    #[test]
    fn test_detect_unauthorized_commit_empty_stored_oid() {
        let tmp = tempfile::tempdir().unwrap();
        // Head OID now lives in <git-dir>/ralph/ (fallback: .git/ralph/ for plain temp dirs)
        let ralph_dir = tmp.path().join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        fs::write(ralph_dir.join("head-oid.txt"), "").unwrap();
        assert!(!detect_unauthorized_commit(tmp.path()));
    }

    #[test]
    fn test_write_wrapper_track_file_atomic_repairs_directory_tamper() {
        // If the wrapper track file path exists as a directory, treat it as tampering.
        // The wrapper must recover (quarantine/remove the directory) and write a real file.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();

        // Track file now lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        // Create a directory at the track file path.
        let track_dir_path = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::create_dir_all(&track_dir_path).unwrap();
        fs::write(track_dir_path.join("payload.txt"), "do not delete").unwrap();

        let wrapper_dir = repo_root.join("some-wrapper-dir");
        fs::create_dir_all(&wrapper_dir).unwrap();

        write_wrapper_track_file_atomic(repo_root, &wrapper_dir).unwrap();

        let track_file_path = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        let meta = fs::metadata(&track_file_path).unwrap();
        assert!(meta.is_file(), "track file path should be a file");
        let content = fs::read_to_string(&track_file_path).unwrap();
        assert!(
            content.contains(&wrapper_dir.display().to_string()),
            "track file should contain wrapper dir path; got: {content}"
        );

        // Quarantine should preserve prior directory contents by renaming in-place.
        let quarantined = fs::read_dir(&ralph_dir)
            .unwrap()
            .filter_map(Result::ok)
            .any(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with("git-wrapper-dir.txt.ralph.tampered.track")
            });
        assert!(
            quarantined,
            "expected quarantined track dir entry in .git/ralph/"
        );
    }

    #[test]
    fn test_repair_marker_path_converts_directory_to_regular_file() {
        // If the marker path exists as a directory, treat it as tampering and
        // recover by quarantining it and creating a regular file marker.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();

        // Marker now lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        let marker_path = ralph_dir.join(MARKER_FILE_NAME);
        fs::create_dir_all(&marker_path).unwrap();

        // Function under test: must quarantine the directory and create a file marker.
        repair_marker_path_if_tampered(repo_root).unwrap();

        let meta = fs::metadata(&marker_path).unwrap();
        assert!(meta.is_file(), "marker path should be a regular file");

        let quarantined = fs::read_dir(&ralph_dir)
            .unwrap()
            .filter_map(Result::ok)
            .any(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with("no_agent_commit.ralph.tampered.marker")
            });
        assert!(
            quarantined,
            "expected quarantined marker dir entry in .git/ralph/"
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_create_marker_in_repo_root_quarantines_special_file() {
        use std::os::unix::fs::symlink;
        use std::os::unix::fs::FileTypeExt;
        use std::os::unix::net::UnixListener;

        // If the marker path exists as a special file (e.g., socket/FIFO),
        // we must not treat it as a valid marker. Quarantine/replace it with
        // a regular file so the `-f` checks used by hooks/wrapper cannot be bypassed.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();

        // Marker now lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        let marker_path = ralph_dir.join(MARKER_FILE_NAME);
        let created_socket = match UnixListener::bind(&marker_path) {
            Ok(listener) => {
                drop(listener);
                true
            }
            Err(err) if err.kind() == io::ErrorKind::PermissionDenied => {
                let fallback_target = ralph_dir.join("marker-symlink-target");
                fs::write(&fallback_target, b"blocked special file fallback").unwrap();
                symlink(&fallback_target, &marker_path).unwrap();
                false
            }
            Err(err) => panic!("failed to create non-regular marker path: {err}"),
        };

        let ft = fs::symlink_metadata(&marker_path).unwrap().file_type();
        assert!(
            ft.is_socket() || (!created_socket && ft.is_symlink()),
            "precondition: marker path should be a socket or fallback symlink"
        );

        create_marker_in_repo_root(repo_root).unwrap();

        let meta = fs::symlink_metadata(&marker_path).unwrap();
        assert!(meta.is_file(), "marker path should be a regular file");

        let quarantined = fs::read_dir(&ralph_dir)
            .unwrap()
            .filter_map(Result::ok)
            .any(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with("no_agent_commit.ralph.tampered.marker")
            });
        assert!(
            quarantined,
            "expected quarantined special marker entry in .git/ralph/"
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_ensure_agent_phase_protections_recreates_marker_after_permissions_quarantine() {
        use std::time::Duration;

        let _lock = ENV_LOCK.lock().unwrap();

        let repo_dir = tempfile::tempdir().unwrap();
        let _repo = git2::Repository::init(repo_dir.path()).unwrap();

        let original_cwd = std::env::current_dir().unwrap();
        let original_path = std::env::var("PATH").unwrap_or_default();
        let _restore = RestoreEnv {
            original_cwd,
            original_path: original_path.clone(),
        };

        std::env::set_current_dir(repo_dir.path()).unwrap();

        // Seed a valid marker so marker_exists is computed true.
        // Marker lives in <git-dir>/ralph/ — for a real repo that is .git/ralph/.
        let ralph_dir = repo_dir.path().join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        let marker_path = ralph_dir.join(MARKER_FILE_NAME);
        fs::write(&marker_path, b"").unwrap();
        assert!(fs::symlink_metadata(&marker_path).unwrap().is_file());

        // Provide a plausible wrapper dir on PATH so the protection check does enough work
        // to let us deterministically tamper with the marker after marker_exists is computed.
        let wrapper_dir = tempfile::Builder::new()
            .prefix(WRAPPER_DIR_PREFIX)
            .tempdir_in(std::env::temp_dir())
            .unwrap();
        // Intentionally bloat PATH to slow down the protection check between the
        // marker_exists snapshot and the marker-permissions verification block.
        let slow_paths = (0..1000)
            .map(|i| {
                format!(
                    "{}/ralph-nonexistent-path-{i}",
                    std::env::temp_dir().display()
                )
            })
            .collect::<Vec<_>>()
            .join(":");
        let new_path = format!(
            "{}:{slow_paths}:{original_path}",
            wrapper_dir.path().display()
        );
        std::env::set_var("PATH", new_path);

        // Run the protection check in another thread so this thread can reliably
        // perform the mid-check tampering before the marker-permissions block runs.
        let ensure_thread = std::thread::spawn(|| {
            let logger = Logger::new(crate::logger::Colors { enabled: false });
            ensure_agent_phase_protections(&logger)
        });

        // Ensure the protection check has taken its marker_exists snapshot.
        std::thread::sleep(Duration::from_millis(10));

        let _ = fs::remove_file(&marker_path);
        let _ = fs::remove_dir_all(&marker_path);
        fs::create_dir(&marker_path).unwrap();

        let _result = ensure_thread.join().unwrap();

        // Regression: even if the marker is swapped to a non-file mid-check, the final state
        // must include a regular file marker.
        let meta = fs::symlink_metadata(&marker_path).unwrap();
        assert!(
            meta.is_file(),
            "marker should be recreated as a regular file"
        );
    }

    // =========================================================================
    // cleanup_agent_phase_silent_at tests
    // =========================================================================

    #[test]
    fn test_cleanup_agent_phase_silent_at_removes_marker() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        // Marker lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        let marker = ralph_dir.join(MARKER_FILE_NAME);
        fs::write(&marker, "").unwrap();

        cleanup_agent_phase_silent_at(repo_root);

        assert!(
            !marker.exists(),
            "marker should be removed by cleanup_agent_phase_silent_at"
        );
    }

    #[test]
    fn test_cleanup_agent_phase_silent_at_removes_head_oid() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        // Head OID lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        let head_oid = ralph_dir.join(HEAD_OID_FILE_NAME);
        fs::write(&head_oid, "abc123\n").unwrap();

        cleanup_agent_phase_silent_at(repo_root);

        assert!(
            !head_oid.exists(),
            "head-oid.txt should be removed by cleanup_agent_phase_silent_at"
        );
    }

    #[test]
    fn test_cleanup_agent_phase_silent_at_removes_generated_files() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let agent_dir = repo_root.join(".agent");
        fs::create_dir_all(&agent_dir).unwrap();
        // Enforcement state is now in .git/ralph/ — NOT in working tree.
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        fs::write(ralph_dir.join(MARKER_FILE_NAME), "").unwrap();

        // Working-tree generated files.
        fs::write(agent_dir.join("PLAN.md"), "plan").unwrap();
        fs::write(agent_dir.join("commit-message.txt"), "msg").unwrap();
        fs::write(agent_dir.join("checkpoint.json.tmp"), "{}").unwrap();

        cleanup_agent_phase_silent_at(repo_root);

        // Enforcement-state files removed via end_agent_phase_in_repo.
        assert!(
            !ralph_dir.join(MARKER_FILE_NAME).exists(),
            "marker should be removed by cleanup_agent_phase_silent_at"
        );

        // Working-tree GENERATED_FILES also removed.
        for file in crate::files::io::agent_files::GENERATED_FILES {
            let path = repo_root.join(file);
            assert!(
                !path.exists(),
                "{file} should be removed by cleanup_agent_phase_silent_at"
            );
        }
    }

    #[test]
    fn test_cleanup_agent_phase_silent_at_removes_wrapper_track_file() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        // Track file lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        // Create a track file pointing to a non-existent wrapper dir (safe to clean)
        let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track_file, "/nonexistent/wrapper/dir\n").unwrap();

        cleanup_agent_phase_silent_at(repo_root);

        assert!(
            !track_file.exists(),
            "wrapper track file should be removed by cleanup_agent_phase_silent_at"
        );
    }

    #[test]
    fn test_cleanup_agent_phase_silent_at_idempotent() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();

        // Running on an empty directory should not panic or error
        cleanup_agent_phase_silent_at(repo_root);
        cleanup_agent_phase_silent_at(repo_root);
    }

    // =========================================================================
    // AGENT_PHASE_REPO_ROOT global tests
    // =========================================================================

    #[test]
    fn test_agent_phase_repo_root_mutex_is_accessible() {
        // Verify the global Mutex is lockable (not poisoned or stuck).
        assert!(
            AGENT_PHASE_REPO_ROOT.try_lock().is_ok(),
            "AGENT_PHASE_REPO_ROOT mutex should be lockable"
        );
    }

    // =========================================================================
    // cleanup_prior_wrapper / cleanup_orphaned_wrapper_at tests
    // =========================================================================

    #[test]
    fn test_cleanup_prior_wrapper_removes_tracked_dir() {
        let _lock = ENV_LOCK.lock().unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        // Track file now lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        // Create a fake wrapper dir in temp with the correct prefix.
        let wrapper_dir = tempfile::Builder::new()
            .prefix(WRAPPER_DIR_PREFIX)
            .tempdir()
            .unwrap();
        let wrapper_dir_path = wrapper_dir.keep();

        // Write track file pointing to the wrapper dir.
        let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track_file, format!("{}\n", wrapper_dir_path.display())).unwrap();

        assert!(
            wrapper_dir_path.exists(),
            "precondition: wrapper dir exists"
        );

        cleanup_orphaned_wrapper_at(repo_root);

        assert!(
            !wrapper_dir_path.exists(),
            "wrapper dir should be removed by cleanup"
        );
        assert!(
            !track_file.exists(),
            "track file should be removed by cleanup"
        );
    }

    #[test]
    fn test_cleanup_prior_wrapper_no_track_file() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();

        // No track file exists — cleanup should be a no-op.
        cleanup_orphaned_wrapper_at(repo_root);

        // No panic, no error.
    }

    #[test]
    fn test_cleanup_prior_wrapper_stale_track_file() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        // Track file now lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        // Track file points to a non-existent dir.
        let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track_file, "/nonexistent/ralph-git-wrapper-stale\n").unwrap();

        cleanup_orphaned_wrapper_at(repo_root);

        assert!(
            !track_file.exists(),
            "stale track file should be removed by cleanup"
        );
    }

    // =========================================================================
    // verify_wrapper_cleaned tests
    // =========================================================================

    #[test]
    fn test_verify_wrapper_cleaned_empty_when_clean() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();

        let remaining = verify_wrapper_cleaned(repo_root);
        assert!(
            remaining.is_empty(),
            "verify_wrapper_cleaned should return empty when no artifacts remain"
        );
    }

    #[test]
    fn test_verify_wrapper_cleaned_reports_remaining_track_file() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        // Track file now lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track_file, "/nonexistent/dir\n").unwrap();

        let remaining = verify_wrapper_cleaned(repo_root);
        assert!(
            !remaining.is_empty(),
            "verify_wrapper_cleaned should report remaining track file"
        );
        assert!(
            remaining[0].contains("track file still exists"),
            "should mention track file: {remaining:?}"
        );
    }

    #[test]
    fn test_verify_wrapper_cleaned_reports_remaining_dir() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        // Track file now lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        // Create a real wrapper dir that still exists.
        let wrapper_dir = tempfile::Builder::new()
            .prefix(WRAPPER_DIR_PREFIX)
            .tempdir()
            .unwrap();
        let wrapper_dir_path = wrapper_dir.keep();

        let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track_file, format!("{}\n", wrapper_dir_path.display())).unwrap();

        let remaining = verify_wrapper_cleaned(repo_root);
        assert!(
            remaining.len() >= 2,
            "should report both track file and wrapper dir: {remaining:?}"
        );

        // Clean up the wrapper dir manually.
        let _ = fs::remove_dir_all(&wrapper_dir_path);
    }

    #[cfg(unix)]
    #[test]
    fn test_disable_git_wrapper_removes_track_file_even_when_dir_removal_fails() {
        use std::os::unix::fs::PermissionsExt;

        let _lock = ENV_LOCK.lock().unwrap();
        let repo_root_tmp = tempfile::tempdir().unwrap();
        let repo_root = repo_root_tmp.path();
        // Track file now lives in .git/ralph/ (fallback for plain temp dirs).
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        let blocked_parent = tempfile::tempdir_in(env::temp_dir()).unwrap();
        let wrapper_dir_path = blocked_parent.path().join("ralph-git-wrapper-blocked");
        fs::create_dir(&wrapper_dir_path).unwrap();
        fs::write(wrapper_dir_path.join("git"), "#!/bin/sh\n").unwrap();

        let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track_file, format!("{}\n", wrapper_dir_path.display())).unwrap();

        let original_parent_mode = fs::metadata(blocked_parent.path())
            .unwrap()
            .permissions()
            .mode();
        let mut parent_permissions = fs::metadata(blocked_parent.path()).unwrap().permissions();
        parent_permissions.set_mode(0o555);
        fs::set_permissions(blocked_parent.path(), parent_permissions).unwrap();

        let mut helpers = GitHelpers {
            wrapper_dir: Some(wrapper_dir_path.clone()),
            wrapper_repo_root: Some(repo_root.to_path_buf()),
            ..GitHelpers::default()
        };

        disable_git_wrapper(&mut helpers);

        // Track file MUST be removed even when the wrapper dir removal fails.
        // Hooks check marker OR track_file, so leaving it behind blocks commits.
        assert!(
            !track_file.exists(),
            "track file must be removed unconditionally, even when wrapper dir removal fails"
        );

        let mut restore_permissions = fs::metadata(blocked_parent.path()).unwrap().permissions();
        restore_permissions.set_mode(original_parent_mode);
        fs::set_permissions(blocked_parent.path(), restore_permissions).unwrap();
        fs::remove_dir_all(&wrapper_dir_path).unwrap();
    }

    // =========================================================================
    // ralph dir removal tests (TDD: these fail until try_remove_ralph_dir fix lands)
    // =========================================================================

    #[test]
    fn test_cleanup_agent_phase_silent_at_removes_ralph_dir_when_all_artifacts_present() {
        // Simulates active agent phase state: marker, head-oid, and track file all present.
        // After cleanup, all files AND the directory itself must be gone.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        fs::write(ralph_dir.join(MARKER_FILE_NAME), "").unwrap();
        fs::write(ralph_dir.join(HEAD_OID_FILE_NAME), "abc123\n").unwrap();
        // Track file pointing to a non-existent wrapper dir (safe to clean).
        fs::write(
            ralph_dir.join(WRAPPER_TRACK_FILE_NAME),
            "/nonexistent/wrapper\n",
        )
        .unwrap();

        cleanup_agent_phase_silent_at(repo_root);

        assert!(
            !ralph_dir.exists(),
            ".git/ralph/ should be fully removed after cleanup_agent_phase_silent_at; \
             all artifacts were removed but the directory still exists"
        );
    }

    #[test]
    fn test_cleanup_removes_ralph_dir_when_stray_head_oid_tmp_file_exists() {
        // Simulates a crash mid-write_head_oid_file_atomic that left a temp file.
        // cleanup_agent_phase_silent_at must remove the stray temp file and the directory.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        fs::write(ralph_dir.join(MARKER_FILE_NAME), "").unwrap();
        // Stray temp file left by an interrupted write_head_oid_file_atomic.
        let stray = ralph_dir.join(format!(".head-oid.tmp.{}.123456789", std::process::id()));
        fs::write(&stray, "deadbeef\n").unwrap();
        // Make it read-only as the atomic writer does.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&stray).unwrap().permissions();
            perms.set_mode(0o444);
            fs::set_permissions(&stray, perms).unwrap();
        }

        cleanup_agent_phase_silent_at(repo_root);

        assert!(
            !ralph_dir.exists(),
            ".git/ralph/ should be fully removed even when a stray .head-oid.tmp.* file exists"
        );
    }

    #[test]
    fn test_cleanup_removes_ralph_dir_when_stray_wrapper_track_tmp_file_exists() {
        // Simulates a crash mid-write_wrapper_track_file_atomic that left a temp file.
        // cleanup_agent_phase_silent_at must remove the stray temp file and the directory.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        fs::write(ralph_dir.join(MARKER_FILE_NAME), "").unwrap();
        // Stray temp file left by an interrupted write_wrapper_track_file_atomic.
        let stray = ralph_dir.join(format!(
            ".git-wrapper-dir.tmp.{}.987654321",
            std::process::id()
        ));
        fs::write(&stray, "/tmp/some-wrapper-dir\n").unwrap();
        // Make it read-only as the atomic writer does.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&stray).unwrap().permissions();
            perms.set_mode(0o444);
            fs::set_permissions(&stray, perms).unwrap();
        }

        cleanup_agent_phase_silent_at(repo_root);

        assert!(
            !ralph_dir.exists(),
            ".git/ralph/ should be fully removed even when a stray .git-wrapper-dir.tmp.* file exists"
        );
    }

    #[test]
    fn test_try_remove_ralph_dir_removes_dir_containing_only_stray_tmp_files() {
        // try_remove_ralph_dir must handle a directory containing only stray temp files.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        // Stray head-oid temp file only (no other artifacts).
        let stray = ralph_dir.join(".head-oid.tmp.99999.111111111");
        fs::write(&stray, "cafebabe\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&stray).unwrap().permissions();
            perms.set_mode(0o444);
            fs::set_permissions(&stray, perms).unwrap();
        }

        let removed = try_remove_ralph_dir(repo_root);

        assert!(
            removed,
            "try_remove_ralph_dir should report success when the directory is gone"
        );
        assert!(
            !ralph_dir.exists(),
            ".git/ralph/ should be fully removed by try_remove_ralph_dir when only stray tmp files remain"
        );
    }

    #[test]
    fn test_try_remove_ralph_dir_reports_failure_when_unexpected_artifact_remains() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        fs::write(ralph_dir.join("quarantine.bin"), "keep").unwrap();

        let removed = try_remove_ralph_dir(repo_root);

        assert!(
            !removed,
            "try_remove_ralph_dir should report failure when .git/ralph remains on disk"
        );
        let remaining = verify_ralph_dir_removed(repo_root);
        assert!(
            remaining
                .iter()
                .any(|entry| entry.contains("directory still exists")),
            "verification should report that the directory still exists: {remaining:?}"
        );
        assert!(
            remaining
                .iter()
                .any(|entry| entry.contains("quarantine.bin")),
            "verification should report the unexpected artifact that blocked removal: {remaining:?}"
        );
    }

    #[test]
    #[cfg(unix)]
    fn test_try_remove_ralph_dir_quarantines_symlinked_ralph_dir_without_touching_target() {
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let git_dir = repo_root.join(".git");
        fs::create_dir_all(&git_dir).unwrap();

        let outside_dir = repo_root.join("outside-ralph-target");
        fs::create_dir_all(&outside_dir).unwrap();
        let outside_tmp = outside_dir.join(".head-oid.tmp.12345.999");
        fs::write(&outside_tmp, "keep me\n").unwrap();

        let ralph_dir = git_dir.join("ralph");
        symlink(&outside_dir, &ralph_dir).unwrap();

        let removed = try_remove_ralph_dir(repo_root);

        assert!(
            removed,
            "try_remove_ralph_dir should treat a quarantined symlink as cleaned up"
        );
        assert!(
            !ralph_dir.exists(),
            ".git/ralph path should be removed after quarantining the symlink"
        );
        assert!(
            outside_tmp.exists(),
            "cleanup must not follow a symlinked .git/ralph and delete temp-like files in the target directory"
        );

        let quarantined = fs::read_dir(&git_dir)
            .unwrap()
            .filter_map(Result::ok)
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .find(|name| name.starts_with("ralph.ralph.tampered.dir."))
            .unwrap_or_default();
        assert!(
            !quarantined.is_empty(),
            "symlinked .git/ralph should be quarantined for inspection"
        );
    }

    #[test]
    #[cfg(unix)]
    fn test_verify_ralph_dir_removed_quarantines_symlinked_ralph_dir_without_touching_target() {
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let git_dir = repo_root.join(".git");
        fs::create_dir_all(&git_dir).unwrap();

        let outside_dir = repo_root.join("outside-verify-target");
        fs::create_dir_all(&outside_dir).unwrap();
        let outside_tmp = outside_dir.join(".git-wrapper-dir.tmp.12345.999");
        fs::write(&outside_tmp, "keep me too\n").unwrap();

        let ralph_dir = git_dir.join("ralph");
        symlink(&outside_dir, &ralph_dir).unwrap();

        let remaining = verify_ralph_dir_removed(repo_root);

        assert!(
            remaining.is_empty(),
            "verification should report .git/ralph as removed after quarantining the symlink: {remaining:?}"
        );
        assert!(
            !ralph_dir.exists(),
            ".git/ralph path should no longer exist after verification sanitizes it"
        );
        assert!(
            outside_tmp.exists(),
            "verification must not follow a symlinked .git/ralph and inspect/delete the target directory"
        );

        let quarantined = fs::read_dir(&git_dir)
            .unwrap()
            .filter_map(Result::ok)
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .find(|name| name.starts_with("ralph.ralph.tampered.dir."))
            .unwrap_or_default();
        assert!(
            !quarantined.is_empty(),
            "verification should quarantine a symlinked .git/ralph path for inspection"
        );
    }

    #[test]
    #[cfg(unix)]
    fn test_cleanup_stray_tmp_files_in_ralph_dir_ignores_symlink_entries() {
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        let outside_target = repo_root.join("outside-target.txt");
        fs::write(&outside_target, "keep readonly mode untouched\n").unwrap();
        let symlink_path = ralph_dir.join(".head-oid.tmp.99999.222222222");
        symlink(&outside_target, &symlink_path).unwrap();

        cleanup_stray_tmp_files_in_ralph_dir(&ralph_dir);

        assert!(
            symlink_path.exists(),
            "cleanup must skip temp-name symlinks instead of deleting them"
        );
        let target_contents = fs::read_to_string(&outside_target).unwrap();
        assert_eq!(target_contents, "keep readonly mode untouched\n");
    }

    #[test]
    #[cfg(windows)]
    fn test_cleanup_stray_tmp_files_in_ralph_dir_removes_readonly_files_on_windows() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        let stray = ralph_dir.join(".head-oid.tmp.99999.333333333");
        fs::write(&stray, "deadbeef\n").unwrap();
        let mut perms = fs::metadata(&stray).unwrap().permissions();
        perms.set_readonly(true);
        fs::set_permissions(&stray, perms).unwrap();

        cleanup_stray_tmp_files_in_ralph_dir(&ralph_dir);

        assert!(
            !stray.exists(),
            "cleanup must clear the readonly attribute before removing stray temp files on Windows"
        );
    }

    #[test]
    fn test_cleanup_agent_phase_silent_at_removes_ralph_hooks() {
        // Verifies that Ralph-managed hooks are removed when cleanup uses the precomputed
        // hooks dir derived from ralph_dir.parent() (bypasses extra libgit2 discovery).
        use crate::git_helpers::hooks;

        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        // Use a real git repo so libgit2 discovery succeeds for all code paths.
        let _repo = git2::Repository::init(repo_root).unwrap();

        let hooks_dir = repo_root.join(".git").join("hooks");
        fs::create_dir_all(&hooks_dir).unwrap();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        // Install read-only Ralph-managed hooks.
        let hook_content = format!("#!/bin/bash\n# {}\nexit 0\n", hooks::HOOK_MARKER);
        for hook_name in hooks::RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(hook_name);
            fs::write(&hook_path, &hook_content).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let mut perms = fs::metadata(&hook_path).unwrap().permissions();
                perms.set_mode(0o555);
                fs::set_permissions(&hook_path, perms).unwrap();
            }
        }

        cleanup_agent_phase_silent_at(repo_root);

        for hook_name in hooks::RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(hook_name);
            let still_has_marker = hook_path.exists()
                && crate::files::file_contains_marker(&hook_path, hooks::HOOK_MARKER)
                    .unwrap_or(false);
            assert!(
                !still_has_marker,
                "Ralph hook {hook_name} should be removed by cleanup_agent_phase_silent_at"
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn test_cleanup_removes_readonly_marker_and_track_file() {
        use std::os::unix::fs::PermissionsExt;

        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git/ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        // Create read-only marker (0o444).
        let marker = ralph_dir.join(MARKER_FILE_NAME);
        fs::write(&marker, "").unwrap();
        fs::set_permissions(&marker, fs::Permissions::from_mode(0o444)).unwrap();

        // Create read-only track file (0o444).
        let track = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track, "/nonexistent\n").unwrap();
        fs::set_permissions(&track, fs::Permissions::from_mode(0o444)).unwrap();

        end_agent_phase_in_repo_at_ralph_dir(repo_root, &ralph_dir);
        cleanup_git_wrapper_dir_silent_at(&ralph_dir);

        assert!(!marker.exists(), "read-only marker should be removed");
        assert!(!track.exists(), "read-only track file should be removed");
    }

    #[test]
    fn test_cleanup_is_idempotent_called_twice() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git/ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        let marker = ralph_dir.join(MARKER_FILE_NAME);
        fs::write(&marker, "").unwrap();

        // First cleanup.
        cleanup_agent_phase_silent_at(repo_root);
        assert!(!marker.exists());

        // Second cleanup — should not panic or error.
        cleanup_agent_phase_silent_at(repo_root);
        assert!(!marker.exists());
    }

    #[test]
    fn test_cleanup_agent_phase_silent_at_from_worktree_root() {
        let tmp = tempfile::tempdir().unwrap();
        let main_repo = git2::Repository::init(tmp.path()).unwrap();
        {
            let mut index = main_repo.index().unwrap();
            let tree_oid = index.write_tree().unwrap();
            let tree = main_repo.find_tree(tree_oid).unwrap();
            let sig = git2::Signature::now("test", "test@test.com").unwrap();
            main_repo
                .commit(Some("HEAD"), &sig, &sig, "initial", &tree, &[])
                .unwrap();
        }

        let wt_path = tmp.path().join("wt-cleanup");
        let _wt = main_repo.worktree("wt-cleanup", &wt_path, None).unwrap();

        let worktree_scope = crate::git_helpers::resolve_protection_scope_from(&wt_path).unwrap();
        let worktree_ralph_dir = worktree_scope.git_dir.join("ralph");
        crate::git_helpers::hooks::install_hooks_in_repo(&wt_path).unwrap();
        fs::write(worktree_ralph_dir.join(MARKER_FILE_NAME), "").unwrap();
        // Also create a track file pointing to a nonexistent wrapper dir.
        fs::write(
            worktree_ralph_dir.join(WRAPPER_TRACK_FILE_NAME),
            "/nonexistent/wrapper\n",
        )
        .unwrap();

        assert!(
            worktree_scope
                .worktree_config_path
                .as_ref()
                .unwrap()
                .exists(),
            "precondition: linked worktree cleanup test must start with config.worktree present"
        );

        // Cleanup from the WORKTREE root — must clean only worktree-local artifacts.
        cleanup_agent_phase_silent_at(&wt_path);

        assert!(
            !worktree_ralph_dir.join(MARKER_FILE_NAME).exists(),
            "marker at worktree git dir should be removed when cleaning from worktree root"
        );
        assert!(
            !worktree_ralph_dir.join(WRAPPER_TRACK_FILE_NAME).exists(),
            "track file at worktree git dir should be removed when cleaning from worktree root"
        );
        for name in crate::git_helpers::hooks::RALPH_HOOK_NAMES {
            let hook_path = worktree_scope.hooks_dir.join(name);
            let still_ralph = hook_path.exists()
                && crate::files::file_contains_marker(
                    &hook_path,
                    crate::git_helpers::hooks::HOOK_MARKER,
                )
                .unwrap_or(false);
            assert!(
                !still_ralph,
                "hook {name} at worktree hooks dir should be removed from worktree root"
            );
        }

        assert!(
            !worktree_scope
                .worktree_config_path
                .as_ref()
                .unwrap()
                .exists(),
            "silent cleanup should remove the worktree-local config override"
        );
        let common_config = crate::git_helpers::resolve_protection_scope_from(&wt_path)
            .unwrap()
            .common_git_dir
            .join("config");
        assert!(
            common_config.exists(),
            "common config should remain inspectable after cleanup"
        );
        assert_eq!(
            git2::Config::open(&common_config)
                .unwrap()
                .get_string("extensions.worktreeConfig")
                .ok(),
            None,
            "silent cleanup should restore shared worktreeConfig state for a linked worktree run"
        );
    }

    #[test]
    fn test_cleanup_agent_phase_silent_at_from_root_repo_restores_root_worktree_scoping() {
        let tmp = tempfile::tempdir().unwrap();
        let main_repo_path = tmp.path().join("main");
        fs::create_dir_all(&main_repo_path).unwrap();
        let main_repo = git2::Repository::init(&main_repo_path).unwrap();
        {
            let mut index = main_repo.index().unwrap();
            let tree_oid = index.write_tree().unwrap();
            let tree = main_repo.find_tree(tree_oid).unwrap();
            let sig = git2::Signature::now("test", "test@test.com").unwrap();
            main_repo
                .commit(Some("HEAD"), &sig, &sig, "initial", &tree, &[])
                .unwrap();
        }

        let sibling_path = tmp.path().join("wt-sibling");
        let _sibling = main_repo
            .worktree("wt-sibling", &sibling_path, None)
            .unwrap();

        let root_scope =
            crate::git_helpers::resolve_protection_scope_from(&main_repo_path).unwrap();
        let common_config = root_scope.common_git_dir.join("config");
        let root_config = root_scope.worktree_config_path.unwrap();

        crate::git_helpers::hooks::install_hooks_in_repo(&main_repo_path).unwrap();
        assert!(
            root_config.exists(),
            "precondition: root config.worktree must exist"
        );
        assert_eq!(
            git2::Config::open(&common_config)
                .unwrap()
                .get_string("extensions.worktreeConfig")
                .ok(),
            Some("true".to_string()),
            "precondition: root worktree install should enable shared worktreeConfig"
        );

        cleanup_agent_phase_silent_at(&main_repo_path);

        assert!(
            !root_config.exists(),
            "silent cleanup should remove the root worktree config override"
        );
        assert_eq!(
            git2::Config::open(&common_config)
                .unwrap()
                .get_string("extensions.worktreeConfig")
                .ok(),
            None,
            "silent cleanup should restore shared worktreeConfig state for a root run"
        );
    }

    // =========================================================================
    // Unconditional track file removal tests
    // =========================================================================

    #[test]
    fn test_track_file_removed_even_when_wrapper_dir_cleanup_fails() {
        // The track file must be removed unconditionally because hooks use OR logic
        // (marker OR track_file) to block commits. If the wrapper dir in /tmp can't
        // be removed, the track file should still be cleaned so hooks don't block.
        let tmp = tempfile::tempdir().unwrap();
        let ralph_dir = tmp.path().join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        // Write a track file pointing to a path that does NOT exist under system
        // temp dir (so wrapper_dir_is_safe_existing_dir returns false and the
        // wrapper dir cleanup "fails" in the sense that the dir wasn't cleaned).
        let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track_file, "/not-a-real-temp-dir/ralph-git-wrapper-fake\n").unwrap();

        cleanup_git_wrapper_dir_silent_at(&ralph_dir);

        assert!(
            !track_file.exists(),
            "track file must be removed unconditionally, even when wrapper dir cleanup fails"
        );
    }

    #[test]
    fn test_disable_git_wrapper_always_removes_track_file() {
        // disable_git_wrapper must always remove the track file regardless of
        // whether the wrapper dir could be cleaned up.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();

        // Write a read-only track file pointing to a non-existent path.
        let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track_file, "/nonexistent/not-temp-prefixed\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&track_file).unwrap().permissions();
            perms.set_mode(0o444);
            fs::set_permissions(&track_file, perms).unwrap();
        }

        let mut helpers = GitHelpers {
            wrapper_dir: None,
            wrapper_repo_root: Some(repo_root.to_path_buf()),
            ..GitHelpers::default()
        };

        disable_git_wrapper(&mut helpers);

        assert!(
            !track_file.exists(),
            "track file must be removed unconditionally by disable_git_wrapper"
        );
    }

    // =========================================================================
    // Global mutex clearing tests
    // =========================================================================

    #[test]
    fn test_global_mutexes_not_cleared_by_end_agent_phase_in_repo() {
        // end_agent_phase_in_repo must NOT clear global mutexes — callers are
        // responsible for calling clear_agent_phase_global_state after ALL cleanup.
        //
        // IMPORTANT: This test modifies process-global mutexes, so we must:
        // 1. Use ClearOnDrop guard to ensure cleanup even on panic.
        // 2. Only set REPO_ROOT and RALPH_DIR (not HOOKS_DIR) to avoid
        //    poisoning parallel tests that call cleanup_agent_phase_silent_at,
        //    which reads HOOKS_DIR to determine the hooks cleanup location.
        struct ClearOnDrop;
        impl Drop for ClearOnDrop {
            fn drop(&mut self) {
                clear_agent_phase_global_state();
            }
        }
        let _guard = ClearOnDrop;
        let _test_lock = agent_phase_test_lock().lock().unwrap();

        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        fs::write(ralph_dir.join(MARKER_FILE_NAME), "").unwrap();

        // Only set REPO_ROOT and RALPH_DIR. Skip HOOKS_DIR to avoid
        // interfering with parallel tests that read it for cleanup.
        set_agent_phase_paths_for_test(Some(repo_root.to_path_buf()), Some(ralph_dir), None);

        end_agent_phase_in_repo(repo_root);

        // REPO_ROOT and RALPH_DIR should still hold their values.
        let repo_root_val = AGENT_PHASE_REPO_ROOT.lock().unwrap().clone();
        assert!(
            repo_root_val.is_some(),
            "AGENT_PHASE_REPO_ROOT should NOT be cleared by end_agent_phase_in_repo"
        );

        let ralph_dir_val = AGENT_PHASE_RALPH_DIR.lock().unwrap().clone();
        assert!(
            ralph_dir_val.is_some(),
            "AGENT_PHASE_RALPH_DIR should NOT be cleared by end_agent_phase_in_repo"
        );
        // _guard's Drop clears mutexes even on panic.
    }

    #[test]
    fn test_clear_agent_phase_global_state_clears_all_mutexes() {
        set_agent_phase_paths_for_test(
            Some(PathBuf::from("/test/repo")),
            Some(PathBuf::from("/test/repo/.git/ralph")),
            Some(PathBuf::from("/test/repo/.git/hooks")),
        );

        clear_agent_phase_global_state();

        assert!(
            AGENT_PHASE_REPO_ROOT.lock().unwrap().is_none(),
            "AGENT_PHASE_REPO_ROOT should be cleared"
        );
        assert!(
            AGENT_PHASE_RALPH_DIR.lock().unwrap().is_none(),
            "AGENT_PHASE_RALPH_DIR should be cleared"
        );
        assert!(
            AGENT_PHASE_HOOKS_DIR.lock().unwrap().is_none(),
            "AGENT_PHASE_HOOKS_DIR should be cleared"
        );
    }

    // =========================================================================
    // Comprehensive cleanup test
    // =========================================================================

    /// Helper: install all agent-phase artifacts for comprehensive cleanup tests.
    fn install_all_agent_phase_artifacts(repo_root: &Path) {
        use crate::git_helpers::hooks;

        let ralph_dir = repo_root.join(".git").join("ralph");
        fs::create_dir_all(&ralph_dir).unwrap();
        let hooks_dir = repo_root.join(".git").join("hooks");
        fs::create_dir_all(&hooks_dir).unwrap();

        let marker = ralph_dir.join(MARKER_FILE_NAME);
        fs::write(&marker, "").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&marker, fs::Permissions::from_mode(0o444)).unwrap();
        }

        let track_file = ralph_dir.join(WRAPPER_TRACK_FILE_NAME);
        fs::write(&track_file, "/nonexistent/wrapper\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&track_file, fs::Permissions::from_mode(0o444)).unwrap();
        }

        fs::write(ralph_dir.join(HEAD_OID_FILE_NAME), "abc123\n").unwrap();

        let hook_content = format!("#!/bin/bash\n# {}\nexit 0\n", hooks::HOOK_MARKER);
        for name in hooks::RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(name);
            fs::write(&hook_path, &hook_content).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&hook_path, fs::Permissions::from_mode(0o555)).unwrap();
            }
        }

        set_agent_phase_paths_for_test(
            Some(repo_root.to_path_buf()),
            Some(ralph_dir),
            Some(hooks_dir),
        );
    }

    #[test]
    fn test_cleanup_agent_phase_silent_at_removes_all_artifacts_including_track_file() {
        use crate::git_helpers::hooks;

        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let _repo = git2::Repository::init(repo_root).unwrap();

        install_all_agent_phase_artifacts(repo_root);
        cleanup_agent_phase_silent_at(repo_root);

        let ralph_dir = repo_root.join(".git").join("ralph");
        let hooks_dir = repo_root.join(".git").join("hooks");

        assert!(
            !ralph_dir.join(MARKER_FILE_NAME).exists(),
            "marker should be removed"
        );
        assert!(
            !ralph_dir.join(WRAPPER_TRACK_FILE_NAME).exists(),
            "track file should be removed"
        );
        assert!(
            !ralph_dir.join(HEAD_OID_FILE_NAME).exists(),
            "head-oid should be removed"
        );
        assert!(!ralph_dir.exists(), "ralph dir should be removed");
        for name in hooks::RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(name);
            let still_ralph = hook_path.exists()
                && crate::files::file_contains_marker(&hook_path, hooks::HOOK_MARKER)
                    .unwrap_or(false);
            assert!(!still_ralph, "hook {name} should be removed");
        }

        // Global mutexes should be cleared.
        assert!(AGENT_PHASE_REPO_ROOT.lock().unwrap().is_none());
        assert!(AGENT_PHASE_RALPH_DIR.lock().unwrap().is_none());
        assert!(AGENT_PHASE_HOOKS_DIR.lock().unwrap().is_none());
    }
}
