use crate::checkpoint::state::EnvironmentSnapshot;

pub fn capture_environment() -> EnvironmentSnapshot {
    crate::checkpoint::io::env_capture::capture_environment()
}
