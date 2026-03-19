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
pub mod env_access;
pub mod event_loop;
pub mod finalization;
pub mod initialization;
#[cfg(any(test, feature = "test-utils"))]
pub mod mock_effect_handler;
pub mod pipeline_setup;
pub mod plumbing;
pub mod plumbing_boundary;
pub(crate) mod rebase;
pub mod resume;
pub mod runtime;
pub mod runtime_factory;
pub mod terminal;
pub mod validation;

pub mod io;

pub mod boundary;
pub mod cloud_progress;
pub mod config;
pub mod core;
pub mod driver;
pub mod error_handling;
pub mod iteration;
pub mod logging;
pub mod recovery;
pub mod trace;

pub use crate::app::config::{EventLoopConfig, EventLoopResult, MAX_EVENT_LOOP_ITERATIONS};
pub use crate::app::core::{run_event_loop, run_event_loop_with_handler, StatefulHandler};

pub mod runner;

pub use runner::run;

#[cfg(feature = "test-utils")]
pub use runner::{
    run_pipeline_with_effect_handler, run_with_config, run_with_config_and_handlers,
    run_with_config_and_resolver, RunWithHandlersParams,
};
