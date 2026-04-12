//! Runtime layer for xtask: OS-facing capabilities.
//!
//! This module contains code that directly interacts with the OS, processes,
//! filesystem, and other runtime capabilities.

pub mod dylint;
pub mod dylint_report;
pub mod process;
pub mod verify;
