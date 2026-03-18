//! Unified streaming state tracking module.
//!
//! This module provides a single source of truth for streaming state across
//! all parsers (`Claude`, `Codex`, `Gemini`, `OpenCode`). It implements the streaming
//! contract:
//!
//! # Streaming Contract
//!
//! 1. **Delta contract**: Each streaming event contains only newly generated text
//! 2. **Message lifecycle**: `MessageStart` → (`ContentBlockStart` + deltas)* → `MessageStop`
//! 3. **Deduplication rule**: Content displayed during streaming is never re-displayed
//! 4. **State reset**: Streaming state resets on `MessageStart`/Init events
//!
//! # Message Lifecycle
//!
//! The streaming message lifecycle follows this sequence:
//!
//! 1. **`MessageStart`** (or equivalent init event):
//!    - Resets all streaming state to `Idle`
//!    - Clears accumulated content
//!    - Resets content block state
//!
//! 2. **`ContentBlockStart`** (optional, for each content block):
//!    - If already in a block with `started_output=true`, finalizes the previous block
//!    - Initializes tracking for the new block index
//!    - Clears any accumulated content for this block index
//!
//! 3. **Text/Thinking deltas** (zero or more per block):
//!    - First delta for each `(content_type, index)` shows prefix
//!    - Subsequent deltas update in-place (with prefix, using carriage return)
//!    - Sets `started_output=true` for the current block
//!
//! 4. **`MessageStop`**:
//!    - Finalizes the current content block
//!    - Marks message as displayed to prevent duplicate final output
//!    - Returns whether content was streamed (for emitting completion newline)
//!
//! # Content Block Transitions
//!
//! When transitioning between content blocks (e.g., block 0 → block 1):
//!
//! ```ignore
//! // Streaming "Hello" in block 0
//! session.on_text_delta(0, "Hello");  // started_output = true
//!
//! // Transition to block 1
//! session.on_content_block_start(1);  // Finalizes block 0, started_output was true
//!
//! // Stream "World" in block 1
//! session.on_text_delta(1, "World");  // New block, shows prefix again
//! ```
//!
//! The `ContentBlockState::InBlock { index, started_output }` tracks:
//! - `index`: Which block is currently active
//! - `started_output`: Whether any content was output for this block
//!
//! This state enables proper finalization of previous blocks when new ones start.
//!
//! # Delta Contract
//!
//! This module enforces a strict **delta contract** - all streaming events must
//! contain only the newly generated text (deltas), not the full accumulated content.
//!
//! Treating snapshots as deltas causes exponential duplication bugs. The session
//! validates that incoming content is genuinely delta-sized and rejects likely
//! snapshot-as-delta violations.
//!
//! # Example
//!
//! ```ignore
//! use crate::json_parser::streaming_state::{StreamingSession, StreamingState};
//!
//! let mut session = StreamingSession::new();
//!
//! // Message starts - reset state
//! session.on_message_start();
//!
//! // Content block starts
//! session.on_content_block_start(0);
//!
//! // Text deltas arrive - accumulate and display
//! let should_show_prefix = session.on_text_delta(0, "Hello");
//! assert!(should_show_prefix); // First chunk shows prefix
//!
//! let should_show_prefix = session.on_text_delta(0, " World");
//! assert!(!should_show_prefix); // Subsequent chunks don't show prefix
//!
//! // Check if content was already streamed (for deduplication)
//! assert!(session.has_any_streamed_content());
//!
//! // Message stops - finalize
//! session.on_message_stop();
//! ```

// Re-export ContentType from types module for use in sub-modules
use crate::json_parser::types::ContentType;

// Contract types: constants, thresholds, and state enums
include!("streaming_state/contract.rs");

// Session implementation: StreamingSession struct and all methods
include!("streaming_state/session.rs");

// Tests: all unit tests for streaming state functionality
include!("streaming_state/tests.rs");
