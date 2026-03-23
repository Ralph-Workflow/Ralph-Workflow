//! JSON Stream Parsing Module
//!
//! Functions for parsing NDJSON (newline-delimited JSON)
//! streams from Claude, Codex, Gemini, and `OpenCode` CLI tools.
//!
//! NDJSON is a format where each line contains a complete JSON object.
//! Agent CLIs emit NDJSON streams for real-time event processing.
//!
//! This module uses serde for JSON parsing, which is ~100x faster
//! than spawning jq for each event.
//!
//! # Key Types
//!
//! - [`ClaudeParser`] - Parser for Claude CLI NDJSON output
//! - [`CodexParser`] - Parser for `OpenAI` Codex CLI NDJSON output
//! - [`GeminiParser`] - Parser for Google Gemini CLI NDJSON output
//! - [`OpenCodeParser`] - Parser for `OpenCode` CLI NDJSON output
//!
//! Parser selection is controlled by [`crate::agents::JsonParserType`].
//!
//! # Module Structure
//!
//! - [`types`] - Shared types and event structures
//! - [`claude`] - Claude CLI output parser (with streaming support)
//! - [`codex`] - `OpenAI` Codex CLI output parser (with streaming support)
//! - [`gemini`] - Google Gemini CLI output parser (with streaming support)
//! - [`opencode`] - `OpenCode` CLI output parser (with streaming support)
//! - [`health`] - Parser health monitoring and graceful degradation
//! - [`printer`] - Test utilities for output verification (`test-utils` feature)
//!
//! # Streaming Support
//!
//! All parsers now support delta streaming for real-time content display:
//! - **Claude**: Full streaming with `DeltaAccumulator` for text and thinking deltas
//! - **Gemini**: Streaming with delta flag support for message content
//! - **Codex**: Streaming for `agent_message` and reasoning item types
//! - **`OpenCode`**: Streaming for text events
//!
//! In verbose mode, parsers show full accumulated content. In normal mode,
//! they show real-time deltas for immediate feedback.
//!
//! ## Verbosity Levels
//!
//! The parsers respect the configured verbosity level:
//! - **Quiet (0)**: Minimal output, aggressive truncation
//! - **Normal (1)**: Balanced output with moderate truncation, shows real-time deltas
//! - **Verbose (2)**: Default - shows more detail including tool inputs and full accumulated text
//! - **Full (3)**: No truncation, show all content
//! - **Debug (4)**: Maximum verbosity, includes raw JSON output
//!
//! # Internal Modules
//!
//! The `stream_classifier` module is internal and handles automatic detection
//! of streaming vs. non-streaming output. It is not exposed in the public API.
//!
//! For detailed streaming contract documentation, see `README.md` in this directory.

pub mod boundary;
pub mod claude;
#[cfg(test)]
mod claude_io_tests;
pub mod codex;
#[cfg(test)]
mod codex_tests;
pub mod deduplication;
pub mod delta_display;
mod event_queue;
pub mod gemini;
#[cfg(test)]
mod gemini_tests;
pub mod health;
pub mod incremental_parser;
pub mod opencode;
#[cfg(test)]
mod opencode_tests;
pub mod printer;
pub mod stream_classifier;
mod streaming_state;
pub mod terminal;
pub mod types;

pub use terminal::TerminalMode;

pub type ClaudeParser = claude::ClaudeParser;
pub type CodexParser = codex::CodexParser;
pub type GeminiParser = gemini::GeminiParser;
pub type OpenCodeParser = opencode::OpenCodeParser;

#[cfg(test)]
mod tests;
