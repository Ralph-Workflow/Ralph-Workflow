//! Boundary module for imperative process management operations.
//!
//! This module contains inherently imperative code for process execution
//! and child process detection that cannot be expressed functionally.

pub mod bfs;
pub mod command;

pub use bfs::{build_command, collect_descendants};
