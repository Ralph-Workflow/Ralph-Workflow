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

use super::hooks::{install_hooks, reinstall_hooks_if_tampered, uninstall_hooks_silent_at};
use super::repo::get_repo_root;
use crate::logger::Logger;
use crate::workspace::Workspace;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use which::which;

const WRAPPER_DIR_TRACK_FILE: &str = ".agent/git-wrapper-dir.txt";
const WRAPPER_DIR_PREFIX: &str = "ralph-git-wrapper-";
const WRAPPER_MARKER: &str = "RALPH_AGENT_PHASE_GIT_WRAPPER";
const HEAD_OID_FILE: &str = ".agent/head-oid.txt";

/// Process-global repo root set during `start_agent_phase` for signal handler fallback.
///
/// The signal handler needs a reliable repo root when CWD-based discovery may fail.
/// This is set in `start_agent_phase` and cleared in `end_agent_phase_in_repo`.
/// The signal handler uses `try_lock` to avoid deadlock risk.
static AGENT_PHASE_REPO_ROOT: Mutex<Option<PathBuf>> = Mutex::new(None);

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

/// Marker file path for blocking commits during agent phase.
const MARKER_FILE: &str = ".no_agent_commit";

fn quarantine_path_in_place(path: &Path, label: &str) -> io::Result<PathBuf> {
    let parent = path.parent().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "path has no parent directory")
    })?;
    let file_name = path
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no file name"))?;

    let suffix = format!(
        "ralph.tampered.{label}.{}.{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    );
    let tampered_name = format!("{}.{}", file_name.to_string_lossy(), suffix);
    let tampered_path = parent.join(tampered_name);

    match fs::rename(path, &tampered_path) {
        Ok(()) => Ok(tampered_path),
        Err(e) => {
            // Best-effort fallback: if the tampered path is an empty directory, we can remove it
            // safely without deleting user data.
            let is_empty_dir = fs::symlink_metadata(path).ok().is_some_and(|m| m.is_dir())
                && fs::read_dir(path)
                    .ok()
                    .is_some_and(|mut it| it.next().is_none());
            if is_empty_dir {
                fs::remove_dir(path)?;
                Ok(path.to_path_buf())
            } else {
                Err(e)
            }
        }
    }
}

fn repair_marker_path_if_tampered(repo_root: &Path) -> io::Result<()> {
    let marker_path = repo_root.join(MARKER_FILE);

    if let Ok(meta) = fs::symlink_metadata(&marker_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            quarantine_path_in_place(&marker_path, "marker")?;
        }
    }

    create_marker_in_repo_root(repo_root)
}

fn create_marker_in_repo_root(repo_root: &Path) -> io::Result<()> {
    let marker_path = repo_root.join(MARKER_FILE);

    if let Ok(meta) = fs::symlink_metadata(&marker_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if is_regular_file {
            return Ok(());
        }

        // Any non-regular marker path (symlink/dir/socket/FIFO/device/etc) can bypass
        // hook/wrapper `-f` checks. Quarantine it and recreate a regular file marker.
        quarantine_path_in_place(&marker_path, "marker")?;
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
    let agent_dir = repo_root.join(".agent");
    if let Ok(meta) = fs::symlink_metadata(&agent_dir) {
        if meta.file_type().is_symlink() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                ".agent path is a symlink; refusing to write wrapper tracking file",
            ));
        }
    }
    fs::create_dir_all(&agent_dir)?;

    let track_file_path = repo_root.join(WRAPPER_DIR_TRACK_FILE);

    // If the track file path is a directory/symlink/special file, treat it as tampering.
    // Quarantine it so we can atomically replace it with a regular file.
    if let Ok(meta) = fs::symlink_metadata(&track_file_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            quarantine_path_in_place(&track_file_path, "track")?;
        }
    }

    let tmp_track = agent_dir.join(format!(
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
/// - `.no_agent_commit` exists in the protected repo root, OR
/// - the wrapper track file exists in the protected repo root (defense-in-depth
///   against an agent deleting the marker mid-run).
///
/// `git_path_escaped` and `protected_repo_root_escaped` must already be
/// shell-single-quote-escaped.
fn make_wrapper_content(git_path_escaped: &str, protected_repo_root_escaped: &str) -> String {
    format!(
        r#"#!/usr/bin/env sh
 set -eu
 # {WRAPPER_MARKER} - generated by ralph
 # NOTE: `command git` still routes through this PATH wrapper because `command`
 # only skips shell functions and aliases, not PATH entries. This wrapper is a
 # real file in PATH, so it is always invoked for any `git` command.
 protected_repo_root='{protected_repo_root_escaped}'
 marker="$protected_repo_root/.no_agent_commit"
 track_file="$protected_repo_root/.agent/git-wrapper-dir.txt"
 # Treat either the marker or the wrapper track file as an active agent-phase signal.
 # This makes the wrapper resilient if an agent deletes the marker mid-run.
 if [ -f "$marker" ] || [ -f "$track_file" ]; then
   # Unset environment variables that could be used to bypass the wrapper
   # by pointing git at a different repository or exec path.
   unset GIT_DIR
   unset GIT_WORK_TREE
   unset GIT_EXEC_PATH
   subcmd=""
  skip_next=0
  for arg in "$@"; do
    if [ "$skip_next" = "1" ]; then
      skip_next=0
      continue
    fi
    case "$arg" in
      -C|--git-dir|--work-tree|--namespace|-c|--config|--exec-path)
        skip_next=1
        ;;
      --git-dir=*|--work-tree=*|--namespace=*|--exec-path=*|-c=*|--config=*)
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
       # Allow only list-only forms of `git branch` (no positional args).
       found_branch=0
       for a2 in "$@"; do
         if [ "$found_branch" = "1" ]; then
           case "$a2" in
             -*) ;;
             *)
               echo "Blocked: git branch <name> disabled during agent phase (list-only allowed)." >&2
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

    let wrapper_dir = tempfile::Builder::new()
        .prefix(WRAPPER_DIR_PREFIX)
        .tempdir()?;
    let wrapper_dir_path = wrapper_dir.keep();
    let wrapper_path = wrapper_dir_path.join("git");

    // Escape the git path for shell script to prevent command injection.
    // Use a helper function to properly handle edge cases and reject unsafe paths.
    let git_path_escaped = escape_shell_single_quoted(git_path_str)?;

    let repo_root = get_repo_root()?;
    helpers.wrapper_repo_root = Some(repo_root.clone());

    let repo_root_str = repo_root.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "repo root path contains invalid UTF-8 characters; cannot create wrapper script",
        )
    })?;
    let repo_root_escaped = escape_shell_single_quoted(repo_root_str)?;

    let wrapper_content = make_wrapper_content(&git_path_escaped, &repo_root_escaped);

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
    write_wrapper_track_file_atomic(&repo_root, &wrapper_dir_path)?;

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
    if let Some(wrapper_dir_path) = removed_wrapper_dir.clone() {
        // Make wrapper writable before removal (wrapper is installed as read-only 0o555).
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
        let _ = fs::remove_dir_all(&wrapper_dir_path);
        // Remove from PATH.
        // Note: This read-modify-write sequence on PATH has a theoretical TOCTOU race,
        // but in practice it's safe because Ralph only calls this from the main thread
        // during controlled shutdown.
        if let Ok(path) = env::var("PATH") {
            let new_path: String = path
                .split(':')
                .filter(|p| !p.is_empty() && Path::new(p) != wrapper_dir_path.as_path())
                .collect::<Vec<_>>()
                .join(":");
            env::set_var("PATH", new_path);
        }
    }

    // IMPORTANT: remove the tracking file using an absolute repo root path.
    // The process CWD may not be the repo root (e.g., tests or effects that change CWD).
    let repo_root = helpers
        .wrapper_repo_root
        .take()
        .or_else(|| crate::git_helpers::get_repo_root().ok());

    let track_file = repo_root.as_ref().map_or_else(
        || PathBuf::from(WRAPPER_DIR_TRACK_FILE),
        |r| r.join(WRAPPER_DIR_TRACK_FILE),
    );

    // If we didn't have in-memory wrapper state (or it was out of date), fall back
    // to the track file for best-effort cleanup.
    if let Ok(content) = fs::read_to_string(&track_file) {
        let wrapper_dir = PathBuf::from(content.trim());
        let same_as_removed = removed_wrapper_dir
            .as_ref()
            .is_some_and(|p| p == &wrapper_dir);
        if wrapper_dir_is_safe_existing_dir(&wrapper_dir) && !same_as_removed {
            // Remove from PATH (exact match) and delete directory.
            if let Ok(path) = env::var("PATH") {
                let new_path: String = path
                    .split(':')
                    .filter(|p| !p.is_empty() && Path::new(p) != wrapper_dir.as_path())
                    .collect::<Vec<_>>()
                    .join(":");
                env::set_var("PATH", new_path);
            }
            let _ = fs::remove_dir_all(&wrapper_dir);
        }
    }

    let _ = fs::remove_file(track_file);
}

/// Start agent phase (creates marker file, installs hooks, enables wrapper).
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn start_agent_phase(helpers: &mut GitHelpers) -> io::Result<()> {
    let repo_root = get_repo_root()?;
    helpers.wrapper_repo_root = Some(repo_root.clone());

    // Store repo root for signal handler fallback.
    if let Ok(mut guard) = AGENT_PHASE_REPO_ROOT.lock() {
        *guard = Some(repo_root.clone());
    }

    // Self-heal: treat non-regular marker path as tampering and recover.
    repair_marker_path_if_tampered(&repo_root)?;
    // Make marker read-only (0o444) to deter agent deletion.
    #[cfg(unix)]
    set_readonly_mode_if_not_symlink(&repo_root.join(MARKER_FILE), 0o444);
    install_hooks()?;
    enable_git_wrapper(helpers)?;

    // Capture HEAD OID baseline for unauthorized commit detection.
    capture_head_oid(&repo_root);
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
pub fn end_agent_phase_in_repo(repo_root: &Path) {
    let marker_path = repo_root.join(MARKER_FILE);
    // Make writable before removal (marker is created as read-only 0o444).
    #[cfg(unix)]
    add_owner_write_if_not_symlink(&marker_path);
    let _ = fs::remove_file(marker_path);

    // Clean up HEAD OID tracking file.
    remove_head_oid_file(repo_root);

    // Clear stored repo root since agent phase is ending.
    if let Ok(mut guard) = AGENT_PHASE_REPO_ROOT.lock() {
        *guard = None;
    }
}

/// Verify and restore agent-phase commit protections before each agent invocation.
///
/// This is the composite integrity check that self-heals against a prior agent
/// that deleted the `.no_agent_commit` marker or tampered with git hooks during
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

    let Ok(repo_root) = get_repo_root() else {
        return result;
    };

    let marker_path = repo_root.join(MARKER_FILE);
    if let Ok(meta) = fs::symlink_metadata(&marker_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            logger.warn(
                ".no_agent_commit marker is not a regular file — quarantining and recreating",
            );
            result.tampering_detected = true;
            result
                .details
                .push(".no_agent_commit marker was not a regular file — quarantined".to_string());
            if let Err(e) = quarantine_path_in_place(&marker_path, "marker") {
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
    let track_file_path = repo_root.join(WRAPPER_DIR_TRACK_FILE);
    if let Ok(meta) = fs::symlink_metadata(&track_file_path) {
        let ft = meta.file_type();
        let is_regular_file = ft.is_file() && !ft.is_symlink();
        if !is_regular_file {
            logger.warn("Git wrapper tracking path is not a regular file — quarantining");
            result.tampering_detected = true;
            result
                .details
                .push("Git wrapper tracking path was not a regular file — quarantined".to_string());
            if let Err(e) = quarantine_path_in_place(&track_file_path, "track") {
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
                    let Some(repo_root_str) = repo_root.to_str() else {
                        logger.warn("Repo root path is not valid UTF-8; cannot restore wrapper");
                        return result;
                    };
                    let Ok(repo_root_escaped) = escape_shell_single_quoted(repo_root_str) else {
                        logger.warn("Failed to generate safe wrapper script (repo root path)");
                        return result;
                    };

                    let wrapper_content =
                        make_wrapper_content(&git_path_escaped, &repo_root_escaped);

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
                if let (Ok(git_path_escaped), Some(repo_root_str)) =
                    (escape_shell_single_quoted(real_git_str), repo_root.to_str())
                {
                    if let Ok(repo_root_escaped) = escape_shell_single_quoted(repo_root_str) {
                        let wrapper_content =
                            make_wrapper_content(&git_path_escaped, &repo_root_escaped);
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
        logger.warn(".no_agent_commit marker is a symlink — removing and recreating");
        let _ = fs::remove_file(&marker_path);
        result.tampering_detected = true;
        result
            .details
            .push(".no_agent_commit marker was a symlink — removed".to_string());
    }
    if !marker_exists {
        logger.warn(".no_agent_commit marker missing — recreating");
        if let Err(e) = create_marker_in_repo_root(&repo_root) {
            logger.warn(&format!("Failed to recreate .no_agent_commit: {e}"));
        } else {
            #[cfg(unix)]
            set_readonly_mode_if_not_symlink(&marker_path, 0o444);
        }
        result.tampering_detected = true;
        result
            .details
            .push(".no_agent_commit marker was missing — recreated".to_string());
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
                        ".no_agent_commit permissions loosened ({mode:#o}) — restoring to 0o444"
                    ));
                    let mut perms = meta.permissions();
                    perms.set_mode(0o444);
                    let _ = fs::set_permissions(&marker_path, perms);
                    result.tampering_detected = true;
                    result.details.push(format!(
                        ".no_agent_commit permissions loosened ({mode:#o}) — restored to 0o444"
                    ));
                }
            } else {
                // A non-file marker path would bypass hook/wrapper `-f` checks.
                // Quarantine and recreate a file marker.
                logger.warn(".no_agent_commit marker is not a regular file — quarantining");
                result.tampering_detected = true;
                result.details.push(
                    ".no_agent_commit marker was not a regular file — quarantined".to_string(),
                );
                if let Err(e) = quarantine_path_in_place(&marker_path, "marker-perms") {
                    logger.warn(&format!("Failed to quarantine marker path: {e}"));
                } else if let Err(e) = create_marker_in_repo_root(&repo_root) {
                    logger.warn(&format!(
                        "Failed to recreate .no_agent_commit after quarantine: {e}"
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
                if let Err(e) = quarantine_path_in_place(&track_file_path, "track-perms") {
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

/// Remove the git wrapper temp directory using an explicit repo root.
fn cleanup_git_wrapper_dir_silent_at(repo_root: &Path) {
    let track_file = repo_root.join(WRAPPER_DIR_TRACK_FILE);
    let wrapper_dir = fs::read_to_string(&track_file)
        .ok()
        .map(|s| PathBuf::from(s.trim()));

    if let Some(wrapper_dir) = wrapper_dir {
        // Treat track file as untrusted; only remove plausible wrapper dirs under temp.
        if wrapper_dir_is_safe_existing_dir(&wrapper_dir) {
            let _ = fs::remove_dir_all(&wrapper_dir);
        }
    }
    let _ = fs::remove_file(track_file);
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
    cleanup_agent_phase_silent_at(&repo_root);
}

/// Best-effort cleanup using an explicit repo root.
///
/// This is the consolidated cleanup function that removes all agent-phase
/// artifacts. All sub-operations use the provided repo root instead of
/// CWD-based discovery, ensuring reliability even if CWD has changed.
pub fn cleanup_agent_phase_silent_at(repo_root: &Path) {
    remove_head_oid_file(repo_root);
    end_agent_phase_in_repo(repo_root);
    cleanup_git_wrapper_dir_silent_at(repo_root);
    uninstall_hooks_silent_at(repo_root);
    cleanup_generated_files_silent_at(repo_root);
}

/// Remove generated files silently using an explicit repo root.
fn cleanup_generated_files_silent_at(repo_root: &Path) {
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

    if fs::symlink_metadata(&marker_path).is_ok() {
        // Make writable before removal (marker is created as read-only 0o444).
        #[cfg(unix)]
        {
            add_owner_write_if_not_symlink(&marker_path);
        }
        fs::remove_file(&marker_path)?;
        logger.success("Removed orphaned .no_agent_commit marker");
    } else {
        logger.info("No orphaned marker found");
    }

    Ok(())
}

/// Capture the current HEAD OID and write it to `.agent/head-oid.txt`.
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
    let agent_dir = repo_root.join(".agent");
    if let Ok(meta) = fs::symlink_metadata(&agent_dir) {
        if meta.file_type().is_symlink() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                ".agent path is a symlink; refusing to write head-oid baseline",
            ));
        }
    }
    fs::create_dir_all(&agent_dir)?;

    let head_oid_path = repo_root.join(HEAD_OID_FILE);
    if matches!(fs::symlink_metadata(&head_oid_path), Ok(m) if m.file_type().is_symlink()) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "head-oid path is a symlink; refusing to write baseline",
        ));
    }

    let tmp_path = agent_dir.join(format!(
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
    let head_oid_path = repo_root.join(HEAD_OID_FILE);
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

/// Remove the head-oid tracking file, making it writable first if needed.
fn remove_head_oid_file(repo_root: &Path) {
    let head_oid_path = repo_root.join(HEAD_OID_FILE);
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

    #[test]
    fn test_wrapper_script_handles_c_flag_before_subcommand() {
        // Verify the wrapper script iterates through arguments to skip global flags
        // like `-C /path`, `--git-dir=.git`, etc. before identifying the subcommand.
        // This ensures `git -C /path commit` is correctly blocked, not just `git commit`.
        let content = make_wrapper_content("git", "/tmp/repo");

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
    fn test_wrapper_script_handles_config_flag_before_subcommand() {
        // Verify the wrapper script handles --config (git 2.46+ alias for -c)
        // as a flag that takes a value argument, so that
        // `git --config core.hooksPath=/dev/null commit` correctly identifies
        // "commit" as the subcommand (by skipping the --config value argument).
        let content = make_wrapper_content("git", "/tmp/repo");

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
        let content = make_wrapper_content("git", "/tmp/repo");

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
        let content = make_wrapper_content("git", "/tmp/repo");
        assert!(
            content.contains("only 'stash list' allowed"),
            "wrapper should only allow stash list; got:\n{content}"
        );
    }

    #[test]
    fn test_wrapper_script_blocks_branch_positional_args() {
        let content = make_wrapper_content("git", "/tmp/repo");
        assert!(
            content.contains("branch <name>"),
            "wrapper should block git branch <name>; got:\n{content}"
        );
    }

    #[test]
    fn test_wrapper_script_uses_protected_repo_root() {
        let content = make_wrapper_content("git", "/tmp/repo");
        assert!(
            content.contains("protected_repo_root"),
            "wrapper should embed protected repo root; got:\n{content}"
        );
        assert!(
            content.contains("marker=\"$protected_repo_root/.no_agent_commit\""),
            "wrapper should check marker under protected repo root; got:\n{content}"
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
        let content = make_wrapper_content("git", "/tmp/repo");
        // Wrapper must unset GIT_DIR, GIT_WORK_TREE, and GIT_EXEC_PATH
        // when .no_agent_commit exists to prevent env var bypass.
        for var in &["GIT_DIR", "GIT_WORK_TREE", "GIT_EXEC_PATH"] {
            assert!(
                content.contains(&format!("unset {var}")),
                "wrapper must unset {var} when marker exists; got:\n{content}"
            );
        }
    }

    #[test]
    fn test_wrapper_script_documents_command_builtin_behavior() {
        let content = make_wrapper_content("git", "/tmp/repo");
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
        let agent_dir = tmp.path().join(".agent");
        fs::create_dir_all(&agent_dir).unwrap();
        fs::write(agent_dir.join("head-oid.txt"), "").unwrap();
        assert!(!detect_unauthorized_commit(tmp.path()));
    }

    #[test]
    fn test_head_oid_file_constant() {
        assert_eq!(HEAD_OID_FILE, ".agent/head-oid.txt");
    }

    #[test]
    fn test_write_wrapper_track_file_atomic_repairs_directory_tamper() {
        // If the wrapper track file path exists as a directory, treat it as tampering.
        // The wrapper must recover (quarantine/remove the directory) and write a real file.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();

        // Create a directory at the track file path.
        let track_dir_path = repo_root.join(WRAPPER_DIR_TRACK_FILE);
        fs::create_dir_all(&track_dir_path).unwrap();
        fs::write(track_dir_path.join("payload.txt"), "do not delete").unwrap();

        let wrapper_dir = repo_root.join("some-wrapper-dir");
        fs::create_dir_all(&wrapper_dir).unwrap();

        write_wrapper_track_file_atomic(repo_root, &wrapper_dir).unwrap();

        let track_file_path = repo_root.join(WRAPPER_DIR_TRACK_FILE);
        let meta = fs::metadata(&track_file_path).unwrap();
        assert!(meta.is_file(), "track file path should be a file");
        let content = fs::read_to_string(&track_file_path).unwrap();
        assert!(
            content.contains(&wrapper_dir.display().to_string()),
            "track file should contain wrapper dir path; got: {content}"
        );

        // Quarantine should preserve prior directory contents by renaming in-place.
        let agent_dir = repo_root.join(".agent");
        let quarantined = fs::read_dir(&agent_dir)
            .unwrap()
            .filter_map(Result::ok)
            .any(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with("git-wrapper-dir.txt.ralph.tampered.track")
            });
        assert!(
            quarantined,
            "expected quarantined track dir entry in .agent/"
        );
    }

    #[test]
    fn test_repair_marker_path_converts_directory_to_regular_file() {
        // If the marker path exists as a directory, treat it as tampering and
        // recover by quarantining it and creating a regular file marker.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();

        let marker_path = repo_root.join(MARKER_FILE);
        fs::create_dir_all(&marker_path).unwrap();

        // Function under test: must quarantine the directory and create a file marker.
        repair_marker_path_if_tampered(repo_root).unwrap();

        let meta = fs::metadata(&marker_path).unwrap();
        assert!(meta.is_file(), "marker path should be a regular file");

        let quarantined = fs::read_dir(repo_root)
            .unwrap()
            .filter_map(Result::ok)
            .any(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with(".no_agent_commit.ralph.tampered.marker")
            });
        assert!(
            quarantined,
            "expected quarantined marker dir entry in repo root"
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_create_marker_in_repo_root_quarantines_special_file() {
        use std::os::unix::fs::FileTypeExt;
        use std::os::unix::net::UnixListener;

        // If the marker path exists as a special file (e.g., socket/FIFO),
        // we must not treat it as a valid marker. Quarantine/replace it with
        // a regular file so the `-f` checks used by hooks/wrapper cannot be bypassed.
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();

        let marker_path = repo_root.join(MARKER_FILE);
        let listener = UnixListener::bind(&marker_path).unwrap();
        drop(listener);

        let ft = fs::symlink_metadata(&marker_path).unwrap().file_type();
        assert!(
            ft.is_socket(),
            "precondition: marker path should be a socket"
        );

        create_marker_in_repo_root(repo_root).unwrap();

        let meta = fs::symlink_metadata(&marker_path).unwrap();
        assert!(meta.is_file(), "marker path should be a regular file");

        let quarantined = fs::read_dir(repo_root)
            .unwrap()
            .filter_map(Result::ok)
            .any(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with(".no_agent_commit.ralph.tampered.marker")
            });
        assert!(
            quarantined,
            "expected quarantined special marker entry in repo root"
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
        let marker_path = repo_dir.path().join(MARKER_FILE);
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
        let marker = repo_root.join(MARKER_FILE);
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
        let agent_dir = repo_root.join(".agent");
        fs::create_dir_all(&agent_dir).unwrap();
        let head_oid = repo_root.join(HEAD_OID_FILE);
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

        // Create all generated files
        fs::write(repo_root.join(".no_agent_commit"), "").unwrap();
        fs::write(agent_dir.join("PLAN.md"), "plan").unwrap();
        fs::write(agent_dir.join("commit-message.txt"), "msg").unwrap();
        fs::write(agent_dir.join("checkpoint.json.tmp"), "{}").unwrap();

        cleanup_agent_phase_silent_at(repo_root);

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
        let agent_dir = repo_root.join(".agent");
        fs::create_dir_all(&agent_dir).unwrap();

        // Create a track file pointing to a non-existent wrapper dir (safe to clean)
        let track_file = repo_root.join(WRAPPER_DIR_TRACK_FILE);
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
}
