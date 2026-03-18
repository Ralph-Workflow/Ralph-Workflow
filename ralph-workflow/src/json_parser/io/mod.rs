//! I/O Boundary Module
//!
//! This module contains streaming parsers and other imperative code that requires
//! mutation, interior mutability (RefCell), and loops. It serves as the boundary
//! between the functional core and I/O operations.
//!
//! Dylint rules are relaxed in this module per docs/code-style/boundaries.md

#![allow(clippy::all)]
#![allow(unsafe_code)]
#![allow(forbid_mut_binding)]
#![allow(forbid_imperative_loops)]
#![allow(forbid_mutating_receiver_methods)]
#![allow(forbid_interior_mutability)]

pub mod claude;
pub mod codex;
pub mod stream_classifier;
