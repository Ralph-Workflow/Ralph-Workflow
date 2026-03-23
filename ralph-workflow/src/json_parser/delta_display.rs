//! Unified delta display system for streaming content.
//!
//! This module provides centralized logic for displaying partial vs. complete
//! content consistently across all parsers. It handles visual distinction,
//! real-time streaming display, and automatic transition from delta to complete.
//!
//! # Streaming Architecture: True Append-Only Pattern
//!
//! This module implements ChatGPT-style real-time streaming using a true append-only
//! pattern in Full mode (TTY with ANSI support):
//!
//! ## True Append-Only Pattern (Full Mode)
//!
//! ```text
//! [ccs/glm] Hello           <- First delta: prefix + content, NO newline, NO cursor movement
//!  World                    <- Parser emits suffix: " World" (no prefix, no control codes)
//! !                          <- Parser emits suffix: "!" (just the new character)
//! \n                         <- Completion: single newline finalizes line
//! ```
//!
//! **Key insight**: NO cursor movement during streaming. Content grows naturally on the
//! same line, terminal handles wrapping automatically.
//!
//! ## Why True Append-Only?
//!
//! Previous implementations used cursor movement patterns that break under wrapping:
//!
//! ### Pattern 1: Newline + Cursor Up (BROKEN)
//! ```text
//! [ccs/glm] Hello\n\x1b[1A              <- newline + cursor up 1
//! \x1b[2K\r[ccs/glm] Hello World\n\x1b[1A  <- clear line + rewrite + cursor up 1
//! ```
//! **Problem**: When content wraps to N rows, cursor-up-1 and clear-1-line cannot erase
//! N-1 rows above. Orphaned wrapped content remains visible, creating waterfall effect.
//!
//! ### Pattern 2: Carriage Return (BROKEN)
//! ```text
//! [ccs/glm] Hello                    <- First delta
//! \r[ccs/glm] Hello World            <- Carriage return + full rewrite
//! ```
//! **Problem**: When content wraps to multiple rows, `\r` only returns to column 0 of
//! current row, not start of logical line. Rewrite corrupts wrapped rows above.
//!
//! ### Pattern 3: True Append-Only (CORRECT)
//! ```text
//! [ccs/glm] Hello                    <- First delta (prefix + content)
//!  World                             <- Suffix only (no prefix, no cursor movement)
//! ```
//! **Advantage**: No cursor movement means wrapping is not an issue. Terminal naturally
//! handles wrapping, content appears to grow on same logical line.
//!
//! ## Why Previous Patterns Failed
//!
//! 1. **Line wrapping breaks cursor positioning**
//!    - When content exceeds terminal width, it wraps to multiple physical rows
//!    - `\x1b[2K` (clear line) only clears ONE row, not all wrapped rows
//!    - `\x1b[1A` (cursor up 1) moves up ONE row, leaving orphaned wrapped rows
//!    - `\r` (carriage return) only moves to column 0 of CURRENT row, not start of logical line
//!    - Result: multi-line waterfall effect instead of in-place update
//!
//! 2. **ANSI-stripping consoles see literal newlines**
//!    - Many CI/log consoles strip or ignore ANSI escape sequences
//!    - The `\n` from `\n\x1b[1A` becomes a real visible newline
//!    - Cursor positioning is ignored, creating repeated prefixed lines
//!
//! 3. **Width-aware solutions are fragile**
//!    - Tracking terminal width and computing row counts is complex
//!    - Different terminals handle wrapping differently (soft vs hard wraps)
//!    - Resizing during streaming breaks assumptions
//!    - Difficult to test all edge cases
//!
//! ## True Append-Only Advantages
//!
//! 1. **Works with wrapping**: No cursor movement means wrapping is handled naturally by terminal
//! 2. **No ANSI cursor positioning**: Uses only basic output, works in all environments
//! 3. **Simple and robust**: One pattern works for all terminal widths and content lengths
//! 4. **Real-time streaming**: Each suffix delta immediately appends to visible line
//! 5. **ANSI-stripping safe**: Even if ANSI is stripped, content still appears correctly
//!
//! ## Terminal Mode Behavior
//!
//! The renderer automatically detects terminal capability and adjusts output:
//!
//! - **Full mode (TTY)**: True append-only streaming with NO cursor movement during deltas
//!   - First delta: emit prefix + content (no newline)
//!   - Subsequent deltas: parser emits only new suffix (no prefix, no control codes)
//!   - Completion: single `\n` to finalize the line
//!   - Works correctly even when content wraps to multiple rows
//!
//! - **Basic/None mode (logs, CI)**: Per-delta output suppressed, flush once at completion
//!   - No per-delta output during streaming (prevents spam in logs)
//!   - Accumulated content flushed ONCE at completion boundaries
//!   - No ANSI sequences in None mode (pipes, redirects, CI environments)
//!
//! # Prefix Display Strategy
//!
//! In the append-only streaming contract, the prefix (e.g., `[ccs-glm]`) is displayed **once**
//! per streamed content block:
//!
//! - **Full mode (TTY)**: prefix is emitted on the first delta only; subsequent deltas append
//!   only the new suffix (no prefix, no cursor movement).
//! - **Basic/None (non-TTY)**: per-delta output is suppressed; the parser flushes the final
//!   accumulated content once at completion boundaries, producing one prefix per block.
//!
//! ## Prefix Debouncing (test-only)
//!
//! Historically, in-place/cursor-up implementations re-rendered the full line (including the
//! prefix) on each delta. For experimentation in tests, `PrefixDebouncer` can control how often
//! a legacy renderer would re-emit the prefix.
//!
//! Current default behavior for the debouncer is **first delta only** (no repeated prefixes)
//! unless a count- or time-threshold is configured.

use crate::json_parser::terminal::TerminalMode;
use crate::logger::Colors;

#[cfg(test)]
use std::time::{Duration, Instant};

// Display utilities (constants and sanitize function)
include!("delta_display/display_utils.rs");

// Prefix debouncing (test-only)
include!("delta_display/debouncer.rs");

// Delta renderer trait and compute_append_only_suffix helper
include!("delta_display/renderer_trait.rs");

// Delta renderer implementations (TextDeltaRenderer, ThinkingDeltaRenderer)
include!("delta_display/renderer_impls.rs");

// Delta display formatter
include!("delta_display/formatter.rs");

// Tests
#[cfg(test)]
#[path = "delta_display/io_tests/mod.rs"]
mod tests;
