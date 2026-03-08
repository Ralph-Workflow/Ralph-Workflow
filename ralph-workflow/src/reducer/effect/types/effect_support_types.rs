//! Supporting types for the [`Effect`] enum.
//!
//! Defines helper structs and enums used as field types within `Effect` variants.

use crate::reducer::state::DevelopmentStatus;
use serde::{Deserialize, Serialize};

/// Data for continuation context writing.
///
/// Groups parameters for [`super::effect_enum::Effect::WriteContinuationContext`] to avoid
/// exceeding the function argument limit.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct ContinuationContextData {
    pub iteration: u32,
    pub attempt: u32,
    pub status: DevelopmentStatus,
    pub summary: String,
    /// Files changed in previous attempt. Box<[String]> saves 8 bytes per instance
    /// vs Vec<String> since this collection is never modified after construction.
    pub files_changed: Option<Box<[String]>>,
    pub next_steps: Option<String>,
}

/// Types of recovery reset operations.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub enum RecoveryResetType {
    /// Reset to the start of a phase (clear phase-specific progress flags).
    PhaseStart,
    /// Reset iteration counter (decrement and restart from Planning).
    IterationReset,
    /// Complete reset (iteration 0, restart from Planning).
    CompleteReset,
}
