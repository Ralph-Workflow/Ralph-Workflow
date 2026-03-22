//! Pure policy and path helpers for git config state — no I/O.
//!
//! Functions in this module compute paths, validate config entry collections,
//! and make decisions from already-resolved values. They have no filesystem,
//! git repository, or process environment dependencies and are safe to test
//! without infrastructure.

use crate::git_helpers::ProtectionScope;
use std::path::{Path, PathBuf};

/// File name for the stored hooks-path state within a ralph dir.
pub(crate) const HOOKS_PATH_STATE_FILE: &str = "hooks-path.previous";

/// Return the absolute path to the hooks-path state file for a ralph dir.
///
/// Pure: path join, no I/O.
pub(crate) fn hooks_path_state_path(ralph_dir: &Path) -> PathBuf {
    ralph_dir.join(HOOKS_PATH_STATE_FILE)
}

/// Return the worktree config path from a protection scope, if present.
///
/// Pure: reference projection from struct field, no I/O.
pub(crate) fn worktree_config_path(scope: &ProtectionScope) -> Option<&Path> {
    scope.worktree_config_path.as_deref()
}

/// Return the common git config path for a protection scope.
///
/// Pure: path join, no I/O.
pub(crate) fn common_config_path(scope: &ProtectionScope) -> PathBuf {
    scope.common_git_dir.join("config")
}

/// Determine the ralph-scoped hooks directory for a given config file path.
///
/// Returns `Some(hooks_dir)` when the config path is either the main repo's
/// `config.worktree` or a linked worktree's `config.worktree`. Returns `None`
/// for unrecognised paths.
///
/// Pure: path arithmetic, no I/O.
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

/// Return `true` iff `entries` is exactly one `core.hooksPath` entry pointing at
/// `expected_dir`.
///
/// Pure: slice inspection and path comparison, no I/O.
pub(crate) fn matches_single_ralph_hooks_path(
    entries: &[(String, Option<String>)],
    expected_dir: &Path,
) -> Result<bool, super::types::GitError> {
    let expected_hooks_path =
        expected_dir
            .to_str()
            .ok_or_else(|| super::types::GitError::ParseFailed {
                context: "hooks path contains invalid UTF-8 characters".to_string(),
            })?;

    Ok(entries.len() == 1
        && entries[0].0 == "core.hooksPath"
        && entries[0].1.as_deref() == Some(expected_hooks_path))
}

/// Return `true` iff `entries` represents only Ralph's `core.hooksPath` for
/// the active `scope` config path.
///
/// Pure: comparison over slice and path, no I/O.
pub(crate) fn is_single_ralph_hooks_path_for_scope(
    entries: &[(String, Option<String>)],
    scope: &ProtectionScope,
    config_path: &Path,
) -> Result<bool, super::types::GitError> {
    let expected_hooks_path =
        scope
            .hooks_dir
            .to_str()
            .ok_or_else(|| super::types::GitError::ParseFailed {
                context: "hooks path contains invalid UTF-8 characters".to_string(),
            })?;

    Ok(worktree_config_path(scope) == Some(config_path)
        && entries.len() == 1
        && entries[0].0 == "core.hooksPath"
        && entries[0].1.as_deref() == Some(expected_hooks_path))
}

#[cfg(test)]
mod typed_error_tests {
    use super::{is_single_ralph_hooks_path_for_scope, matches_single_ralph_hooks_path};
    use crate::git_helpers::domain::types::GitError;
    use crate::git_helpers::ProtectionScope;
    use std::path::{Path, PathBuf};

    #[cfg(unix)]
    #[test]
    fn test_matches_single_ralph_hooks_path_returns_parse_failed_for_non_utf8_path() {
        use std::ffi::OsStr;
        use std::os::unix::ffi::OsStrExt;
        let bad_bytes: &[u8] = &[0xFF, 0xFE];
        let bad_path = Path::new(OsStr::from_bytes(bad_bytes));
        let entries = vec![("core.hooksPath".to_string(), Some("/some/path".to_string()))];
        let err = matches_single_ralph_hooks_path(&entries, bad_path).unwrap_err();
        assert!(
            matches!(err, GitError::ParseFailed { .. }),
            "expected ParseFailed, got {err:?}"
        );
    }

    #[test]
    fn test_matches_single_ralph_hooks_path_returns_ok_true_when_single_matching_entry() {
        let path = Path::new("/hooks/dir");
        let entries = vec![("core.hooksPath".to_string(), Some("/hooks/dir".to_string()))];
        let result = matches_single_ralph_hooks_path(&entries, path);
        assert_eq!(result, Ok(true));
    }

    #[test]
    fn test_matches_single_ralph_hooks_path_returns_ok_false_when_no_entries() {
        let path = Path::new("/hooks/dir");
        let result = matches_single_ralph_hooks_path(&[], path);
        assert_eq!(result, Ok(false));
    }

    #[test]
    fn test_matches_single_ralph_hooks_path_returns_ok_false_when_wrong_value() {
        let path = Path::new("/hooks/dir");
        let entries = vec![("core.hooksPath".to_string(), Some("/other/dir".to_string()))];
        let result = matches_single_ralph_hooks_path(&entries, path);
        assert_eq!(result, Ok(false));
    }

    #[cfg(unix)]
    #[test]
    fn test_is_single_ralph_hooks_path_for_scope_returns_parse_failed_for_non_utf8_hooks_dir() {
        use std::ffi::OsStr;
        use std::os::unix::ffi::OsStrExt;
        let bad_bytes: &[u8] = &[0xFF, 0xFE];
        let bad_path = PathBuf::from(OsStr::from_bytes(bad_bytes));
        let common = Path::new("/repo/.git");
        let scope = ProtectionScope {
            repo_root: common.parent().unwrap_or(common).to_path_buf(),
            git_dir: common.to_path_buf(),
            common_git_dir: common.to_path_buf(),
            hooks_dir: bad_path,
            ralph_dir: common.join("ralph"),
            is_linked_worktree: false,
            worktree_config_path: Some(common.join("config.worktree")),
            uses_worktree_scoped_hooks: true,
        };
        let entries = vec![("core.hooksPath".to_string(), Some("/some/path".to_string()))];
        let config_path = common.join("config.worktree");
        let err = is_single_ralph_hooks_path_for_scope(&entries, &scope, &config_path).unwrap_err();
        assert!(
            matches!(err, GitError::ParseFailed { .. }),
            "expected ParseFailed, got {err:?}"
        );
    }
}

#[cfg(test)]
mod pure_helpers_tests {
    use super::{
        common_config_path, hooks_path_state_path, scoped_hooks_dir_for_config,
        worktree_config_path, HOOKS_PATH_STATE_FILE,
    };
    use crate::git_helpers::ProtectionScope;
    use std::path::{Path, PathBuf};

    fn make_scope(common: &Path, worktree_cfg: Option<PathBuf>, hooks: &Path) -> ProtectionScope {
        ProtectionScope {
            repo_root: common.parent().unwrap_or(common).to_path_buf(),
            git_dir: common.to_path_buf(),
            common_git_dir: common.to_path_buf(),
            hooks_dir: hooks.to_path_buf(),
            ralph_dir: common.join("ralph"),
            is_linked_worktree: worktree_cfg.is_some(),
            uses_worktree_scoped_hooks: worktree_cfg.is_some(),
            worktree_config_path: worktree_cfg,
        }
    }

    // --- hooks_path_state_path ---

    #[test]
    fn test_hooks_path_state_path_joins_constant_filename() {
        let ralph_dir = Path::new("/repo/.git/ralph");
        let result = hooks_path_state_path(ralph_dir);
        assert_eq!(result, ralph_dir.join(HOOKS_PATH_STATE_FILE));
    }

    #[test]
    fn test_hooks_path_state_path_filename_is_hooks_path_previous() {
        let ralph_dir = Path::new("/some/dir");
        let result = hooks_path_state_path(ralph_dir);
        assert_eq!(result.file_name().unwrap(), "hooks-path.previous");
    }

    // --- worktree_config_path ---

    #[test]
    fn test_worktree_config_path_returns_none_when_not_set() {
        let common = Path::new("/repo/.git");
        let scope = make_scope(common, None, &common.join("ralph/hooks"));
        assert!(worktree_config_path(&scope).is_none());
    }

    #[test]
    fn test_worktree_config_path_returns_some_when_set() {
        let common = Path::new("/repo/.git");
        let cfg = common.join("config.worktree");
        let scope = make_scope(common, Some(cfg.clone()), &common.join("ralph/hooks"));
        assert_eq!(worktree_config_path(&scope), Some(cfg.as_path()));
    }

    // --- common_config_path ---

    #[test]
    fn test_common_config_path_returns_config_in_common_git_dir() {
        let common = Path::new("/repo/.git");
        let scope = make_scope(common, None, &common.join("ralph/hooks"));
        assert_eq!(common_config_path(&scope), common.join("config"));
    }

    // --- scoped_hooks_dir_for_config ---

    #[test]
    fn test_scoped_hooks_dir_for_config_config_in_common_git_dir_returns_ralph_hooks() {
        let common = Path::new("/repo/.git");
        let config_path = common.join("config");
        let result = scoped_hooks_dir_for_config(&config_path, common);
        assert_eq!(result, Some(common.join("ralph").join("hooks")));
    }

    #[test]
    fn test_scoped_hooks_dir_for_config_linked_worktree_returns_local_ralph_hooks() {
        // /repo/.git/worktrees/wt1/config -> parent is wt1, grandparent is worktrees
        let common = Path::new("/repo/.git");
        let wt_git_dir = common.join("worktrees").join("wt1");
        let config_path = wt_git_dir.join("config");
        let result = scoped_hooks_dir_for_config(&config_path, common);
        assert_eq!(result, Some(wt_git_dir.join("ralph").join("hooks")));
    }

    #[test]
    fn test_scoped_hooks_dir_for_config_non_worktrees_parent_returns_none() {
        // config path parent is not common_git_dir and grandparent is not "worktrees"
        let common = Path::new("/repo/.git");
        let other_dir = Path::new("/repo/.git/other/subdir");
        let config_path = other_dir.join("config");
        let result = scoped_hooks_dir_for_config(&config_path, common);
        assert!(result.is_none());
    }
}

#[cfg(test)]
mod is_single_ralph_scope_tests {
    use super::is_single_ralph_hooks_path_for_scope;
    use crate::git_helpers::ProtectionScope;
    use std::path::{Path, PathBuf};

    fn make_scope_with_hooks(
        common: &Path,
        worktree_cfg: Option<PathBuf>,
        hooks: PathBuf,
    ) -> ProtectionScope {
        ProtectionScope {
            repo_root: common.parent().unwrap_or(common).to_path_buf(),
            git_dir: common.to_path_buf(),
            common_git_dir: common.to_path_buf(),
            hooks_dir: hooks,
            ralph_dir: common.join("ralph"),
            is_linked_worktree: worktree_cfg.is_some(),
            uses_worktree_scoped_hooks: worktree_cfg.is_some(),
            worktree_config_path: worktree_cfg,
        }
    }

    #[test]
    fn test_is_single_ralph_hooks_path_for_scope_true_on_exact_match() {
        let common = Path::new("/repo/.git");
        let hooks_dir = PathBuf::from("/repo/.git/ralph/hooks");
        let cfg_path = common.join("config.worktree");
        let scope = make_scope_with_hooks(common, Some(cfg_path.clone()), hooks_dir.clone());
        let entries = vec![(
            "core.hooksPath".to_string(),
            Some(hooks_dir.to_str().unwrap().to_string()),
        )];
        assert_eq!(
            is_single_ralph_hooks_path_for_scope(&entries, &scope, &cfg_path),
            Ok(true)
        );
    }

    #[test]
    fn test_is_single_ralph_hooks_path_for_scope_false_when_config_path_mismatch() {
        let common = Path::new("/repo/.git");
        let hooks_dir = PathBuf::from("/repo/.git/ralph/hooks");
        let cfg_path = common.join("config.worktree");
        let scope = make_scope_with_hooks(common, Some(cfg_path), hooks_dir.clone());
        let entries = vec![(
            "core.hooksPath".to_string(),
            Some(hooks_dir.to_str().unwrap().to_string()),
        )];
        let other_path = common.join("other.config");
        assert_eq!(
            is_single_ralph_hooks_path_for_scope(&entries, &scope, &other_path),
            Ok(false)
        );
    }

    #[test]
    fn test_is_single_ralph_hooks_path_for_scope_false_when_no_worktree_config_path() {
        let common = Path::new("/repo/.git");
        let hooks_dir = PathBuf::from("/repo/.git/ralph/hooks");
        let scope = make_scope_with_hooks(common, None, hooks_dir.clone());
        let entries = vec![(
            "core.hooksPath".to_string(),
            Some(hooks_dir.to_str().unwrap().to_string()),
        )];
        let config_path = common.join("config");
        assert_eq!(
            is_single_ralph_hooks_path_for_scope(&entries, &scope, &config_path),
            Ok(false)
        );
    }

    #[test]
    fn test_is_single_ralph_hooks_path_for_scope_false_when_multiple_entries() {
        let common = Path::new("/repo/.git");
        let hooks_dir = PathBuf::from("/repo/.git/ralph/hooks");
        let cfg_path = common.join("config.worktree");
        let scope = make_scope_with_hooks(common, Some(cfg_path.clone()), hooks_dir.clone());
        let entries = vec![
            (
                "core.hooksPath".to_string(),
                Some(hooks_dir.to_str().unwrap().to_string()),
            ),
            ("user.name".to_string(), Some("Alice".to_string())),
        ];
        assert_eq!(
            is_single_ralph_hooks_path_for_scope(&entries, &scope, &cfg_path),
            Ok(false)
        );
    }

    #[test]
    fn test_is_single_ralph_hooks_path_for_scope_false_when_wrong_key() {
        let common = Path::new("/repo/.git");
        let hooks_dir = PathBuf::from("/repo/.git/ralph/hooks");
        let cfg_path = common.join("config.worktree");
        let scope = make_scope_with_hooks(common, Some(cfg_path.clone()), hooks_dir.clone());
        let entries = vec![(
            "user.name".to_string(),
            Some(hooks_dir.to_str().unwrap().to_string()),
        )];
        assert_eq!(
            is_single_ralph_hooks_path_for_scope(&entries, &scope, &cfg_path),
            Ok(false)
        );
    }

    #[test]
    fn test_is_single_ralph_hooks_path_for_scope_false_when_empty_entries() {
        let common = Path::new("/repo/.git");
        let hooks_dir = PathBuf::from("/repo/.git/ralph/hooks");
        let cfg_path = common.join("config.worktree");
        let scope = make_scope_with_hooks(common, Some(cfg_path.clone()), hooks_dir);
        assert_eq!(
            is_single_ralph_hooks_path_for_scope(&[], &scope, &cfg_path),
            Ok(false)
        );
    }
}
