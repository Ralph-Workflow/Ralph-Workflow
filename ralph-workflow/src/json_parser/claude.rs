//! Claude CLI JSON parser.
//
//! This module provides the functional core implementation of the Claude parser.
//! The I/O boundary module at `io::claude` re-exports from here.

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::logger::{Colors, CHECK, CROSS};
use std::fmt::Write as _;
use std::io::Write;

use super::streaming_state::StreamingSession;
use super::terminal::TerminalMode;
use super::types::{format_tool_input, format_unknown_json_event, ClaudeEvent, StreamInnerEvent};
use crate::json_parser::health::monitor::HealthMonitor;

// Delta handling submodule
mod delta_handling;

// I/O state module (exempt from interior mutability lints)
pub mod io;

// Parser core: struct definition and constructor methods
include!("claude/parser.rs");

// Stream parsing methods
include!("claude/stream_parsing.rs");

// Formatting methods
include!("claude/formatting.rs");

// Tests
include!("claude/tests.rs");
