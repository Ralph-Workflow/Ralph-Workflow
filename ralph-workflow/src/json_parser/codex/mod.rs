//! Codex CLI JSON parser.
//!
//! This module re-exports from the I/O boundary module where the actual
//! implementation lives. The boundary module is exempt from functional
//! programming lints per docs/code-style/boundaries.md.

pub use crate::json_parser::io::codex::CodexParser;
