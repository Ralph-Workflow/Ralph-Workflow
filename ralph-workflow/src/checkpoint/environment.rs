pub fn restore_environment_from_checkpoint(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
) -> usize {
    let vars = restore_environment_impl(checkpoint);
    vars.iter()
        .for_each(|(key, value)| std::env::set_var(key, value));
    vars.len()
}

pub fn restore_environment_impl(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
) -> Vec<(String, String)> {
    checkpoint
        .env_snapshot
        .as_ref()
        .map_or_else(Vec::new, |env_snap| {
            env_snap
                .ralph_vars
                .iter()
                .filter(|(key, _)| !crate::checkpoint::state::is_sensitive_env_key(key))
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect()
        })
}
