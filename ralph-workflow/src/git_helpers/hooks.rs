//! Git hook installation and management.
//!
//! This module handles the lifecycle of Ralph-managed git hooks, including:
//!
//! - **Installation**: Creating `pre-commit`, `pre-push`, `pre-merge-commit`, and `commit-msg`
//!   hooks that block git operations during the agent phase. Hooks check both the enforcement
//!   marker (`<git-dir>/ralph/no_agent_commit`) and the wrapper track file
//!   (`<git-dir>/ralph/git-wrapper-dir.txt`), both embedded as absolute paths at install time.
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

use super::repo::{resolve_protection_scope_from, ProtectionScope};
use crate::files::file_contains_marker;
use crate::logger::Logger;
#[cfg(any(test, feature = "test-utils"))]
use crate::workspace::Workspace;
use git2::Config;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

const HOOKS_PATH_STATE_FILE: &str = "hooks-path.previous";
const WORKTREE_CONFIG_STATE_KEY: &str = "ralph.worktreeConfigOriginalState";

#[derive(Debug, Clone, PartialEq, Eq)]
enum StoredHookPath {
    Missing,
    Value(String),
}

fn hooks_path_state_path(ralph_dir: &Path) -> PathBuf {
    ralph_dir.join(HOOKS_PATH_STATE_FILE)
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum StoredSharedWorktreeConfigState {
    Missing,
    Value(String),
}

impl StoredSharedWorktreeConfigState {
    fn serialize(&self) -> String {
        match self {
            Self::Missing => "missing".to_string(),
            Self::Value(value) => format!("value:{value}"),
        }
    }

    fn deserialize(raw: &str) -> Self {
        raw.strip_prefix("value:")
            .map_or(Self::Missing, |value| Self::Value(value.to_string()))
    }
}

fn worktree_config_path(scope: &ProtectionScope) -> Option<&Path> {
    scope.worktree_config_path.as_deref()
}

fn common_config_path(scope: &ProtectionScope) -> PathBuf {
    scope.common_git_dir.join("config")
}

fn ensure_config_file_exists(path: &Path) -> io::Result<()> {
    if path.exists() {
        return Ok(());
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    File::create(path)?.sync_all()?;
    Ok(())
}

fn open_config(path: &Path) -> io::Result<Config> {
    ensure_config_file_exists(path)?;
    Config::open(path).map_err(|e| crate::git_helpers::git2_to_io_error(&e))
}

fn read_config_string(path: &Path, key: &str) -> io::Result<Option<String>> {
    if !path.exists() {
        return Ok(None);
    }
    let config = Config::open(path).map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
    match config.get_string(key) {
        Ok(value) => Ok(Some(value)),
        Err(err) if err.code() == git2::ErrorCode::NotFound => Ok(None),
        Err(err) => Err(crate::git_helpers::git2_to_io_error(&err)),
    }
}

fn remove_config_file_if_no_entries(path: &Path) -> io::Result<()> {
    if !path.exists() {
        return Ok(());
    }

    let config = Config::open(path).map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
    let mut entries = config
        .entries(None)
        .map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
    if entries.next().is_none() {
        fs::remove_file(path)?;
    }

    Ok(())
}

fn store_hook_path_state(path: &Path, state: &StoredHookPath) -> io::Result<()> {
    let content = match state {
        StoredHookPath::Missing => "missing\n".to_string(),
        StoredHookPath::Value(value) => format!("value\n{value}"),
    };
    fs::write(path, content)
}

fn load_hook_path_state(path: &Path) -> io::Result<Option<StoredHookPath>> {
    if !path.exists() {
        return Ok(None);
    }
    let content = fs::read_to_string(path)?;
    if let Some(value) = content.strip_prefix("value\n") {
        return Ok(Some(StoredHookPath::Value(value.to_string())));
    }
    Ok(Some(StoredHookPath::Missing))
}

fn read_config_path(config_path: &Path) -> io::Result<Option<PathBuf>> {
    read_config_string(config_path, "core.hooksPath").map(|value| value.map(PathBuf::from))
}

fn config_entries(path: &Path) -> io::Result<Vec<(String, Option<String>)>> {
    if !path.exists() {
        return Ok(Vec::new());
    }

    let config = Config::open(path).map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
    let mut entries = config
        .entries(None)
        .map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
    let mut values = Vec::new();

    while let Some(entry) = entries.next() {
        let entry = entry.map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
        let name = entry
            .name()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "config entry missing name"))?
            .to_string();
        let value = entry.value().map(ToString::to_string);
        values.push((name, value));
    }

    Ok(values)
}

fn read_shared_worktree_config_state(
    common_config: &Path,
) -> io::Result<Option<StoredSharedWorktreeConfigState>> {
    if !common_config.exists() {
        return Ok(None);
    }

    let config =
        Config::open(common_config).map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
    match config.get_string(WORKTREE_CONFIG_STATE_KEY) {
        Ok(value) => Ok(Some(StoredSharedWorktreeConfigState::deserialize(&value))),
        Err(err) if err.code() == git2::ErrorCode::NotFound => Ok(None),
        Err(err) => Err(crate::git_helpers::git2_to_io_error(&err)),
    }
}

fn write_shared_worktree_config_state(
    common_config: &Path,
    state: &StoredSharedWorktreeConfigState,
) -> io::Result<()> {
    let mut config = open_config(common_config)?;
    config
        .set_str(WORKTREE_CONFIG_STATE_KEY, &state.serialize())
        .map_err(|e| crate::git_helpers::git2_to_io_error(&e))
}

fn remove_shared_worktree_config_state(common_config: &Path) -> io::Result<()> {
    let mut config = open_config(common_config)?;
    match config.remove(WORKTREE_CONFIG_STATE_KEY) {
        Ok(()) => {}
        Err(err) if err.code() == git2::ErrorCode::NotFound => {}
        Err(err) => return Err(crate::git_helpers::git2_to_io_error(&err)),
    }
    remove_config_file_if_no_entries(common_config)
}

fn write_worktree_hooks_path(scope: &ProtectionScope) -> io::Result<()> {
    let Some(config_path) = worktree_config_path(scope) else {
        return Ok(());
    };
    let hooks_path = scope.hooks_dir.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "hooks path contains invalid UTF-8 characters",
        )
    })?;
    let mut config = open_config(config_path)?;
    config
        .set_str("core.hooksPath", hooks_path)
        .map_err(|e| crate::git_helpers::git2_to_io_error(&e))
}

fn restore_worktree_hooks_path(scope: &ProtectionScope) -> io::Result<()> {
    let Some(config_path) = worktree_config_path(scope) else {
        return Ok(());
    };
    let Some(state) = load_hook_path_state(&hooks_path_state_path(&scope.ralph_dir))? else {
        return Ok(());
    };

    let mut config = open_config(config_path)?;
    match state {
        StoredHookPath::Missing => match config.remove("core.hooksPath") {
            Ok(()) => {}
            Err(err) if err.code() == git2::ErrorCode::NotFound => {}
            Err(err) => return Err(crate::git_helpers::git2_to_io_error(&err)),
        },
        StoredHookPath::Value(value) => config
            .set_str("core.hooksPath", &value)
            .map_err(|e| crate::git_helpers::git2_to_io_error(&e))?,
    }

    let _ = fs::remove_file(hooks_path_state_path(&scope.ralph_dir));
    remove_config_file_if_no_entries(config_path)?;
    Ok(())
}

fn scoped_hooks_dir_for_config(config_path: &Path, common_git_dir: &Path) -> Option<PathBuf> {
    let git_dir = config_path.parent()?;
    if git_dir == common_git_dir {
        return Some(common_git_dir.join("ralph").join("hooks"));
    }

    let worktrees_dir = git_dir.parent()?;
    (worktrees_dir.file_name()? == "worktrees").then(|| git_dir.join("ralph").join("hooks"))
}

fn protected_config_paths(scope: &ProtectionScope) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    paths.push(scope.common_git_dir.join("config.worktree"));

    let worktrees_dir = scope.common_git_dir.join("worktrees");
    if let Ok(entries) = fs::read_dir(worktrees_dir) {
        for entry in entries.flatten() {
            paths.push(entry.path().join("config.worktree"));
        }
    }

    paths
}

fn other_active_ralph_hooks_path_overrides_exist(scope: &ProtectionScope) -> io::Result<bool> {
    let current_config = worktree_config_path(scope);

    for config_path in protected_config_paths(scope) {
        if current_config == Some(config_path.as_path()) || !config_path.exists() {
            continue;
        }

        let Some(expected_hooks_dir) =
            scoped_hooks_dir_for_config(&config_path, &scope.common_git_dir)
        else {
            continue;
        };

        if read_config_path(&config_path)?.is_some_and(|value| value == expected_hooks_dir) {
            return Ok(true);
        }
    }

    Ok(false)
}

fn config_worktree_is_safe_to_activate(
    scope: &ProtectionScope,
    config_path: &Path,
) -> io::Result<bool> {
    let entries = config_entries(config_path)?;
    if entries.is_empty() {
        return Ok(true);
    }

    let expected_hooks_path = scope.hooks_dir.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "hooks path contains invalid UTF-8 characters",
        )
    })?;

    Ok(worktree_config_path(scope) == Some(config_path)
        && entries.len() == 1
        && entries[0].0 == "core.hooksPath"
        && entries[0].1.as_deref() == Some(expected_hooks_path))
}

fn ensure_worktree_config_extension_activation_is_safe(scope: &ProtectionScope) -> io::Result<()> {
    for config_path in protected_config_paths(scope) {
        if !config_worktree_is_safe_to_activate(scope, &config_path)? {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "refusing to enable extensions.worktreeConfig because {} already contains worktree-specific settings outside Ralph's active scope",
                    config_path.display()
                ),
            ));
        }
    }

    Ok(())
}

fn ensure_worktree_config_extension(scope: &ProtectionScope) -> io::Result<()> {
    if !scope.uses_worktree_scoped_hooks {
        return Ok(());
    }

    let common_config = common_config_path(scope);
    let mut config = open_config(&common_config)?;
    let current_state = match config.get_string("extensions.worktreeConfig") {
        Ok(value) => Some(value),
        Err(err) if err.code() == git2::ErrorCode::NotFound => None,
        Err(err) => return Err(crate::git_helpers::git2_to_io_error(&err)),
    };

    if current_state.as_deref() == Some("true") {
        return Ok(());
    }

    ensure_worktree_config_extension_activation_is_safe(scope)?;

    if read_shared_worktree_config_state(&common_config)?.is_none() {
        let stored_state = current_state.map_or(
            StoredSharedWorktreeConfigState::Missing,
            StoredSharedWorktreeConfigState::Value,
        );
        write_shared_worktree_config_state(&common_config, &stored_state)?;
        config = open_config(&common_config)?;
    }

    config
        .set_str("extensions.worktreeConfig", "true")
        .map_err(|e| crate::git_helpers::git2_to_io_error(&e))
}

fn restore_worktree_config_extension(scope: &ProtectionScope) -> io::Result<()> {
    if !scope.uses_worktree_scoped_hooks || other_active_ralph_hooks_path_overrides_exist(scope)? {
        return Ok(());
    }

    let common_config = common_config_path(scope);
    let Some(state) = read_shared_worktree_config_state(&common_config)? else {
        return Ok(());
    };
    let mut config = open_config(&common_config)?;
    match state {
        StoredSharedWorktreeConfigState::Missing => {
            match config.remove("extensions.worktreeConfig") {
                Ok(()) => {}
                Err(err) if err.code() == git2::ErrorCode::NotFound => {}
                Err(err) => return Err(crate::git_helpers::git2_to_io_error(&err)),
            }
        }
        StoredSharedWorktreeConfigState::Value(value) => config
            .set_str("extensions.worktreeConfig", &value)
            .map_err(|e| crate::git_helpers::git2_to_io_error(&e))?,
    }
    remove_shared_worktree_config_state(&common_config)?;
    Ok(())
}

fn ensure_worktree_hook_scoping(scope: &ProtectionScope) -> io::Result<()> {
    if !scope.uses_worktree_scoped_hooks {
        return Ok(());
    }

    ensure_worktree_config_extension(scope)?;

    let state_path = hooks_path_state_path(&scope.ralph_dir);
    let created_state_file = if state_path.exists() {
        false
    } else {
        let current_value = worktree_config_path(scope)
            .map(|path| read_config_string(path, "core.hooksPath"))
            .transpose()?
            .flatten();
        let state = current_value.map_or(StoredHookPath::Missing, StoredHookPath::Value);
        store_hook_path_state(&state_path, &state)?;
        true
    };

    if let Err(err) = write_worktree_hooks_path(scope) {
        if created_state_file {
            let _ = fs::remove_file(&state_path);
        }
        return Err(err);
    }

    Ok(())
}

fn restore_worktree_hook_scoping(scope: &ProtectionScope) -> io::Result<()> {
    if !scope.uses_worktree_scoped_hooks {
        return Ok(());
    }

    restore_worktree_hooks_path(scope)?;
    restore_worktree_config_extension(scope)
}

fn ensure_scoped_hooks_dir_is_owned(scope: &ProtectionScope) -> io::Result<()> {
    if scope.hooks_dir.parent() != Some(scope.ralph_dir.as_path()) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "refusing to install hooks outside Ralph's scoped metadata dir: {}",
                scope.hooks_dir.display()
            ),
        ));
    }

    if let Ok(meta) = fs::symlink_metadata(&scope.hooks_dir) {
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

    fs::create_dir_all(&scope.hooks_dir)?;

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

fn hooks_path_matches_scope(scope: &ProtectionScope) -> io::Result<bool> {
    let Some(config_path) = worktree_config_path(scope) else {
        return Ok(true);
    };
    let Some(value) = read_config_string(config_path, "core.hooksPath")? else {
        return Ok(false);
    };
    Ok(Path::new(&value) == scope.hooks_dir)
}

fn remove_scoped_hooks_dir_if_empty(scope: &ProtectionScope) {
    if scope.hooks_dir.parent() != Some(scope.ralph_dir.as_path()) {
        return;
    }
    let _ = fs::remove_dir(&scope.hooks_dir);
}

/// Uninstall all Ralph-managed hooks in an explicit repository.
///
/// This is used for startup cleanup where the process current working directory
/// may differ from the repo root we're operating on.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn uninstall_hooks_in_repo(repo_root: &Path, logger: &Logger) -> io::Result<()> {
    let scope = resolve_protection_scope_from(repo_root)?;
    let hooks_dir = scope.hooks_dir.clone();
    if !hooks_dir.exists() {
        restore_worktree_hook_scoping(&scope)?;
        remove_scoped_hooks_dir_if_empty(&scope);
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

    restore_worktree_hook_scoping(&scope)?;
    remove_scoped_hooks_dir_if_empty(&scope);

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
pub const RALPH_HOOK_NAMES: &[&str] = &["pre-commit", "pre-push", "pre-merge-commit", "commit-msg"];

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

/// Generate the content of a Ralph-managed hook script.
///
/// The hook checks both the enforcement marker AND the wrapper track file for defense-in-depth.
/// Both paths are embedded as absolute literals at install time using libgit2 to resolve
/// the actual git metadata directory (handles worktrees correctly).
///
/// If either file exists, the hook blocks the operation.
///
/// `marker_path_bash`, `track_file_path_bash`, and `orig_path_bash` must be pre-escaped
/// for bash single quotes.
fn make_hook_content(
    hook_name: &str,
    marker_path_bash: &str,
    track_file_path_bash: &str,
    orig_path_bash: &str,
) -> String {
    format!(
        r#"#!/usr/bin/env bash
set -euo pipefail
# {HOOK_MARKER} - generated by ralph

marker={marker_path_bash}
track_file={track_file_path_bash}

if [[ -f "$marker" ]] || [[ -f "$track_file" ]]; then
  echo "{hook_name} blocked: agent phase protections active."
  exit 1
fi

orig={orig_path_bash}
if [[ -f "$orig" ]]; then
  exec "$orig" "$@"
fi

exit 0
"#
    )
}

fn install_hook_with_repo_root(
    hook_name: &str,
    ralph_dir: &Path,
    hooks_dir: &Path,
    hook_path: &Path,
) -> io::Result<()> {
    // Compute absolute paths for marker and track file inside the ralph git dir.
    let marker_path = ralph_dir.join("no_agent_commit");
    let track_file_path = ralph_dir.join("git-wrapper-dir.txt");

    // Render paths as bash-safe literals (single-quoted) to avoid path parsing issues.
    // This tolerates spaces and most odd characters, including embedded double-quotes.
    let marker_path_bash = bash_single_quote_literal(&marker_path.display().to_string());
    let track_file_path_bash = bash_single_quote_literal(&track_file_path.display().to_string());

    let hook_parent_dir = hook_path.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "Hook path has no parent directory",
        )
    })?;
    if hook_parent_dir != hooks_dir {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "refusing to install hook outside scoped hooks dir: {}",
                hook_path.display()
            ),
        ));
    }

    let resolved_hook_dir = fs::canonicalize(hooks_dir)?;
    let resolved_ralph_dir = fs::canonicalize(ralph_dir)?;
    if resolved_hook_dir.parent() != Some(resolved_ralph_dir.as_path()) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "refusing to install hook outside Ralph's scoped metadata dir: {}",
                hook_path.display()
            ),
        ));
    }

    // Use absolute path for orig backup.
    // Handle the case where hook_path has no parent or file_name gracefully.
    let hook_file_name = hook_path
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Hook path has no file name"))?;
    let hook_path_abs = resolved_hook_dir.join(hook_file_name);
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

    // Write new hook with absolute enforcement paths embedded (no git CLI dependency).
    let hook_content = make_hook_content(
        hook_name,
        &marker_path_bash,
        &track_file_path_bash,
        &orig_path_bash,
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

/// Install a git hook.
///
/// The hook script embeds absolute paths to the enforcement state files inside
/// the ralph git metadata directory, avoiding any dependency on the git CLI.
/// The paths are resolved at installation time using libgit2.
///
/// # Errors
///
/// Returns error if the operation fails.
#[cfg(any(test, feature = "test-utils"))]
pub fn install_hook(hook_name: &str, hook_path: &Path) -> io::Result<()> {
    let repo_root = super::repo::get_repo_root()?;
    let ralph_dir = super::repo::ensure_ralph_git_dir(&repo_root)?;
    let hooks_dir = hook_path.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "Hook path has no parent directory",
        )
    })?;
    install_hook_with_repo_root(hook_name, &ralph_dir, hooks_dir, hook_path)
}

/// Install Ralph-managed hooks for an explicit repository root.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn install_hooks_in_repo(repo_root: &Path) -> io::Result<()> {
    let scope = resolve_protection_scope_from(repo_root)?;

    // Resolve the ralph metadata dir (handles worktrees via libgit2).
    let ralph_dir = super::repo::ensure_ralph_git_dir(repo_root)?;
    let hooks_dir = scope.hooks_dir.clone();
    ensure_scoped_hooks_dir_is_owned(&scope)?;
    ensure_worktree_hook_scoping(&scope)?;

    for hook_name in RALPH_HOOK_NAMES {
        let label = match *hook_name {
            "pre-commit" => "Commit",
            "pre-push" => "Push",
            "pre-merge-commit" => "Merge commit",
            "commit-msg" => "Commit message",
            _ => hook_name,
        };
        install_hook_with_repo_root(label, &ralph_dir, &hooks_dir, &hooks_dir.join(hook_name))?;
    }

    Ok(())
}

/// Install pre-commit and pre-push hooks.
///
/// # Errors
///
/// Returns error if the operation fails.
#[cfg(any(test, feature = "test-utils"))]
pub fn install_hooks() -> io::Result<()> {
    let repo_root = super::repo::get_repo_root()?;
    install_hooks_in_repo(&repo_root)
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
    let repo_root = super::repo::get_repo_root()?;
    uninstall_hooks_in_repo(&repo_root, logger)
}

/// Silently uninstall all Ralph-managed hooks for a specific repo root.
///
/// This variant accepts an explicit repo root instead of relying on CWD-based
/// discovery, making it reliable even when the process CWD has changed.
pub fn uninstall_hooks_silent_at(repo_root: &Path) {
    let Ok(scope) = resolve_protection_scope_from(repo_root) else {
        return;
    };
    uninstall_hooks_silent_in_dir(&scope.hooks_dir);
    let _ = restore_worktree_hook_scoping(&scope);
    remove_scoped_hooks_dir_if_empty(&scope);
}

/// Silently uninstall Ralph-managed hooks from an explicitly provided hooks directory.
///
/// Use this when the hooks directory is already known (e.g., derived from a
/// pre-computed ralph dir as `ralph_dir.parent().join("hooks")`) to avoid an
/// extra libgit2 discovery call.
///
/// Only files that contain [`HOOK_MARKER`] are removed or restored — arbitrary
/// files in the hooks directory are never touched.
///
/// All operations are best-effort; errors are silently ignored.
pub fn uninstall_hooks_silent_in_hooks_dir(hooks_dir: &Path) {
    uninstall_hooks_silent_in_dir(hooks_dir);
}

/// Shared implementation for silent hook uninstallation.
fn uninstall_hooks_silent_in_dir(hooks_dir: &Path) {
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

/// Verify that no Ralph-managed hooks remain after cleanup.
///
/// Returns a list of hook names that still contain the Ralph marker.
/// An empty list means cleanup was successful.
///
/// # Errors
///
/// Returns an error if the git repository cannot be opened or the hooks directory
/// cannot be located for `repo_root`.
pub fn verify_hooks_removed(repo_root: &Path) -> io::Result<Vec<&'static str>> {
    let hooks_dir = super::repo::get_hooks_dir_from(repo_root)?;
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

/// Reinstall hooks if they have been tampered with or removed.
///
/// Checks each Ralph-managed hook (see [`RALPH_HOOK_NAMES`]) for the presence
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
    let Ok(scope) = super::repo::resolve_protection_scope() else {
        return Ok(false); // No git repo — nothing to protect
    };
    let hooks_dir = scope.hooks_dir.clone();

    let hooks_missing_or_tampered = RALPH_HOOK_NAMES.iter().any(|name| {
        let path = hooks_dir.join(name);
        if !path.exists() {
            return true;
        }
        !matches!(file_contains_marker(&path, HOOK_MARKER), Ok(true))
    });

    let hooks_path_tampered =
        scope.uses_worktree_scoped_hooks && !hooks_path_matches_scope(&scope)?;
    let needs_reinstall = hooks_missing_or_tampered || hooks_path_tampered;

    if needs_reinstall {
        logger.warn("Git hooks tampered with or missing — reinstalling");
        install_hooks_in_repo(&scope.repo_root)?;
        Ok(true)
    } else {
        Ok(false)
    }
}

/// Verify and restore read-only executable permissions on Ralph-managed hooks.
///
/// Checks each Ralph-managed hook (see [`RALPH_HOOK_NAMES`]) for the expected
/// permission mode (0o555). If any hook has loosened permissions (e.g., an agent
/// ran `chmod 755`), this function restores the restrictive permissions.
///
/// This is called from `ensure_agent_phase_protections()` after
/// `reinstall_hooks_if_tampered()`.
#[cfg(unix)]
pub fn enforce_hook_permissions(repo_root: &Path, logger: &Logger) {
    use std::os::unix::fs::PermissionsExt;

    let Ok(hooks_dir) = super::repo::get_hooks_dir_from(repo_root) else {
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
        if matches!(fs::symlink_metadata(&path), Ok(m) if m.file_type().is_symlink()) {
            logger.warn(&format!(
                "{hook_name} is a symlink — refusing to chmod hook permissions"
            ));
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

    fn init_repo_with_commit(path: &Path) -> git2::Repository {
        let repo = git2::Repository::init(path).unwrap();
        let sig = git2::Signature::now("test", "test@test.com").unwrap();
        fs::write(path.join("tracked.txt"), "tracked\n").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(Path::new("tracked.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "initial", &tree, &[])
            .unwrap();
        drop(tree);
        repo
    }

    // =========================================================================
    // Tests using MemoryWorkspace (workspace-aware)
    // =========================================================================

    #[test]
    fn test_ralph_hook_names_contains_all_hooks() {
        assert_eq!(RALPH_HOOK_NAMES.len(), 4);
        assert!(RALPH_HOOK_NAMES.contains(&"pre-commit"));
        assert!(RALPH_HOOK_NAMES.contains(&"pre-push"));
        assert!(RALPH_HOOK_NAMES.contains(&"pre-merge-commit"));
        assert!(RALPH_HOOK_NAMES.contains(&"commit-msg"));
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

    // =========================================================================
    // Hook content generation helper for tests
    // =========================================================================

    /// Generate hook content for testing without needing a real git repo.
    /// This mirrors the logic in `install_hook` but with provided paths.
    fn generate_hook_content_for_test(
        hook_label: &str,
        hook_filename: &str,
        ralph_dir: &str,
    ) -> String {
        let marker_path_bash = bash_single_quote_literal(&format!("{ralph_dir}/no_agent_commit"));
        let track_file_path_bash =
            bash_single_quote_literal(&format!("{ralph_dir}/git-wrapper-dir.txt"));
        let orig_path_bash =
            bash_single_quote_literal(&format!("{ralph_dir}/../hooks/{hook_filename}.ralph.orig"));
        make_hook_content(
            hook_label,
            &marker_path_bash,
            &track_file_path_bash,
            &orig_path_bash,
        )
    }

    #[test]
    fn test_generate_hook_content_for_test_uses_hook_filename_for_orig_path() {
        // Production install_hook backs up the hook by hook filename (e.g. pre-commit),
        // not by the human-readable label. The test helper must mirror that.
        let hook_content =
            generate_hook_content_for_test("Commit", "pre-commit", "/tmp/test-repo/.git/ralph");
        assert!(
            hook_content.contains("pre-commit.ralph.orig"),
            "orig hook path should use hook filename (pre-commit); got:\n{hook_content}"
        );
    }

    #[test]
    fn test_hook_blocking_message_is_ascii_only() {
        // Hook scripts are used in minimal environments; keep output ASCII-only.
        let hook_content =
            generate_hook_content_for_test("Commit", "pre-commit", "/tmp/test-repo/.git/ralph");
        assert!(
            hook_content.is_ascii(),
            "hook content must be ASCII-only; got:\n{hook_content}"
        );
    }

    // =========================================================================
    // Hook dual-check tests (marker + track file)
    // =========================================================================

    #[test]
    fn test_hook_content_contains_track_file_check() {
        // Hook scripts must check BOTH the enforcement marker AND the wrapper track file
        // for defense-in-depth. Both are embedded as absolute paths at install time.
        let hook_content =
            generate_hook_content_for_test("Commit", "pre-commit", "/tmp/test-repo/.git/ralph");
        assert!(
            hook_content.contains("git-wrapper-dir.txt"),
            "hook must check track file (git-wrapper-dir.txt); got:\n{hook_content}"
        );
        assert!(
            hook_content.contains("no_agent_commit"),
            "hook must check enforcement marker (no_agent_commit); got:\n{hook_content}"
        );
    }

    #[test]
    fn test_hook_blocks_when_only_track_file_exists() {
        // If an agent deletes the marker but the track file still exists,
        // the hook must still block the commit.
        let hook_content =
            generate_hook_content_for_test("Commit", "pre-commit", "/tmp/test-repo/.git/ralph");
        // The hook should use OR logic: marker OR track file
        assert!(
            hook_content.contains("||"),
            "hook must use OR logic to check marker OR track file; got:\n{hook_content}"
        );
    }

    #[test]
    fn test_hook_blocking_message_is_generic() {
        // The blocking message should mention 'agent phase' since
        // the hook blocks on either marker or track file.
        let hook_content =
            generate_hook_content_for_test("Commit", "pre-commit", "/tmp/test-repo/.git/ralph");
        assert!(
            hook_content.contains("agent phase"),
            "hook blocking message should mention 'agent phase'; got:\n{hook_content}"
        );
    }

    #[test]
    fn test_commit_msg_hook_content_generated() {
        // commit-msg hook provides a second blocking layer that fires even if
        // pre-commit is somehow bypassed.
        let hook_content = generate_hook_content_for_test(
            "Commit message",
            "commit-msg",
            "/tmp/test-repo/.git/ralph",
        );
        assert!(
            hook_content.contains(HOOK_MARKER),
            "commit-msg hook must contain the Ralph marker; got:\n{hook_content}"
        );
        assert!(
            hook_content.contains("Commit message blocked"),
            "commit-msg hook must reference 'Commit message' in blocking msg; got:\n{hook_content}"
        );
    }

    #[test]
    fn test_verify_hooks_removed_with_workspace() {
        // verify_hooks_removed works on the real hooks dir, so we test the logic
        // indirectly by checking that RALPH_HOOK_NAMES includes all hooks that
        // would be checked. This is a unit-level sanity check; system tests
        // cover the full install-uninstall-verify lifecycle.
        let expected_hooks = ["pre-commit", "pre-push", "pre-merge-commit", "commit-msg"];
        for hook in &expected_hooks {
            assert!(
                RALPH_HOOK_NAMES.contains(hook),
                "verify_hooks_removed checks RALPH_HOOK_NAMES which must contain {hook}"
            );
        }
    }

    // =========================================================================
    // uninstall_hooks_silent_at tests
    // =========================================================================

    #[test]
    fn test_uninstall_hooks_silent_at_removes_ralph_hooks() {
        // Create a temp dir simulating a git repo with hooks
        let tmp = tempfile::tempdir().unwrap();
        let repo_root = tmp.path();
        let hooks_dir = repo_root.join(".git/hooks");
        fs::create_dir_all(&hooks_dir).unwrap();

        // Create a Ralph-managed hook
        let hook_content = format!("#!/bin/bash\n# {HOOK_MARKER}\nexit 0\n");
        let hook_path = hooks_dir.join("pre-commit");
        fs::write(&hook_path, &hook_content).unwrap();

        // Initialize git repo so get_hooks_dir_from works
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

        // Initialize git repo
        let _repo = git2::Repository::init(repo_root).unwrap();

        // Create a Ralph-managed hook with a .ralph.orig backup
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

        // Create a non-Ralph hook
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
        // Should not panic when repo root doesn't exist
        let nonexistent = Path::new("/nonexistent/repo/root");
        uninstall_hooks_silent_at(nonexistent);
    }

    #[test]
    fn test_scoped_hooks_dir_for_config_maps_main_and_linked_worktrees_to_distinct_hook_dirs() {
        let tmp = tempfile::tempdir().unwrap();
        let main_repo = init_repo_with_commit(tmp.path());
        let worktree_path = tmp.path().join("wt-test");
        let _worktree = main_repo.worktree("wt-test", &worktree_path, None).unwrap();
        let worktree_repo = git2::Repository::open(&worktree_path).unwrap();

        let main_config = main_repo.path().join("config.worktree");
        let linked_config = worktree_repo.path().join("config.worktree");

        assert_eq!(
            scoped_hooks_dir_for_config(&main_config, main_repo.path()),
            Some(main_repo.path().join("ralph/hooks"))
        );
        assert_eq!(
            scoped_hooks_dir_for_config(&linked_config, main_repo.path()),
            Some(worktree_repo.path().join("ralph/hooks"))
        );
    }

    #[test]
    fn test_last_worktree_hook_cleanup_restores_shared_worktree_config_extension() {
        let tmp = tempfile::tempdir().unwrap();
        let root_repo_path = tmp.path().join("main");
        fs::create_dir_all(&root_repo_path).unwrap();
        let main_repo = init_repo_with_commit(&root_repo_path);
        let worktree_one = tmp.path().join("wt-one");
        let worktree_two = tmp.path().join("wt-two");
        let _wt_one = main_repo.worktree("wt-one", &worktree_one, None).unwrap();
        let _wt_two = main_repo.worktree("wt-two", &worktree_two, None).unwrap();
        let logger = Logger::new(crate::logger::Colors::with_enabled(false));
        let common_config = root_repo_path.join(".git/config");

        install_hooks_in_repo(&worktree_one).unwrap();
        install_hooks_in_repo(&worktree_two).unwrap();
        assert_eq!(
            read_config_string(&common_config, "extensions.worktreeConfig").unwrap(),
            Some("true".to_string())
        );

        uninstall_hooks_in_repo(&worktree_one, &logger).unwrap();
        assert_eq!(
            read_config_string(&common_config, "extensions.worktreeConfig").unwrap(),
            Some("true".to_string())
        );

        uninstall_hooks_in_repo(&worktree_two, &logger).unwrap();
        assert_eq!(
            read_config_string(&common_config, "extensions.worktreeConfig").unwrap(),
            None
        );
    }

    #[test]
    fn test_install_hooks_refuses_to_enable_shared_worktree_config_when_other_worktree_config_exists(
    ) {
        let tmp = tempfile::tempdir().unwrap();
        let root_repo_path = tmp.path().join("main");
        fs::create_dir_all(&root_repo_path).unwrap();
        let main_repo = init_repo_with_commit(&root_repo_path);
        let worktree_one = tmp.path().join("wt-one");
        let worktree_two = tmp.path().join("wt-two");
        let _wt_one = main_repo.worktree("wt-one", &worktree_one, None).unwrap();
        let _wt_two = main_repo.worktree("wt-two", &worktree_two, None).unwrap();

        let sibling_config = git2::Repository::open(&worktree_two)
            .unwrap()
            .path()
            .join("config.worktree");
        let mut sibling_cfg = open_config(&sibling_config).unwrap();
        sibling_cfg.set_str("core.fsmonitor", "true").unwrap();

        let common_config = root_repo_path.join(".git/config");
        let active_config = git2::Repository::open(&worktree_one)
            .unwrap()
            .path()
            .join("config.worktree");

        let err = install_hooks_in_repo(&worktree_one).expect_err(
            "install must refuse to enable shared worktreeConfig when another config.worktree would become active",
        );

        assert_eq!(err.kind(), io::ErrorKind::PermissionDenied);
        assert_eq!(
            read_config_string(&common_config, "extensions.worktreeConfig").unwrap(),
            None,
            "unsafe install must not mutate shared extension state"
        );
        assert_eq!(
            read_config_string(&active_config, "core.hooksPath").unwrap(),
            None,
            "unsafe install must not write active hooksPath override"
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_install_hooks_in_linked_worktree_quarantines_symlinked_ralph_dir_before_creating_hooks()
    {
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let root_repo_path = tmp.path().join("main");
        fs::create_dir_all(&root_repo_path).unwrap();
        let main_repo = init_repo_with_commit(&root_repo_path);
        let worktree_path = tmp.path().join("wt-one");
        let _wt = main_repo.worktree("wt-one", &worktree_path, None).unwrap();

        let scope = resolve_protection_scope_from(&worktree_path).unwrap();
        let outside = tempfile::tempdir().unwrap();
        symlink(outside.path(), &scope.ralph_dir).unwrap();

        install_hooks_in_repo(&worktree_path).unwrap();

        let ralph_meta = fs::symlink_metadata(&scope.ralph_dir).unwrap();
        assert!(
            ralph_meta.is_dir() && !ralph_meta.file_type().is_symlink(),
            "install_hooks_in_repo should recreate linked-worktree ralph dir as a real directory"
        );
        assert!(
            !outside.path().join("hooks").exists(),
            "scoped hook creation must not follow a symlinked linked-worktree ralph dir"
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_install_hooks_in_repo_rejects_symlinked_scoped_hooks_dir() {
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let root_repo_path = tmp.path().join("main");
        fs::create_dir_all(&root_repo_path).unwrap();
        let main_repo = init_repo_with_commit(&root_repo_path);
        let worktree_path = tmp.path().join("wt-one");
        let _wt = main_repo.worktree("wt-one", &worktree_path, None).unwrap();

        let scope = resolve_protection_scope_from(&worktree_path).unwrap();
        fs::create_dir_all(&scope.ralph_dir).unwrap();
        let outside = tempfile::tempdir().unwrap();
        symlink(outside.path(), &scope.hooks_dir).unwrap();

        let err = install_hooks_in_repo(&worktree_path).expect_err(
            "install must reject hook dirs that resolve outside the scoped ralph metadata dir",
        );

        assert_eq!(err.kind(), io::ErrorKind::PermissionDenied);
        assert!(
            !outside.path().join("pre-commit").exists(),
            "install must not create hooks through the symlink target"
        );
        assert!(
            read_config_string(
                &scope
                    .worktree_config_path
                    .clone()
                    .expect("linked worktree should have config.worktree"),
                "core.hooksPath"
            )
            .unwrap()
            .is_none(),
            "install must not persist a worktree hooksPath override when hook dir ownership is unsafe"
        );
        assert!(
            !hooks_path_state_path(&scope.ralph_dir).exists(),
            "failed install must not leave stale hooks-path.previous state behind"
        );
    }

    #[test]
    fn test_install_hooks_in_repo_rolls_back_hooks_path_state_on_failed_activation() {
        let tmp = tempfile::tempdir().unwrap();
        let root_repo_path = tmp.path().join("main");
        fs::create_dir_all(&root_repo_path).unwrap();
        let main_repo = init_repo_with_commit(&root_repo_path);
        let worktree_one = tmp.path().join("wt-one");
        let worktree_two = tmp.path().join("wt-two");
        let _wt_one = main_repo.worktree("wt-one", &worktree_one, None).unwrap();
        let _wt_two = main_repo.worktree("wt-two", &worktree_two, None).unwrap();

        let scope = resolve_protection_scope_from(&worktree_one).unwrap();
        let sibling_config = git2::Repository::open(&worktree_two)
            .unwrap()
            .path()
            .join("config.worktree");
        let mut sibling_cfg = open_config(&sibling_config).unwrap();
        sibling_cfg.set_str("core.fsmonitor", "true").unwrap();

        let err = install_hooks_in_repo(&worktree_one)
            .expect_err("unsafe shared worktreeConfig activation should fail");

        assert_eq!(err.kind(), io::ErrorKind::PermissionDenied);
        assert!(
            !hooks_path_state_path(&scope.ralph_dir).exists(),
            "failed activation must not leave stale hooks-path.previous state behind"
        );
    }
}
