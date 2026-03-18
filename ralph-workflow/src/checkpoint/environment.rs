//! Runtime boundary for checkpoint module.
//! This module contains OS-boundary code like environment access.

/// Restore environment variables from a checkpoint.
///
/// Restore safe environment variables from the checkpoint snapshot.
/// This is a boundary function - environment access is allowed here.
#[must_use]
pub fn restore_environment_from_checkpoint(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
) -> usize {
    restore_environment_impl(checkpoint, |key, value| {
        std::env::set_var(key, value);
    })
}

/// Inner implementation for restoring environment variables from a checkpoint.
///
/// Accepts an injectable `set_var` callback so tests can verify which variables
/// would be set without mutating the real process environment (eliminating the
/// need for `#[serial]`).
pub fn restore_environment_impl(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
    mut set_var: impl FnMut(&str, &str),
) -> usize {
    let Some(ref env_snap) = checkpoint.env_snapshot else {
        return 0;
    };

    let mut restored: usize = 0;

    for (key, value) in &env_snap.ralph_vars {
        if crate::checkpoint::state::is_sensitive_env_key(key) {
            continue;
        }
        set_var(key, value);
        restored = restored.saturating_add(1);
    }

    restored
}
