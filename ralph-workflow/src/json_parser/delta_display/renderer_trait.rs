// Delta renderer trait definition.
//
// Contains the DeltaRenderer trait and compute_append_only_suffix helper.
//
// # CCS Spam Prevention Architecture
//
// This module implements a three-layer approach to prevent repeated prefixed lines
// for streaming deltas in non-TTY modes (logs, CI output):

#[cfg(any(test, debug_assertions))]
use std::io::Write;
//
// ## Layer 1: Suppression at Renderer Level
//
// Delta renderers (`TextDeltaRenderer`, `ThinkingDeltaRenderer`) return empty strings
// in `TerminalMode::Basic` and `TerminalMode::None` for both `render_first_delta` and
// `render_subsequent_delta`. This prevents per-delta spam at the source.
//
// ## Layer 2: Accumulation in StreamingSession
//
// `StreamingSession` (in `streaming_state/session`) accumulates all content by
// (ContentType, index) across deltas. This state is preserved across all delta
// events for a single message.
//
// ## Layer 3: Flush at Completion Boundaries
//
// Parser layer (ClaudeParser, CodexParser) flushes accumulated content ONCE at
// completion boundaries:
// - ClaudeParser: `handle_message_stop` (in `claude/delta_handling.rs`)
// - CodexParser: `item.completed` handlers (in `codex/event_handlers/*.rs`)
//
// This ensures:
// - **Full mode (TTY)**: Real-time append-only streaming (no cursor movement)
// - **Basic/None modes**: One prefixed line per content block, regardless of delta count
//
// ## Validation
//
// Regression tests validate this architecture:
// - `ccs_delta_spam_systematic_reproduction.rs`: systematic reproduction (all delta types, both parsers, both modes)
// - `ccs_all_delta_types_spam_reproduction.rs`: 1000+ deltas per block
// - `ccs_streaming_spam_all_deltas.rs`: all delta types (text/thinking/tool)
// - `ccs_nuclear_full_log_regression.rs`: large captured logs (thousands of deltas)
// - `ccs_streaming_edge_cases.rs`: edge cases (empty deltas, rapid transitions)
// - `ccs_wrapping_waterfall_reproduction.rs`: wrapping/cursor-up failure reproduction
// - `ccs_ansi_stripping_console.rs`: ANSI-stripping console behavior
// - `codex_reasoning_spam_regression.rs`: Codex reasoning regression

/// Renderer for streaming delta content.
///
/// This trait defines the contract for rendering streaming deltas consistently
/// across all parsers using the append-only pattern.
///
/// # Append-Only Pattern (Full Mode)
///
/// The renderer supports true append-only streaming that works correctly under
/// terminal line wrapping and in ANSI-stripping environments:
///
/// 1. **First delta**: Shows prefix with accumulated content, NO newline
///    - Example: `[ccs/glm] Hello`
///    - No cursor movement, content stays on current line
///
/// 2. **Subsequent deltas**: Parser computes and emits ONLY new suffix
///    - Parser responsibility: track last rendered content and emit only delta
///    - Example: parser emits ` World` (just the new text with color codes)
///    - NO prefix rewrite, NO `\r` (carriage return), NO cursor movement
///    - Renderers provide `render_subsequent_delta` for backward compatibility
///      but parsers implementing append-only should bypass it
///
/// 3. **Completion**: Single newline to finalize the line
///    - Example: `\n`
///    - Moves cursor to next line after streaming completes
///
/// This pattern works correctly even when content wraps to multiple terminal rows
/// because there is NO cursor movement. The terminal naturally handles wrapping,
/// and content appears to grow incrementally on the same logical line.
///
/// # Why Append-Only?
///
/// Previous patterns using `\r` (carriage return) or `\n\x1b[1A` (newline + cursor up)
/// fail in two scenarios:
///
/// 1. **Line wrapping**: When content exceeds terminal width and wraps to multiple rows,
///    `\r` only returns to column 0 of current row (not start of logical line), and
///    `\x1b[1A` (cursor up 1 row) + `\x1b[2K` (clear 1 row) cannot erase all wrapped rows
/// 2. **ANSI-stripping consoles**: Many CI/log environments strip or ignore ANSI sequences,
///    so `\n` becomes a literal newline causing waterfall spam
///
/// Append-only streaming eliminates both issues by never using cursor movement.
///
/// # Non-TTY Modes (Basic/None)
///
/// Per-delta output is suppressed. Content is flushed ONCE at completion boundaries
/// by the parser layer to prevent spam in logs and CI output.
///
/// # Rendering Rules
///
/// - `render_first_delta()`: Called for the first delta of a content block
///   - Must include prefix
///   - Must NOT include newline (stays on current line for append-only)
///   - Shows the accumulated content so far
///
/// - `render_subsequent_delta()`: Called for subsequent deltas
///   - **Parsers implementing append-only MUST compute the suffix and bypass this method**
///   - Renderer implementations in this repo intentionally return empty strings in all modes
///     to avoid reintroducing cursor/CR patterns.
///
/// - `render_completion()`: Called when streaming completes
///   - Returns single newline (`\n`) in Full mode to finalize the line
///   - Returns empty string in Basic/None mode (parser already flushed with newline)
///
/// # Terminal Mode Awareness
///
/// The renderer automatically adapts output based on terminal capability:
/// - **Full mode**: Append-only streaming (no cursor movement during deltas)
/// - **Basic mode**: Per-delta output suppressed; parser flushes once at completion
/// - **None mode**: Per-delta output suppressed; parser flushes once at completion, plain text
///
/// # Example
///
/// ```ignore
/// use crate::json_parser::delta_display::DeltaRenderer;
/// use crate::logger::Colors;
/// use crate::json_parser::TerminalMode;
///
/// let colors = Colors { enabled: true };
/// let terminal_mode = TerminalMode::detect();
///
/// // First chunk
/// let output = DeltaRenderer::render_first_delta(
///     "Hello",
///     "ccs-glm",
///     colors,
///     terminal_mode
/// );
///
/// // Second chunk
/// let output = DeltaRenderer::render_subsequent_delta(
///     "Hello World",
///     "ccs-glm",
///     colors,
///     terminal_mode
/// );
///
/// // Complete
/// let output = DeltaRenderer::render_completion(terminal_mode);
/// ```
pub trait DeltaRenderer {
    /// Render the first delta of a content block.
    ///
    /// This is called when streaming begins for a new content block.
    /// The output should include the prefix and the accumulated content.
    ///
    /// # Arguments
    /// * `accumulated` - The full accumulated content so far
    /// * `prefix` - The agent prefix (e.g., "ccs-glm")
    /// * `colors` - Terminal colors
    /// * `terminal_mode` - The detected terminal capability mode
    ///
    /// # Returns
    /// A formatted string with prefix and content.
    ///
    /// In Full mode, this MUST NOT include a trailing newline or any cursor movement.
    /// (Append-only streaming keeps the cursor on the current line until completion.)
    ///
    /// In Basic/None modes, returns an empty string (per-delta output is suppressed; the parser
    /// flushes the final newline-terminated content at completion boundaries).
    fn render_first_delta(
        accumulated: &str,
        prefix: &str,
        colors: Colors,
        terminal_mode: TerminalMode,
    ) -> String;

    /// Render a subsequent delta (in-place update).
    ///
    /// This is called for all deltas after the first. The output should
    /// clear the entire line and rewrite with the prefix and accumulated content
    /// in Full mode, or append content in Basic/None mode.
    ///
    /// # Arguments
    /// * `accumulated` - The full accumulated content so far
    /// * `prefix` - The agent prefix (e.g., "ccs-glm")
    /// * `colors` - Terminal colors
    /// * `terminal_mode` - The detected terminal capability mode
    ///
    /// # Returns
    /// A formatted string representing the delta.
    ///
    /// In the append-only contract, parsers should NOT call this method in Full mode; they should
    /// compute the new suffix and emit it directly. The default renderer implementations return
    /// empty strings to make incorrect usage obvious.
    ///
    /// In Basic/None modes, this returns an empty string (per-delta output is suppressed).
    fn render_subsequent_delta(
        accumulated: &str,
        prefix: &str,
        colors: Colors,
        terminal_mode: TerminalMode,
    ) -> String;

    /// Render the completion of streaming.
    ///
    /// This is called when streaming completes to finalize the line.
    /// In Full mode with append-only pattern, this emits a single newline to complete the line.
    ///
    /// The streamed content is already visible on the terminal from previous deltas.
    /// This method simply adds the final newline for proper line termination.
    ///
    /// # Arguments
    /// * `terminal_mode` - The detected terminal capability mode
    ///
    /// # Returns
    /// A string with appropriate completion sequence for the terminal mode.
    #[must_use]
    fn render_completion(terminal_mode: TerminalMode) -> String {
        match terminal_mode {
            TerminalMode::Full => "\n".to_string(), // Single newline at end for append-only pattern
            // In non-TTY modes, streamed output is suppressed and the parser flushes
            // newline-terminated content at completion boundaries. Returning a newline here
            // would add an extra blank line if a caller invokes `render_completion`.
            TerminalMode::Basic | TerminalMode::None => String::new(),
        }
    }
}

/// Compute the append-only suffix to emit for a snapshot-style accumulated string.
///
/// Providers differ in what they send as a "delta": some stream true incremental suffixes,
/// others send the full accumulated content repeatedly (snapshot-style). Our append-only
/// rendering contract treats the parser's sanitized accumulated content as the source of truth.
///
/// Given the last rendered sanitized content and the current sanitized content, return
/// the string that should be appended to the terminal to advance the visible output.
///
/// Rules:
/// - If `last_rendered` is empty, emit `current` (first delta).
/// - If `current` starts with `last_rendered`, emit the new suffix only.
/// - Otherwise, treat as a discontinuity/reset and emit an empty suffix.
///
/// ## Why discontinuities emit nothing
///
/// In an append-only renderer, emitting `current` on a discontinuity would append an entire
/// replacement snapshot onto already-rendered output, producing duplicated/corrupted display.
/// Callers that need to surface a reset must do so explicitly (e.g., finalize the current line
/// and start a new one).
///
/// ## Discontinuity Detection
///
/// A discontinuity occurs when `current` does not start with `last_rendered` (i.e.,
/// `current.strip_prefix(last_rendered)` returns `None`). This indicates:
/// - Non-monotonic deltas from the provider (e.g., "Hello World" followed by "Hello Universe")
/// - Protocol violations where content changes unexpectedly
/// - Content resets that should be handled explicitly by the caller
///
/// When a discontinuity is detected, this function returns an empty string. Callers should
/// detect this condition (when both `last_rendered` and `current` are non-empty but the
/// result is empty) and emit appropriate warnings or metrics to track provider behavior.
#[must_use]
pub fn compute_append_only_suffix<'a>(last_rendered: &str, current: &'a str) -> &'a str {
    if last_rendered.is_empty() {
        return current;
    }

    let suffix = current.strip_prefix(last_rendered).unwrap_or_default();

    // Debug assertion to help detect unexpected discontinuities during development
    #[cfg(debug_assertions)]
    if suffix.is_empty() && !current.is_empty() && !last_rendered.is_empty() {
        let _ = writeln!(
            std::io::stderr(),
            "Debug: Delta discontinuity detected in compute_append_only_suffix. \
             Last rendered: {:?} (len={}), Current: {:?} (len={}). \
             This may indicate non-monotonic deltas from the provider.",
            &last_rendered[..last_rendered.len().min(50)],
            last_rendered.len(),
            &current[..current.len().min(50)],
            current.len()
        );
    }

    suffix
}
