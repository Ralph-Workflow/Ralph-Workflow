//! Template engine runtime - imperative parsing and rendering.
//!
//! This code is inherently imperative (byte-by-byte parsing, string manipulation)
//! and lives in the runtime boundary module where functional lints are relaxed.

pub mod parser;
pub mod renderer;
