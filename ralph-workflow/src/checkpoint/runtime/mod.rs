//! Runtime boundary for checkpoint module.

pub mod environment;
pub mod env_capture;

pub use environment::{restore_environment_from_checkpoint, restore_environment_impl};
pub use env_capture::capture_environment;
