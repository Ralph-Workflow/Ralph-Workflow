// git_helpers/worktree/io.rs — boundary module for worktree-scoped hooks management.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Worktree-scoped hooks management.

use crate::git_helpers::config_state;
use crate::git_helpers::repo::ProtectionScope;

fn read_current_hooks_path(
    scope: &ProtectionScope,
) -> std::io::Result<Option<String>> {
    scope
        .worktree_config_path
        .as_deref()
        .map(|path| config_state::read_config_string(path, "core.hooksPath"))
        .transpose()
        .map(Option::flatten)
}

fn store_hooks_path_state_if_new(
    scope: &ProtectionScope,
    state_path: &std::path::Path,
) -> std::io::Result<bool> {
    if state_path.exists() {
        return Ok(false);
    }
    let current_value = read_current_hooks_path(scope)?;
    let state = current_value.map_or(
        config_state::StoredHookPath::Missing,
        config_state::StoredHookPath::Value,
    );
    config_state::store_hook_path_state(state_path, &state)?;
    Ok(true)
}

fn write_hooks_path_with_rollback(
    scope: &ProtectionScope,
    state_path: &std::path::Path,
    created_state_file: bool,
) -> std::io::Result<()> {
    config_state::write_worktree_hooks_path(scope).inspect_err(|_| {
        if created_state_file {
            let _ = std::fs::remove_file(state_path);
        }
    })
}

pub(crate) fn ensure_worktree_hook_scoping(scope: &ProtectionScope) -> std::io::Result<()> {
    if !scope.uses_worktree_scoped_hooks {
        return Ok(());
    }

    config_state::ensure_worktree_config_extension(scope)?;

    let state_path = config_state::hooks_path_state_path(&scope.ralph_dir);
    let created_state_file = store_hooks_path_state_if_new(scope, &state_path)?;

    write_hooks_path_with_rollback(scope, &state_path, created_state_file)
}

pub(crate) fn restore_worktree_hook_scoping(scope: &ProtectionScope) -> std::io::Result<()> {
    if !scope.uses_worktree_scoped_hooks {
        return Ok(());
    }

    config_state::restore_worktree_hooks_path(scope)?;
    config_state::restore_worktree_config_extension(scope)
}
