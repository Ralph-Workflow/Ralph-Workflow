//! Claude I/O Boundary Module
//!
//! Contains streaming parsers and formatting code that requires mutation,
//! interior mutability (RefCell), and loops. This module is exempt from
//! functional programming lints per docs/code-style/boundaries.md.

// Delta handling submodule (boundary - uses RefCell)
mod delta_handling;

// Stream parsing methods (boundary - uses RefCell and I/O loops)
include!("stream_parsing.rs");

// Formatting methods (boundary - uses RefCell for session access)
include!("formatting.rs");

// Parser core: struct definition and constructor methods (boundary - uses RefCell)
include!("parser.rs");
