//! I/O boundary module for effect handlers.
//!
//! This module contains handlers that perform I/O operations (agent invocation,
//! file operations, etc.). The dylint rules are relaxed in this module to allow
//! imperative code patterns that are necessary for I/O operations.
//!
//! See: `docs/code-style/boundaries.md`

pub mod cloud;
