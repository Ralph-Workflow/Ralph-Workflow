//! Gemini I/O Boundary Module
//!
//! Contains streaming parsers and formatting code that requires mutation,
//! interior mutability (RefCell), and loops. This module is exempt from
//! functional programming lints per docs/code-style/boundaries.md.

#![allow(clippy::all)]
#![allow(unsafe_code)]
#![allow(forbid_mut_binding)]
#![allow(forbid_imperative_loops)]
#![allow(forbid_mutating_receiver_methods)]
#![allow(forbid_interior_mutability)]

// Note: This module is a placeholder. The actual implementation
// is in the parent gemini module with lint exemptions.
