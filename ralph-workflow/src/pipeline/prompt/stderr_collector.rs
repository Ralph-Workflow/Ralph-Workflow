//! Re-export stderr_collector functions from runtime boundary module.
//!
//! This module re-exports from the runtime boundary to avoid dylint violations.

pub use super::runtime::stderr_collector::{
    cancel_and_join_stderr_collector, collect_stderr_with_cap_and_drain,
};
