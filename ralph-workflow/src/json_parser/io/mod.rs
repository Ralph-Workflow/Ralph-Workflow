//! I/O Boundary Module
//!
//! This module contains streaming parsers and other imperative code that requires
//! mutation, interior mutability (RefCell), and loops. It serves as the boundary
//! between the functional core and I/O operations.
//!
//! Dylint rules are relaxed in this module per docs/code-style/boundaries.md

#![allow(clippy::all)]
#![allow(unsafe_code)]

pub mod stream_classifier;
