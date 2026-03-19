pub fn restore_environment_from_checkpoint(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
) -> usize {
    restore_environment_impl(checkpoint, |key, value| {
        std::env::set_var(key, value);
    })
}

pub fn restore_environment_impl(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
    mut set_var: impl FnMut(&str, &str),
) -> usize {
    let Some(ref env_snap) = checkpoint.env_snapshot else {
        return 0;
    };

    let restored = env_snap
        .ralph_vars
        .iter()
        .filter(|(key, _)| !crate::checkpoint::state::is_sensitive_env_key(key))
        .count();

    env_snap
        .ralph_vars
        .iter()
        .filter(|(key, _)| !crate::checkpoint::state::is_sensitive_env_key(key))
        .for_each(|(key, value)| set_var(key, value));

    restored
}
