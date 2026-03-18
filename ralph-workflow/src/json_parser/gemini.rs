//! Gemini CLI JSON parser.

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::logger::{Colors, CHECK, CROSS};
use std::cell::RefCell;
use std::fmt::Write as _;
use std::io::{self, BufRead, Write};
use std::rc::Rc;

use super::delta_display;
use super::delta_display::{DeltaRenderer, TextDeltaRenderer};
use super::health::HealthMonitor;
#[cfg(feature = "test-utils")]
use super::health::StreamingQualityMetrics;
use super::printer::SharedPrinter;
use super::streaming_state::StreamingSession;
use super::terminal::TerminalMode;
use super::types::{format_tool_input, format_unknown_json_event, ContentType, GeminiEvent};

include!("gemini/parser.rs");
include!("gemini/event_parsing.rs");
include!("gemini/stream_parsing.rs");
