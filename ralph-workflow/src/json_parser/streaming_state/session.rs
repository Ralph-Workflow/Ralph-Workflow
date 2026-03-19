// Streaming session tracker implementation.
//
// This file contains the `StreamingSession` struct and all its implementation
// methods for tracking streaming state across all parsers.

use crate::json_parser::deduplication::rolling_hash::RollingHashWindow;
use crate::json_parser::deduplication::{get_overlap_thresholds, DeltaDeduplicator};
use crate::json_parser::health::StreamingQualityMetrics;
use itertools::Itertools;
use std::collections::{HashMap, HashSet};
use std::hash::Hasher;
use std::io::Write as IoWrite;

// Include the sub-modules
include!("session/session_struct.rs");
include!("session/state_management.rs");
include!("session/delta_handling.rs");
