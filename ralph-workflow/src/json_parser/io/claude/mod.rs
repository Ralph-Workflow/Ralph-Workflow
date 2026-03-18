//! Claude I/O Boundary Module
//!
//! Contains streaming parsers and formatting code that requires mutation,
//! interior mutability (RefCell), and loops. This module is exempt from
//! functional programming lints per docs/code-style/boundaries.md.

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::json_parser::io::health::HealthMonitor;
#[cfg(any(test, feature = "test-utils"))]
use crate::json_parser::io::health::StreamingQualityMetrics;
use crate::json_parser::printer::SharedPrinter;
use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::{
    format_tool_input, format_unknown_json_event, ClaudeEvent, ContentBlock, StreamInnerEvent,
};
use crate::logger::{Colors, CHECK, CROSS};
use std::cell::RefCell;
use std::io::{self, BufRead};
use std::rc::Rc;

// Delta handling submodule (boundary - uses RefCell)
mod delta_handling;

// Stream parsing methods (boundary - uses RefCell and I/O loops)
include!("stream_parsing.rs");

// Formatting methods (boundary - uses RefCell for session access)
include!("formatting.rs");

// Parser core: struct definition and constructor methods (boundary - uses RefCell)
include!("parser.rs");
