//! Boundary module for imperative process management operations.
//!
//! This module contains inherently imperative code for process execution
//! and child process detection that cannot be expressed functionally.

pub mod bfs;
pub mod command;

pub use bfs::{collect_descendants, compute_from_descendants};
pub use command::{build_agent_command_internal, build_command};
