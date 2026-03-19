//! `OpenCode` event parser implementation
//!
//! This module handles parsing and displaying `OpenCode` NDJSON event streams.
//!
//! # Source Code Reference
//!
//! This parser is based on analysis of the `OpenCode` source code from:
//! - **Repository**: <https://github.com/anomalyco/opencode>
//! - **Key source files**:
//!   - `/packages/opencode/src/cli/cmd/run.ts` - NDJSON output generation
//!   - `/packages/opencode/src/session/message-v2.ts` - Message part type definitions
//!
//! # NDJSON Event Format
//!
//! `OpenCode` outputs NDJSON (newline-delimited JSON) events via `--format json`.
//! Each event has the structure:
//!
//! ```json
//! {
//!   "type": "step_start" | "step_finish" | "tool_use" | "text" | "error",
//!   "timestamp": 1768191337567,
//!   "sessionID": "ses_44f9562d4ffe",
//!   ...event-specific data (usually in "part" field)
//! }
//! ```
//!
//! From `run.ts` lines 146-201, the event types are generated as:
//! ```typescript
//! outputJsonEvent("tool_use", { part })    // Tool invocations
//! outputJsonEvent("step_start", { part })  // Step initialization
//! outputJsonEvent("step_finish", { part }) // Step completion
//! outputJsonEvent("text", { part })        // Streaming text content
//! outputJsonEvent("error", { error })      // Error events
//! ```
//!
//! # Part Type Definitions
//!
//! ## `StepStartPart` (`message-v2.ts` lines 194-200)
//!
//! ```typescript
//! {
//!   id: string,
//!   sessionID: string,
//!   messageID: string,
//!   type: "step-start",
//!   snapshot: string | undefined  // Git commit hash for state snapshot
//! }
//! ```
//!
//! ## `StepFinishPart` (`message-v2.ts` lines 202-219)
//!
//! ```typescript
//! {
//!   id: string,
//!   sessionID: string,
//!   messageID: string,
//!   type: "step-finish",
//!   reason: string,               // "tool-calls", "end_turn", etc.
//!   snapshot: string | undefined,
//!   cost: number,                 // Cost in USD
//!   tokens: {
//!     input: number,
//!     output: number,
//!     reasoning: number,
//!     cache: { read: number, write: number }
//!   }
//! }
//! ```
//!
//! ## `TextPart` (`message-v2.ts` lines 62-77)
//!
//! ```typescript
//! {
//!   id: string,
//!   sessionID: string,
//!   messageID: string,
//!   type: "text",
//!   text: string,
//!   synthetic?: boolean,
//!   ignored?: boolean,
//!   time?: { start: number, end?: number },
//!   metadata?: Record<string, any>
//! }
//! ```
//!
//! ## `ToolPart` (`message-v2.ts` lines 289-298)
//!
//! ```typescript
//! {
//!   id: string,
//!   sessionID: string,
//!   messageID: string,
//!   type: "tool",
//!   callID: string,
//!   tool: string,  // Tool name: "read", "bash", "write", "edit", "glob", "grep", etc.
//!   state: ToolState,
//!   metadata?: Record<string, any>
//! }
//! ```
//!
//! ## `ToolState` Variants (`message-v2.ts` lines 221-287)
//!
//! The `state` field is a discriminated union based on `status`:
//!
//! ### Pending (`status: "pending"`)
//! ```typescript
//! { status: "pending", input: Record<string, any>, raw: string }
//! ```
//!
//! ### Running (`status: "running"`)
//! ```typescript
//! {
//!   status: "running",
//!   input: Record<string, any>,
//!   title?: string,
//!   metadata?: Record<string, any>,
//!   time: { start: number }
//! }
//! ```
//!
//! ### Completed (`status: "completed"`)
//! ```typescript
//! {
//!   status: "completed",
//!   input: Record<string, any>,
//!   output: string,
//!   title: string,
//!   metadata: Record<string, any>,
//!   time: { start: number, end: number, compacted?: number },
//!   attachments?: FilePart[]
//! }
//! ```
//!
//! ### Error (`status: "error"`)
//! ```typescript
//! {
//!   status: "error",
//!   input: Record<string, any>,
//!   error: string,
//!   metadata?: Record<string, any>,
//!   time: { start: number, end: number }
//! }
//! ```
//!
//! ## Tool Input Parameters
//!
//! The `state.input` object contains tool-specific parameters:
//!
//! | Tool    | Input Fields                                         |
//! |---------|-----------------------------------------------------|
//! | `read`  | `{ filePath: string, offset?: number, limit?: number }` |
//! | `bash`  | `{ command: string, timeout?: number }`              |
//! | `write` | `{ filePath: string, content: string }`              |
//! | `edit`  | `{ filePath: string, ... }`                          |
//! | `glob`  | `{ pattern: string, path?: string }`                 |
//! | `grep`  | `{ pattern: string, path?: string, include?: string }` |
//! | `fetch` | `{ url: string, format?: string, timeout?: number }` |
//!
//! From `run.ts` line 168, the title fallback is:
//! ```typescript
//! const title = part.state.title ||
//!   (Object.keys(part.state.input).length > 0 ? JSON.stringify(part.state.input) : "Unknown")
//! ```
//!
//! # Streaming Output Behavior
//!
//! This parser implements real-time streaming output for text deltas. When content
//! arrives in multiple chunks (via `text` events), the parser:
//!
//! 1. **Accumulates** text deltas from each chunk into a buffer
//! 2. **Displays** the accumulated text after each chunk
//! 3. **Uses carriage return (`\r`) and line clearing (`\x1b[2K`)** to rewrite the entire line,
//!    creating an updating effect that shows the content building up in real-time
//! 4. **Shows prefix on every delta**, rewriting the entire line each time (industry standard)
//!
//! Example output sequence for streaming "Hello World" in two chunks:
//! ```text
//! [OpenCode] Hello\r       (first text event with prefix, no newline)
//! \x1b[2K\r[OpenCode] Hello World\r  (second text event clears line, rewrites with accumulated)
//! [OpenCode] ✓ Step finished... (step_finish shows prefix with newline)
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

// Parser state (RefCell/Cell fields isolated to boundary)
pub mod io;

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::logger::{Colors, CHECK, CROSS};
use std::cell::{Cell, RefCell};
use std::fmt::Write as _;
use std::io::{self, BufRead, Write};
use std::path::Path;
use std::rc::Rc;

use super::delta_display::{DeltaRenderer, TextDeltaRenderer};
use super::health::HealthMonitor;
#[cfg(feature = "test-utils")]
use super::health::StreamingQualityMetrics;
use super::printer::SharedPrinter;
use super::streaming_state::StreamingSession;
use super::terminal::TerminalMode;
use super::types::{format_tool_input, format_unknown_json_event, ContentType};

// Event type definitions
include!("opencode/event_types.rs");

// Parser core: struct definition and constructor methods
include!("opencode/parser_core.rs");

// Parser stream processing: event parsing and stream handling methods
include!("opencode/parser_stream.rs");

// Event formatting methods
include!("opencode/formatting/step.rs");
include!("opencode/formatting/tool.rs");
include!("opencode/formatting/text_and_error.rs");

// Tests
include!("opencode/tests.rs");
