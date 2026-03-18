//! Boundary module - thin composition seams for lint implementations.
//!
//! Per `docs/code-style/boundaries.md`, boundary/ is for thin composition seams:
//! - gather inputs from one or more capabilities
//! - call pure domain helpers
//! - emit returned logs or diagnostics
//! - translate between capability errors and domain errors
//!
//! These lints are boundary code because they:
//! 1. Gather context from the compiler (capability)
//! 2. Use pure domain logic to detect patterns
//! 3. Emit diagnostics (the effect)

pub mod boundary_function_too_complex;
pub mod forbid_imperative_loops;
pub mod forbid_interior_mutability;
pub mod forbid_io_effects;
pub mod forbid_mut_binding;
pub mod forbid_mutating_receiver_methods;
pub mod forbid_nested_boundary_modules;
pub mod forbid_terminal_output;
