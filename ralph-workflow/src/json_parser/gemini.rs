//! Gemini CLI JSON parser.
//!
//! Parses NDJSON output from Gemini CLI and formats it for display.
//!
//! # Streaming Output Behavior
//!
//! This parser implements real-time streaming output for text deltas. When content
//! arrives in multiple chunks (via `message` events with `delta: true`), the parser:
//!
//! 1. **Accumulates** text deltas from each chunk into a buffer
//! 2. **Displays** the accumulated text after each chunk
//! 3. **Uses carriage return (`\r`) and line clearing (`\x1b[2K`)** to rewrite the entire line,
//!    creating an updating effect that shows the content building up in real-time
//! 4. **Shows prefix on every delta**, rewriting the entire line each time (industry standard)
//!
//! Example output sequence for streaming "Hello World" in two chunks:
//! ```text
//! [Gemini] Hello\r         (first delta with prefix, no newline)
//! \x1b[2K\r[Gemini] Hello World\r  (second delta clears line, rewrites with accumulated)
//! [Gemini] Hello World\n   (final non-delta message shows complete result)
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

#![allow(forbid_mut_binding)]
#![allow(forbid_imperative_loops)]
#![allow(forbid_mutating_receiver_methods)]
#![allow(forbid_interior_mutability)]

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
