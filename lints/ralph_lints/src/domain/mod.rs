//! Domain module - pure computation with no I/O effects.
//!
//! This module contains:
//! - Boundary detection (path analysis)
//! - Metrics calculation
//! - Pattern matching logic for detecting I/O, mutability, etc.
//!
//! Per `docs/code-style/boundaries.md`, domain modules must not:
//! - call std::fs
//! - inspect environment variables
//! - read the current working directory
//! - spawn processes
//! - print to stdout/stderr
//! - read the clock

pub mod boundary;
pub mod metrics;
