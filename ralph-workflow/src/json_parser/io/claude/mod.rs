//! Claude I/O Boundary Module
//!
//! Contains streaming parsers and formatting code that requires mutation,
//! interior mutability (RefCell), and loops. This module is exempt from
//! functional programming lints per docs/code-style/boundaries.md.

#![allow(clippy::all)]
#![allow(unsafe_code)]

mod formatting;
mod parser;
mod stream_parsing;

pub use parser::ClaudeParser;
