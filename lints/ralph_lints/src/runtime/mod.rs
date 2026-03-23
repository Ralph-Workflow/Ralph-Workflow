//! Runtime module - OS-facing capabilities.
//!
//! This module handles:
//! - Environment access (std::env)
//! - File system queries for workspace detection
//! - Other OS-level capabilities
//!
//! Per `docs/code-style/boundaries.md`, runtime/ is for OS-facing capabilities
//! including environment access.

pub mod file_length;
