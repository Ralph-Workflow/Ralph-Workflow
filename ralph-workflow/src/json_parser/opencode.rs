//! `OpenCode` event parser implementation
//!
//! This module handles parsing and displaying `OpenCode` NDJSON event streams.

pub mod io;

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::logger::{Colors, CHECK, CROSS};
use std::io::{BufRead, Write};
use std::path::Path;

use super::delta_display::{DeltaRenderer, TextDeltaRenderer};
use super::health::HealthMonitor;
#[cfg(feature = "test-utils")]
use super::health::StreamingQualityMetrics;
use super::terminal::TerminalMode;
use super::types::{format_tool_input, format_unknown_json_event, ContentType};

include!("opencode/event_types.rs");
include!("opencode/parser_core.rs");
include!("opencode/parser_stream.rs");
include!("opencode/formatting.rs");
include!("opencode/tests.rs");
