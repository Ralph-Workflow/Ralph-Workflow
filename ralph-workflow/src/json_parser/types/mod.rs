//! Shared types and utilities for NDJSON stream parsers.
//!
//! This module defines the event types emitted by AI agent CLIs during streaming
//! execution. Each agent (Claude, Codex, Gemini, `OpenCode`) outputs NDJSON (newline-delimited
//! JSON) with agent-specific event schemas that get normalized into these types.

mod accumulator;
mod claude;
mod codex;
mod formatting;
mod gemini;

pub use accumulator::{ContentType, DeltaAccumulator};
pub use formatting::{
    determine_output_cutoff, format_cost_suffix, format_dim_continuation_line,
    format_duration_for_display, format_short_hash, format_token_counts, format_tokens_suffix,
    format_tool_input, format_unknown_json_event, normalize_blank_lines,
};

pub type AssistantMessage = claude::AssistantMessage;
pub type ClaudeEvent = claude::ClaudeEvent;
pub type ContentBlock = claude::ContentBlock;
pub type ContentBlockDelta = claude::ContentBlockDelta;
pub type MessageDeltaData = claude::MessageDeltaData;
pub type MessageUsage = claude::MessageUsage;
pub type StreamError = claude::StreamError;
pub type StreamInnerEvent = claude::StreamInnerEvent;
pub type UserMessage = claude::UserMessage;

pub type CodexEvent = codex::CodexEvent;
pub type CodexItem = codex::CodexItem;
pub type CodexUsage = codex::CodexUsage;

pub type GeminiEvent = gemini::GeminiEvent;
pub type GeminiStats = gemini::GeminiStats;
