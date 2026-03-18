//! Claude CLI JSON parser.
//!
//! Parses NDJSON output from Claude CLI and formats it for display.
//!
//! # Streaming Output Behavior
//!
//! This parser implements real-time streaming output for text deltas. When content
//! arrives in multiple chunks (via `content_block_delta` events), the parser:
//!
//! 1. **Accumulates** text deltas from each chunk into a buffer
//! 2. **Displays** the accumulated text after each chunk
//! 3. **Uses carriage return (`\r`) and line clearing (`\x1b[2K`)** to rewrite the entire line,
//!    creating an updating effect that shows the content building up in real-time
//! 4. **Shows prefix on every delta**, rewriting the entire line each time (industry standard)
//!
//! Example output sequence for streaming "Hello World" in two chunks:
//! ```text
//! [Claude] Hello\r          (first chunk with prefix, no newline)
//! \x1b[2K\r[Claude] Hello World\r  (second chunk clears line, rewrites with accumulated)
//! [Claude] Hello World\n    (message_stop adds final newline)
//! ```
//!
//! # Single-Line Pattern
//!
//! The renderer uses a single-line pattern with carriage return for in-place updates.
//! This is the industry standard for streaming CLIs (used by Rich, Ink, Bubble Tea).
//!
//! Each delta rewrites the entire line with prefix, ensuring that:
//! - The user always sees the prefix
//! - Content updates in-place without visual artifacts
//! - Terminal state is clean and predictable
//!
//! This pattern is consistent across all parsers (Claude, Codex, Gemini, `OpenCode`)
//! with variations in when the prefix is shown based on each format's event structure.

//! Boundary module - allows imperative code patterns
#![allow(clippy::all)]
#![allow(unsafe_code)]

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::logger::{Colors, CHECK, CROSS};
use std::cell::RefCell;
use std::fmt::Write as _;
use std::io::{self, BufRead, Write};
use std::rc::Rc;

use super::health::HealthMonitor;
#[cfg(any(test, feature = "test-utils"))]
use super::health::StreamingQualityMetrics;
use super::printer::SharedPrinter;
use super::streaming_state::StreamingSession;
use super::terminal::TerminalMode;
use super::types::{
    format_tool_input, format_unknown_json_event, ClaudeEvent, ContentBlock, StreamInnerEvent,
};

// Parser struct and constructors
include!("claude/parser.rs");

// Event formatting methods
include!("claude/formatting.rs");

// Delta handling methods
mod delta_handling;

// Stream parsing methods
include!("claude/stream_parsing.rs");

// Tests
include!("claude/tests.rs");
