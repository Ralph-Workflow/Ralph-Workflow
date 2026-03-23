pub fn restore_environment_from_checkpoint(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
) -> usize {
    crate::checkpoint::io::environment::restore_environment_from_checkpoint(checkpoint)
}

pub fn restore_environment_impl(
    checkpoint: &crate::checkpoint::PipelineCheckpoint,
) -> Vec<(String, String)> {
    crate::checkpoint::io::environment::restore_environment_impl(checkpoint)
}
