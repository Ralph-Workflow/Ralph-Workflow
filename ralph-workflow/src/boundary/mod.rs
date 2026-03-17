//! Boundary composition module.
//!
//! Thin seams that wire pure domain functions to effectful capabilities.
//! May use mutation for local coordination but should be kept minimal.
//!
//! Deep business logic belongs in domain modules.
//!
//! ## Module Organization
//!
//! This module provides composition functions that:
//! - Gather inputs from multiple capabilities
//! - Call pure domain helpers
//! - Emit returned logs or diagnostics
//! - Translate between capability errors and domain errors
//!
//! ## Key Principles
//!
//! - Keep business logic in domain modules
//! - Use this module for wiring, not computing
//! - Pass resolved dependencies explicitly (no ambient reads)

pub mod config_loading;
