//! Claude I/O Boundary Module
//!
//! Contains streaming parsers and formatting code that requires mutation,
//! interior mutability (RefCell), and loops. This module is exempt from
//! functional programming lints per docs/code-style/boundaries.md.

// Use the original claude module's implementation
pub use crate::json_parser::claude::ClaudeParser;
