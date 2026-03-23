//! Per-attempt logging infrastructure for commit message generation.
//!
//! This module provides detailed logging for each commit generation attempt,
//! creating a clear audit trail for debugging parsing failures. Each attempt
//! produces a unique numbered log file that captures:
//! - Prompt information
//! - Raw agent output
//! - All extraction attempts with reasons
//! - Validation results
//! - Final outcome
//!
//! Log files are organized by session to prevent overwrites and allow
//! comparison across multiple attempts.

use chrono::{DateTime, Local};
use std::path::{Path, PathBuf};

use crate::common::truncate_text;
use crate::workspace::Workspace;

include!("commit_logging/message_generation.rs");
include!("commit_logging/file_logging.rs");
include!("commit_logging/io.rs");
include!("commit_logging/tests.rs");
