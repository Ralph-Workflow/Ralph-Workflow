//! Gemini I/O Boundary Module
//!
//! Contains streaming parsers and formatting code that requires mutation,
//! interior mutability (RefCell), and loops. This module is exempt from
//! functional programming lints per docs/code-style/boundaries.md.

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::json_parser::delta_display::{self, TextDeltaRenderer};
use crate::json_parser::io::health::HealthMonitor;
use crate::json_parser::printer::SharedPrinter;
use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::{
    format_tool_input, format_unknown_json_event, ContentType, GeminiEvent,
};
use crate::logger::{Colors, CHECK, CROSS};
use std::cell::RefCell;
use std::io::{self, BufRead};
use std::rc::Rc;

include!("parser.rs");
include!("stream_parsing.rs");
include!("event_parsing.rs");
