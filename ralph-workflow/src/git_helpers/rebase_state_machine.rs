//! Rebase state machine for fault-tolerant rebase operations.
//!
//! This module provides a state machine that manages rebase operations
//! with checkpoint-based recovery. It tracks the current phase of a rebase
//! operation and can resume from interruptions.

#![deny(unsafe_code)]

use std::io;
use std::path::Path;

use crate::workspace::{Workspace, WorkspaceFs};

use super::rebase_checkpoint::{
    clear_rebase_checkpoint, load_rebase_checkpoint, rebase_checkpoint_exists,
    save_rebase_checkpoint, RebaseCheckpoint, RebasePhase,
};

include!("rebase_state_machine/states.rs");
include!("rebase_state_machine/transitions.rs");
include!("rebase_state_machine/tests.rs");
