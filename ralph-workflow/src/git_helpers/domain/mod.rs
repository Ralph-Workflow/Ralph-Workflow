//! Pure domain logic for git_helpers — no I/O, no process execution.
//!
//! Functions here operate on already-resolved values and are safe to test
//! without any filesystem, repository, or process infrastructure.

pub(crate) mod config_policy;
pub(crate) mod parse;
pub(crate) mod types;

