//! Persistent state and config management for git hooks.
//!
//! Handles:
//! - Storing/loading the previous hooksPath value per-repo
//! - Storing/loading the shared worktreeConfig extension state
//! - Reading/writing git config values for hooks settings

use crate::git_helpers::repo::ProtectionScope;
use git2::Config;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub(crate) const HOOKS_PATH_STATE_FILE: &str = "hooks-path.previous";
pub(crate) const WORKTREE_CONFIG_STATE_KEY: &str = "ralph.worktreeConfigOriginalState";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum StoredHookPath {
    Missing,
    Value(String),
}

pub(crate) fn hooks_path_state_path(ralph_dir: &Path) -> PathBuf {
    ralph_dir.join(HOOKS_PATH_STATE_FILE)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum StoredSharedWorktreeConfigState {
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

pub(crate) fn worktree_config_path(scope: &ProtectionScope) -> Option<&Path> {
    scope.worktree_config_path.as_deref()
}

pub(crate) fn common_config_path(scope: &ProtectionScope) -> PathBuf {
    scope.common_git_dir.join("config")
}

pub(crate) fn ensure_config_file_exists(path: &Path) -> io::Result<()> {
    if path.exists() {
        return Ok(());
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::File::create(path)?.sync_all()?;
    Ok(())
}

pub(crate) fn open_config(path: &Path) -> io::Result<Config> {
    ensure_config_file_exists(path)?;
    Config::open(path).map_err(|e| crate::git_helpers::git2_to_io_error(&e))
}

pub(crate) fn read_config_string(path: &Path, key: &str) -> io::Result<Option<String>> {
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

pub(crate) fn store_hook_path_state(path: &Path, state: &StoredHookPath) -> io::Result<()> {
    let content = match state {
        StoredHookPath::Missing => "missing\n".to_string(),
        StoredHookPath::Value(value) => format!("value\n{value}"),
    };
    fs::write(path, content)
}

pub(crate) fn load_hook_path_state(path: &Path) -> io::Result<Option<StoredHookPath>> {
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

pub(crate) fn config_entries(path: &Path) -> io::Result<Vec<(String, Option<String>)>> {
    if !path.exists() {
        return Ok(Vec::new());
    }

    let config = Config::open(path).map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
    let entries = config
        .entries(None)
        .map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
    collect_config_entries(entries)
}

fn collect_config_entries(
    mut entries: git2::ConfigEntries,
) -> io::Result<Vec<(String, Option<String>)>> {
    let mut result = Vec::new();
    while let Some(entry) = entries.next() {
        let entry = entry.map_err(|e| crate::git_helpers::git2_to_io_error(&e))?;
        let name = entry
            .name()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "config entry missing name"))?
            .to_string();
        let value = entry.value().map(ToString::to_string);
        result.push((name, value));
    }
    Ok(result)
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

pub(crate) fn write_worktree_hooks_path(scope: &ProtectionScope) -> io::Result<()> {
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

pub(crate) fn restore_worktree_hooks_path(scope: &ProtectionScope) -> io::Result<()> {
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

fn protected_config_paths(scope: &ProtectionScope) -> Vec<PathBuf> {
    let worktrees_dir = scope.common_git_dir.join("worktrees");
    let worktree_paths: Vec<PathBuf> = fs::read_dir(worktrees_dir)
        .into_iter()
        .flatten()
        .map(|entry| entry.unwrap().path().join("config.worktree"))
        .collect();

    std::iter::once(scope.common_git_dir.join("config.worktree"))
        .chain(worktree_paths)
        .collect()
}

pub(crate) fn scoped_hooks_dir_for_config(
    config_path: &Path,
    common_git_dir: &Path,
) -> Option<PathBuf> {
    let git_dir = config_path.parent()?;
    if git_dir == common_git_dir {
        return Some(common_git_dir.join("ralph").join("hooks"));
    }

    let worktrees_dir = git_dir.parent()?;
    (worktrees_dir.file_name()? == "worktrees").then(|| git_dir.join("ralph").join("hooks"))
}

fn config_contains_only_expected_ralph_hooks_path(
    config_path: &Path,
    common_git_dir: &Path,
) -> io::Result<bool> {
    let entries = config_entries(config_path)?;
    debug_assert!(!entries.is_empty());

    let Some(expected_hooks_dir) = scoped_hooks_dir_for_config(config_path, common_git_dir) else {
        return Ok(false);
    };

    matches_single_ralph_hooks_path(&entries, &expected_hooks_dir)
}

fn matches_single_ralph_hooks_path(
    entries: &[(String, Option<String>)],
    expected_dir: &Path,
) -> io::Result<bool> {
    let expected_hooks_path = expected_dir.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "hooks path contains invalid UTF-8 characters",
        )
    })?;

    Ok(entries.len() == 1
        && entries[0].0 == "core.hooksPath"
        && entries[0].1.as_deref() == Some(expected_hooks_path))
}

fn other_active_ralph_hooks_path_overrides_exist(scope: &ProtectionScope) -> io::Result<bool> {
    let current_config = worktree_config_path(scope);
    let protected = protected_config_paths(scope);

    protected.iter().try_fold(false, |found, config_path| {
        if found {
            return Ok(true);
        }
        if current_config == Some(config_path.as_path()) || !config_path.exists() {
            return Ok(false);
        }
        let Some(expected_hooks_dir) =
            scoped_hooks_dir_for_config(config_path, &scope.common_git_dir)
        else {
            return Ok(false);
        };
        Ok(read_config_path(config_path)?.is_some_and(|value| value == expected_hooks_dir))
    })
}

fn config_worktree_is_safe_to_activate(
    scope: &ProtectionScope,
    config_path: &Path,
) -> io::Result<bool> {
    let entries = config_entries(config_path)?;
    if entries.is_empty() {
        return Ok(true);
    }

    is_single_ralph_hooks_path_for_scope(&entries, scope, config_path)
}

fn is_single_ralph_hooks_path_for_scope(
    entries: &[(String, Option<String>)],
    scope: &ProtectionScope,
    config_path: &Path,
) -> io::Result<bool> {
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
    protected_config_paths(scope).iter().try_for_each(|config_path| {
        if config_worktree_is_safe_to_activate(scope, config_path)? {
            return Ok(());
        }
        Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "refusing to enable extensions.worktreeConfig because {} already contains worktree-specific settings outside Ralph's active scope",
                config_path.display()
            ),
        ))
    })
}

pub(crate) fn ensure_worktree_config_extension(scope: &ProtectionScope) -> io::Result<()> {
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

pub(crate) fn restore_worktree_config_extension(scope: &ProtectionScope) -> io::Result<()> {
    if !scope.uses_worktree_scoped_hooks
        || other_active_ralph_hooks_path_overrides_exist(scope)?
        || unrelated_worktree_config_entries_exist(scope)?
    {
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

fn unrelated_worktree_config_entries_exist(scope: &ProtectionScope) -> io::Result<bool> {
    let protected = protected_config_paths(scope);

    protected.iter().try_fold(false, |found, config_path| {
        if found {
            return Ok(true);
        }
        if !config_path.exists() {
            return Ok(false);
        }

        if config_entries(config_path)?.is_empty() {
            return Ok(false);
        }

        if !config_contains_only_expected_ralph_hooks_path(config_path, &scope.common_git_dir)? {
            return Ok(true);
        }
        Ok(false)
    })
}

pub(crate) fn hooks_path_matches_scope(scope: &ProtectionScope) -> io::Result<bool> {
    let Some(config_path) = worktree_config_path(scope) else {
        return Ok(true);
    };
    let Some(value) = read_config_string(config_path, "core.hooksPath")? else {
        return Ok(false);
    };
    Ok(Path::new(&value) == scope.hooks_dir)
}

pub(crate) fn remove_scoped_hooks_dir_if_empty(scope: &ProtectionScope) {
    if scope.hooks_dir.parent() != Some(scope.ralph_dir.as_path()) {
        return;
    }
    let _ = fs::remove_dir(&scope.hooks_dir);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn init_repo_with_commit(path: &std::path::Path) -> git2::Repository {
        let repo = git2::Repository::init(path).unwrap();
        let sig = git2::Signature::now("test", "test@test.com").unwrap();
        fs::write(path.join("tracked.txt"), "tracked\n").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("tracked.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "initial", &tree, &[])
            .unwrap();
        drop(tree);
        repo
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
        let common_config = root_repo_path.join(".git/config");

        crate::git_helpers::install::install_hooks_in_repo(&worktree_one).unwrap();
        crate::git_helpers::install::install_hooks_in_repo(&worktree_two).unwrap();
        assert_eq!(
            read_config_string(&common_config, "extensions.worktreeConfig").unwrap(),
            Some("true".to_string())
        );

        let logger = crate::logger::Logger::new(crate::logger::Colors::with_enabled(false));
        crate::git_helpers::uninstall::uninstall_hooks_in_repo(&worktree_one, &logger).unwrap();
        assert_eq!(
            read_config_string(&common_config, "extensions.worktreeConfig").unwrap(),
            Some("true".to_string())
        );

        crate::git_helpers::uninstall::uninstall_hooks_in_repo(&worktree_two, &logger).unwrap();
        assert_eq!(
            read_config_string(&common_config, "extensions.worktreeConfig").unwrap(),
            None
        );
    }

    #[test]
    fn test_last_worktree_hook_cleanup_keeps_shared_worktree_config_extension_when_non_ralph_entries_exist(
    ) {
        let tmp = tempfile::tempdir().unwrap();
        let root_repo_path = tmp.path().join("main");
        fs::create_dir_all(&root_repo_path).unwrap();
        let main_repo = init_repo_with_commit(&root_repo_path);
        let worktree_one = tmp.path().join("wt-one");
        let worktree_two = tmp.path().join("wt-two");
        let _wt_one = main_repo.worktree("wt-one", &worktree_one, None).unwrap();
        let _wt_two = main_repo.worktree("wt-two", &worktree_two, None).unwrap();
        let logger = crate::logger::Logger::new(crate::logger::Colors::with_enabled(false));
        let common_config = root_repo_path.join(".git/config");
        let sibling_config = git2::Repository::open(&worktree_two)
            .unwrap()
            .path()
            .join("config.worktree");

        crate::git_helpers::install::install_hooks_in_repo(&worktree_one).unwrap();
        assert_eq!(
            read_config_string(&common_config, "extensions.worktreeConfig").unwrap(),
            Some("true".to_string())
        );

        let mut sibling_cfg = open_config(&sibling_config).unwrap();
        sibling_cfg.set_str("core.fsmonitor", "true").unwrap();

        crate::git_helpers::uninstall::uninstall_hooks_in_repo(&worktree_one, &logger).unwrap();
        assert_eq!(
            read_config_string(&common_config, "extensions.worktreeConfig").unwrap(),
            Some("true".to_string())
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

        let err = crate::git_helpers::install::install_hooks_in_repo(&worktree_one)
            .expect_err(
                "install must refuse to enable shared worktreeConfig when another config.worktree would become active",
            );

        assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
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

        let scope =
            crate::git_helpers::repo::resolve_protection_scope_from(&worktree_path).unwrap();
        let outside = tempfile::tempdir().unwrap();
        symlink(outside.path(), &scope.ralph_dir).unwrap();

        crate::git_helpers::install::install_hooks_in_repo(&worktree_path).unwrap();

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

        let scope =
            crate::git_helpers::repo::resolve_protection_scope_from(&worktree_path).unwrap();
        fs::create_dir_all(&scope.ralph_dir).unwrap();
        let outside = tempfile::tempdir().unwrap();
        symlink(outside.path(), &scope.hooks_dir).unwrap();

        let err = crate::git_helpers::install::install_hooks_in_repo(&worktree_path).expect_err(
            "install must reject hook dirs that resolve outside the scoped ralph metadata dir",
        );

        assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
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

        let scope = crate::git_helpers::repo::resolve_protection_scope_from(&worktree_one).unwrap();
        let sibling_config = git2::Repository::open(&worktree_two)
            .unwrap()
            .path()
            .join("config.worktree");
        let mut sibling_cfg = open_config(&sibling_config).unwrap();
        sibling_cfg.set_str("core.fsmonitor", "true").unwrap();

        let err = crate::git_helpers::install::install_hooks_in_repo(&worktree_one)
            .expect_err("unsafe shared worktreeConfig activation should fail");

        assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
        assert!(
            !hooks_path_state_path(&scope.ralph_dir).exists(),
            "failed activation must not leave stale hooks-path.previous state behind"
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_uninstall_hooks_in_repo_rejects_symlinked_scoped_hooks_dir() {
        use crate::git_helpers::install::HOOK_MARKER;
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let root_repo_path = tmp.path().join("main");
        fs::create_dir_all(&root_repo_path).unwrap();
        let main_repo = init_repo_with_commit(&root_repo_path);
        let worktree_path = tmp.path().join("wt-one");
        let _wt = main_repo.worktree("wt-one", &worktree_path, None).unwrap();

        let scope =
            crate::git_helpers::repo::resolve_protection_scope_from(&worktree_path).unwrap();
        fs::create_dir_all(&scope.ralph_dir).unwrap();
        let outside = tempfile::tempdir().unwrap();
        let outside_hook = outside.path().join("pre-commit");
        fs::write(
            &outside_hook,
            format!("#!/bin/bash\n# {HOOK_MARKER}\nexit 0\n"),
        )
        .unwrap();
        symlink(outside.path(), &scope.hooks_dir).unwrap();

        let logger = crate::logger::Logger::new(crate::logger::Colors::with_enabled(false));
        let err = crate::git_helpers::uninstall::uninstall_hooks_in_repo(&worktree_path, &logger)
            .expect_err("cleanup must refuse symlinked scoped hook dirs");

        assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
        assert!(
            outside_hook.exists(),
            "cleanup must not follow the scoped hook dir symlink and delete outside hooks"
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_uninstall_hooks_silent_at_skips_symlinked_scoped_hooks_dir() {
        use crate::git_helpers::install::HOOK_MARKER;
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let root_repo_path = tmp.path().join("main");
        fs::create_dir_all(&root_repo_path).unwrap();
        let main_repo = init_repo_with_commit(&root_repo_path);
        let worktree_path = tmp.path().join("wt-one");
        let _wt = main_repo.worktree("wt-one", &worktree_path, None).unwrap();

        let scope =
            crate::git_helpers::repo::resolve_protection_scope_from(&worktree_path).unwrap();
        fs::create_dir_all(&scope.ralph_dir).unwrap();
        let outside = tempfile::tempdir().unwrap();
        let outside_hook = outside.path().join("pre-commit");
        fs::write(
            &outside_hook,
            format!("#!/bin/bash\n# {HOOK_MARKER}\nexit 0\n"),
        )
        .unwrap();
        symlink(outside.path(), &scope.hooks_dir).unwrap();

        crate::git_helpers::uninstall::uninstall_hooks_silent_at(&worktree_path);

        assert!(
            outside_hook.exists(),
            "silent cleanup must not follow the scoped hook dir symlink and delete outside hooks"
        );
    }
}
