//! Pipeline checkpoint state and persistence.
//!
//! This module contains the checkpoint data structures and file operations
//! for saving and loading pipeline state.
//!
//! # Checkpoint Format
//!
//! Version support:
//! - v3 checkpoints are supported (current).
//! - v2 checkpoints are supported via a minimal in-memory migration to v3 (version bump;
//!   v3-only fields remain empty).
//! - v1 and pre-v1 checkpoints are not supported.
//!
//! The following legacy phases are also rejected:
//! - "Fix" (use "Development" instead)
//! - "`ReviewAgain`" (use "Review" instead)
//!
//! # Backwards Compatibility
//!
//! Legacy checkpoint formats (v1, pre-v1) are not supported.
//!
//! If a checkpoint cannot be loaded and you need to start fresh, prefer backing it up first:
//! `cp .agent/checkpoint.json .agent/checkpoint.backup.json && rm .agent/checkpoint.json`

use serde::de::{self, Visitor};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;

use crate::workspace::Workspace;
use sha2::{Digest, Sha256};

pub(crate) use chrono::Local;

mod io {
    pub type Error = std::io::Error;
    pub type ErrorKind = std::io::ErrorKind;
    pub type Result<T> = std::result::Result<T, Error>;
}

include!("state/types/snapshots_and_phases.rs");
include!("state/types/pipeline_phase.rs");
include!("state/types/checkpoint.rs");
include!("state/serialization.rs");

#[cfg(test)]
mod tests {
    include!("state/tests.rs");
}
