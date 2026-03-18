//! Claude CLI JSON parser.
//!
//! This module re-exports the Claude parser implementation from the I/O boundary module.
//! The actual implementation is in `io::claude` which is exempt from functional programming lints.

pub use crate::json_parser::io::claude::ClaudeParser;
