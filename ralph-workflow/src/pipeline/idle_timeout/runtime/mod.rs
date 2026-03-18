//! Runtime module for idle timeout - contains OS-boundary code.

pub mod clock;
pub mod file_activity;
pub mod kill;
pub mod monitor;
mod readers;
