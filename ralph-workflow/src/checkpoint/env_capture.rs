use crate::checkpoint::state::EnvironmentSnapshot;

pub fn capture_environment() -> EnvironmentSnapshot {
    EnvironmentSnapshot::from_env_vars(std::env::vars())
}
