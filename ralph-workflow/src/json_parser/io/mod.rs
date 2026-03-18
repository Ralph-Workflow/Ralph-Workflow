//! I/O Boundary Module
//!
//! This module contains streaming parsers and other imperative code that requires
//! mutation, interior mutability (RefCell), and loops. It serves as the boundary
//! between the functional core and I/O operations.
//!
//! Dylint rules are relaxed in this module per docs/code-style/boundaries.md

pub mod claude;
pub mod codex;
pub mod health;
pub mod incremental_parser;
pub mod stream_classifier;
