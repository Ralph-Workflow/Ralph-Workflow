//! Gemini CLI JSON parser.

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::logger::{Colors, CHECK, CROSS};
use std::io::{BufRead, Write};

use super::delta_display;
use super::delta_display::{DeltaRenderer, TextDeltaRenderer};
#[cfg(feature = "test-utils")]
use super::health::StreamingQualityMetrics;
use super::types::{format_tool_input, format_unknown_json_event, ContentType, GeminiEvent};
use crate::json_parser::health::monitor::HealthMonitor;

pub mod boundary;

pub mod io;

include!("gemini/parser.rs");
include!("gemini/event_parsing.rs");
include!("gemini/stream_parsing.rs");
