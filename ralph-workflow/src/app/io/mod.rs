//! I/O boundary module for filesystem, environment, and terminal operations.
//!
//! This module contains code that performs direct I/O operations including:
//! - Filesystem operations via `std::fs`
//! - Environment variable access via `std::env`
//! - Terminal input/output via `std::io`
//!
//! As a boundary module, it is exempt from functional programming lints.

pub mod effect_handler;
