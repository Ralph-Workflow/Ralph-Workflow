//! Worktree-scoped hooks management.

use crate::git_helpers::config_state;
use crate::git_helpers::repo::ProtectionScope;
use std::io;

pub(crate) fn ensure_worktree_hook_scoping(scope: &ProtectionScope) -> io::Result<()> {
    if !scope.uses_worktree_scoped_hooks {
        return Ok(());
    }

    config_state::ensure_worktree_config_extension(scope)?;

    let state_path = config_state::hooks_path_state_path(&scope.ralph_dir);
    let created_state_file = if state_path.exists() {
        false
    } else {
        let current_value = scope
            .worktree_config_path
            .as_deref()
            .map(|path| config_state::read_config_string(path, "core.hooksPath"))
            .transpose()?
            .flatten();
        let state = current_value.map_or(
            config_state::StoredHookPath::Missing,
            config_state::StoredHookPath::Value,
        );
        config_state::store_hook_path_state(&state_path, &state)?;
        true
    };

    if let Err(err) = config_state::write_worktree_hooks_path(scope) {
        if created_state_file {
            let _ = std::fs::remove_file(&state_path);
        }
        return Err(err);
    }

    Ok(())
}

pub(crate) fn restore_worktree_hook_scoping(scope: &ProtectionScope) -> io::Result<()> {
    if !scope.uses_worktree_scoped_hooks {
        return Ok(());
    }

    config_state::restore_worktree_hooks_path(scope)?;
    config_state::restore_worktree_config_extension(scope)
}
