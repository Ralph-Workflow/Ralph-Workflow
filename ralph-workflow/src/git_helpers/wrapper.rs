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
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use which::which;

const WRAPPER_DIR_TRACK_FILE: &str = ".agent/git-wrapper-dir.txt";
const WRAPPER_DIR_PREFIX: &str = "ralph-git-wrapper-";
const WRAPPER_MARKER: &str = "RALPH_AGENT_PHASE_GIT_WRAPPER";

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
    let marker_exists = marker_path.exists();

    // Ensure the PATH wrapper is present and intact.
    //
    // CRITICAL: Treat the track file as untrusted input.
    // We only use it if it points to a plausible temp directory AND that directory is
    // already present on PATH (meaning it was installed by Ralph).
    let track_file_path = repo_root.join(WRAPPER_DIR_TRACK_FILE);
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

    // Recreate marker if missing.
    if !marker_exists {
        logger.warn(".no_agent_commit marker missing — recreating");
        if let Err(e) = File::create(&marker_path) {
            logger.warn(&format!("Failed to recreate .no_agent_commit: {e}"));
        }
        result.tampering_detected = true;
        result
            .details
            .push(".no_agent_commit marker was deleted — recreated".to_string());
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
                result.tampering_detected = true;
                result.details.push(format!(
                    ".no_agent_commit permissions loosened ({mode:#o}) — restored to 0o444"
                ));
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
    super::hooks::enforce_hook_permissions(logger);

    result
}

fn cleanup_git_wrapper_dir_silent() {
    let Ok(repo_root) = crate::git_helpers::get_repo_root() else {
        return;
    };
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
}
