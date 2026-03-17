// Streaming session tracker implementation.
//
// This file contains the `StreamingSession` struct and all its implementation
// methods for tracking streaming state across all parsers.

use crate::json_parser::deduplication::RollingHashWindow;
use crate::json_parser::deduplication::{get_overlap_thresholds, DeltaDeduplicator};
use crate::json_parser::health::StreamingQualityMetrics;
use std::collections::hash_map::DefaultHasher;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::io::Write as IoWrite;

// Include the sub-modules
include!("session/session_struct.rs");
include!("session/state_management.rs");
include!("session/delta_handling.rs");
