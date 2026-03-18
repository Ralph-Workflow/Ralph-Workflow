//! I/O Boundary Module
//!
//! This module contains streaming parsers and other imperative code that requires
//! mutation, interior mutability (RefCell), and loops. It serves as the boundary
//! between the functional core and I/O operations.
//!
//! Dylint rules are relaxed in this module per docs/code-style/boundaries.md
//!
//! Note: This module must remain FLAT. Subdirectories are not allowed per
//! docs/code-style/boundaries.md. Parser implementations should be in domain
//! modules (json_parser/claude/, json_parser/gemini/, etc.).

pub mod incremental_parser;
pub mod stream_classifier;
