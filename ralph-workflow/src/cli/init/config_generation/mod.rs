//! Configuration file generation and validation.
//!
//! This module handles creating Ralph config files and PROMPT.md templates:
//! - Global config creation (`--init-global`)
//! - Local config creation (`--init-local-config`)
//! - Config validation (`--check-config`)
//! - PROMPT.md generation (`--init`)
//!
//! # Module Organization
//!
//! - [`global`] - Global config file creation
//! - [`local`] - Local config file creation
//! - [`validation`] - Config validation and error display (uses pure formatting functions)
//! - [`boundary`] - I/O boundary for PROMPT.md creation from templates
//!
//! All handlers accept a [`ConfigEnvironment`](crate::config::ConfigEnvironment) for
//! dependency injection, enabling tests to use in-memory storage instead of real filesystem.

//!
//! # Architecture Note
//!
//! The `validation.rs` module is in the `boundary/` subdirectory because it performs
//! I/O (printing). According to the Boundary-First Architecture pattern
//! from the refactoring plan, all I/O operations should be in boundary modules.
//!
//! The pure formatting functions in `validation_format.rs` are kept separate from the
//! I/O boundary functions in `validation.rs` to allow testing of formatting
//! logic without any I/O.

//!
//! See `docs/plans/2026-03-16-functional-rust-refactoring-plan.md` for details.

mod global;
mod local;
mod validation;

#[path = "boundary.rs"]
pub mod boundary;

// Re-export public API for external callers
pub use global::{handle_init_global, handle_init_global_with};
pub use local::{handle_init_local_config, handle_init_local_config_with};
pub use validation::{handle_check_config, handle_check_config_with};

// Re-export prompt handlers from boundary module
pub use boundary::{
    handle_init_state_inference_with_env, handle_init_template_arg_at_path_with_env,
};
