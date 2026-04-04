//! Native scan types for multi-pattern file scanning.
//!
//! These types are now defined in `crate::types` so that both the I/O boundary
//! layer and the domain layer can reference them without a boundary dependency.
//! This module re-exports them for backwards compatibility.

pub use crate::types::{
    LineIndex, MatchMode, NativeScanCheck, NativeScanCheckResult, NativeScanViolation,
};
