//! I/O boundary module for effect handlers.
//!
//! This module contains handlers that perform I/O operations (agent invocation,
//! file operations, etc.). The dylint rules are relaxed in this module to allow
//! imperative code patterns that are necessary for I/O operations.
//!
//! See: `docs/code-style/boundaries.md`

pub mod agent;
pub mod analysis;
pub mod checkpoint;
pub mod cloud;
pub mod commit;
pub mod context;
pub mod development;
pub mod lifecycle;
pub mod planning;
pub mod rebase;
pub mod retry_guidance;
pub mod review;
