pub fn restore_environment_from_checkpoint(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
) -> usize {
    let vars = restore_environment_impl(checkpoint);
    let count = vars.len();
    for (key, value) in vars {
        std::env::set_var(&key, &value);
    }
    count
}

pub fn restore_environment_impl(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
) -> Vec<(String, String)> {
    let Some(ref env_snap) = checkpoint.env_snapshot else {
        return Vec::new();
    };

    env_snap
        .ralph_vars
        .iter()
        .filter(|(key, _)| !crate::checkpoint::state::is_sensitive_env_key(key))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}
