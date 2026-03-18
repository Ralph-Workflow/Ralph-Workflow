//! Application entrypoint and pipeline orchestration.
//!
//! This module is the CLI layer operating **before the repository root is known**.
//! It uses [`AppEffect`][effect::AppEffect] for side effects, which is distinct from
//! [`Effect`][crate::reducer::effect::Effect] used after repo root discovery.
//!
//! # Boundary Module
//!
//! This module is a boundary module that handles I/O operations before the repository
//! root is discovered. As such, it is exempt from functional programming lints.
//!
//! # Two Effect Layers
//!
//! Ralph has two distinct effect types (see also [`crate`] documentation):
//!
//! | Layer | When | Filesystem Access |
//! |-------|------|-------------------|
//! | `AppEffect` (this module) | Before repo root known | `std::fs` directly |
//! | `Effect` ([`crate::reducer`]) | After repo root known | Via [`Workspace`][crate::workspace::Workspace] |
//!
//! These layers must never mix: `AppEffect` handlers cannot use `Workspace`.

pub mod config_init;
pub mod context;
pub mod detection;
pub mod effect;
pub mod effect_handler;
pub mod effectful;
pub mod event_loop;
pub mod finalization;
#[cfg(any(test, feature = "test-utils"))]
pub mod mock_effect_handler;
pub mod plumbing;
pub(crate) mod rebase;
pub mod resume;
pub mod runtime;
pub mod validation;

pub mod io;
pub mod runner;

pub use runner::run;

#[cfg(feature = "test-utils")]
pub use runner::{
    run_pipeline_with_effect_handler, run_with_config, run_with_config_and_handlers,
    run_with_config_and_resolver, RunWithHandlersParams,
};
