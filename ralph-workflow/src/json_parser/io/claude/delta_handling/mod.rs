//! # Delta Handling
//!
//! Claude streaming delta processing with CCS spam prevention.
//!
//! ## Overview
//!
//! This module implements the delta handling system for Claude's streaming API,
//! with sophisticated CCS spam prevention that eliminates repeated prefixed lines
//! in non-TTY modes (logs, CI output).
//!
//! ## CCS Spam Prevention (Critical Fix)
//!
//! The spam bug occurred because delta renderers emitted one line per delta in
//! non-TTY modes, resulting in hundreds of repeated "[ccs/glm]" lines for a
//! single streamed message.
//!
//! ### Fix Architecture
//!
//! 1. **Suppression:** Delta renderers (`TextDeltaRenderer`, `ThinkingDeltaRenderer`)
//!    return empty strings in non-TTY modes (Basic/None) to suppress per-delta output.
//!
//! 2. **Accumulation:** `StreamingSession` accumulates content by (`ContentType`, index)
//!    across all deltas for text, thinking, and tool input.
//!
//! 3. **Flush:** `handle_message_stop` flushes accumulated content ONCE at
//!    completion boundaries, emitting a single prefixed line per content block.
//!
//! ## Delta Processing Flow
//!
//! ```text
//! content_block_delta → accumulate → [Full: append-only | Basic/None: suppress]
//!                    ↓
//!         message_stop → flush accumulated → single output per block
//! ```
//!
//! ## Modules
//!
//! - `content_blocks`: Content block delta handling (text, thinking, tool use)
//! - `messages`: Message-level handling (text deltas, message stop, flush logic)
//! - `errors`: Error event handling
//!
//! ## Validation
//!
//! The fix is validated with comprehensive regression tests covering ultra-extreme
//! scenarios (1000+ deltas per block, multi-turn sessions, all delta types).
//!
//! See regression tests:
//! - `tests/integration_tests/ccs_delta_spam_systematic_reproduction.rs`
//! - `tests/integration_tests/ccs_all_delta_types_spam_reproduction.rs`
//! - `tests/integration_tests/ccs_nuclear_full_log_regression.rs`
//! - `tests/integration_tests/ccs_streaming_edge_cases.rs`
//! - `tests/integration_tests/ccs_extreme_streaming_regression.rs`
//! - `tests/integration_tests/ccs_streaming_spam_all_deltas.rs`
//! - `tests/integration_tests/ccs_real_world_log_regression.rs`
//! - `tests/integration_tests/codex_reasoning_spam_regression.rs`
//!
//! ## See Also
//!
//! - `StreamingSession` - Content accumulation and deduplication
//! - `TextDeltaRenderer`, `ThinkingDeltaRenderer` - Delta rendering with mode-aware suppression
//! - `delta_display` - Display formatting utilities

mod content_blocks;
mod errors;
mod messages;
