// Delta renderer implementations.
//
// Contains TextDeltaRenderer and ThinkingDeltaRenderer implementations of the DeltaRenderer trait.

/// Default implementation of `DeltaRenderer` for text content.
///
/// Supports true append-only streaming pattern that works correctly under
/// line wrapping and in ANSI-stripping environments.
///
/// - First delta: prefix + content (no newline, stays on current line)
/// - Subsequent deltas: **Parser computes and emits only new suffix**
/// - Completion: single newline via `DeltaRenderer::render_completion`
/// - Sanitizes newlines to spaces (to prevent artificial line breaks)
/// - Applies consistent color formatting
///
/// # Output Pattern
///
/// ## Full Mode (TTY with capable terminal) - Append-Only Pattern
///
/// ```text
/// [ccs-glm] Hello                    <- First delta: prefix + content, NO newline
///  World                             <- Parser emits suffix: " World" (no prefix, no \r)
/// \n                                  <- Completion: single newline
/// ```
///
/// Result: Single logical line that may wrap to multiple terminal rows.
/// Terminal handles wrapping naturally. No cursor movement means wrapping is not an issue.
///
/// ## Full Mode (Legacy Pattern - Deprecated)
///
/// Some parsers not yet implementing append-only may still use `render_subsequent_delta`
/// which rewrites the line with `\r`. This pattern has known issues with wrapping:
///
/// ```text
/// [ccs-glm] Hello                    <- First delta
/// \r[ccs-glm] Hello World            <- Subsequent: carriage return + full rewrite
/// ```
///
/// Issue: When content wraps, `\r` only returns to column 0 of current row, not
/// start of logical line. This causes display corruption.
///
/// ## Basic/None Mode (non-TTY logs)
///
/// In non-TTY modes, per-delta output is suppressed to avoid repeated prefixed
/// lines for partial updates. The parser is responsible for flushing the final
/// accumulated content once at a completion boundary (e.g. `message_stop`).
///
/// ```text
/// [ccs-glm] Hello World\n
/// ```
///
/// # CCS Spam Prevention (Bug Fix)
///
/// This implementation prevents repeated prefixed lines for CCS agents (ccs/codex,
/// ccs/glm) in non-TTY modes. The spam fix is validated with comprehensive regression
/// tests that simulate real-world streaming scenarios:
///
/// - **Ultra-extreme delta counts:** Tests verify no spam with 1000+ deltas per content block
/// - **Multi-turn sessions:** Validates 3+ turns with 200+ deltas each (600+ total)
/// - **All delta types:** Covers text deltas, thinking deltas, and tool input deltas
/// - **Real-world logs:** Tests with production logs containing 12,596 total deltas
///
/// The multi-line pattern (in-place updates) is the industry standard used by
/// Rich, Ink, Bubble Tea, and other production CLI libraries for clean streaming
/// output.
///
/// See regression tests:
/// - `tests/integration_tests/ccs_delta_spam_systematic_reproduction.rs` (systematic reproduction & verification)
/// - `tests/integration_tests/ccs_all_delta_types_spam_reproduction.rs` (1000+ deltas, edge case coverage)
/// - `tests/integration_tests/ccs_extreme_streaming_regression.rs` (500+ deltas per block)
/// - `tests/integration_tests/ccs_streaming_spam_all_deltas.rs` (all delta types)
/// - `tests/integration_tests/ccs_real_world_log_regression.rs` (production log regression)
/// - `tests/integration_tests/ccs_nuclear_full_log_regression.rs` (large captured logs)
/// - `tests/integration_tests/codex_reasoning_spam_regression.rs` (Codex reasoning regression)
/// - `tests/integration_tests/ccs_wrapping_waterfall_reproduction.rs` (wrapping waterfall reproduction)
/// - `tests/integration_tests/ccs_wrapping_comprehensive.rs` (wrapping + append-only behavior)
/// - `tests/integration_tests/ccs_ansi_stripping_console.rs` (ANSI-stripping console behavior)
pub struct TextDeltaRenderer;

impl DeltaRenderer for TextDeltaRenderer {
    fn render_first_delta(
        accumulated: &str,
        prefix: &str,
        colors: Colors,
        terminal_mode: TerminalMode,
    ) -> String {
        // Sanitize content: replace newlines with spaces and collapse multiple whitespace
        // NOTE: No truncation here - allow full content to accumulate during streaming
        let sanitized = sanitize_for_display(accumulated);

        match terminal_mode {
            TerminalMode::Full => {
                // Append-only pattern: prefix + content, NO NEWLINE
                // This allows content to grow on same line without wrapping issues
                format!(
                    "{}[{}]{} {}{}{}",
                    colors.dim(),
                    prefix,
                    colors.reset(),
                    colors.white(),
                    sanitized,
                    colors.reset()
                )
            }
            TerminalMode::Basic | TerminalMode::None => {
                // SUPPRESS per-delta output in non-TTY modes to prevent spam.
                // The accumulated content will be rendered ONCE at completion boundaries
                // (message_stop, content_block_stop) by the parser layer.
                // This prevents repeated prefixed lines in logs and CI output.
                String::new()
            }
        }
    }

    fn render_subsequent_delta(
        _accumulated: &str,
        _prefix: &str,
        _colors: Colors,
        terminal_mode: TerminalMode,
    ) -> String {
        // DEPRECATED: This method implements a carriage return (\r) pattern that FAILS
        // under terminal line wrapping. Parsers implementing the append-only pattern
        // MUST NOT call this method in Full mode.
        //
        // WHY DEPRECATED:
        // - The \r (carriage return) pattern rewrites the full line for each delta
        // - When content exceeds terminal width and wraps to multiple rows, \r only
        //   returns to column 0 of the CURRENT row, not the start of the logical line
        // - This causes orphaned content on wrapped rows, creating a waterfall effect
        //
        // CORRECT PATTERN (used by ClaudeParser, CodexParser):
        // - Parser tracks last rendered content
        // - Parser computes suffix: new_suffix = current[last_rendered.len()..]
        // - Parser emits ONLY the suffix directly (bypassing this method)
        // - No prefix rewrite, no \r, no cursor movement
        //
        // This method returns empty string in Full mode to make tests fail explicitly
        // if parsers incorrectly call it. Use render_first_delta + suffix emission instead.

        match terminal_mode {
            TerminalMode::Full => {
                // CRITICAL: Parsers MUST NOT call this method in Full mode.
                // Return empty string to make incorrect usage visible in tests.
                //
                // If you're seeing this in a test failure, the parser needs to:
                // 1. Track last rendered content in parser state
                // 2. Compute suffix directly: &sanitized[last_rendered.len()..]
                // 3. Emit suffix with format!("{}{}{}",colors.white(), suffix, colors.reset())
                //
                // See ClaudeParser::handle_content_block_delta (lines 173-215) for correct pattern.
                String::new()
            }
            TerminalMode::Basic | TerminalMode::None => {
                // SUPPRESS per-delta output in non-TTY modes to prevent spam.
                // The accumulated content will be rendered ONCE at completion boundaries
                // (message_stop, content_block_stop) by the parser layer.
                // This prevents repeated prefixed lines in logs and CI output.
                String::new()
            }
        }
    }
}

/// Renderer for streaming thinking deltas.
///
/// Supports the same append-only pattern as `TextDeltaRenderer`:
/// - First delta: prefix + "Thinking: " + content (no newline)
/// - Subsequent deltas: **Parser computes and emits only new suffix**
/// - Completion: single newline via `DeltaRenderer::render_completion`
///
/// # Append-Only Pattern
///
/// For true append-only streaming in Full mode, parsers should:
/// 1. Call `render_first_delta` for the first thinking delta (shows prefix + content)
/// 2. Track last rendered content and emit only new suffixes directly (bypass `render_subsequent_delta`)
/// 3. Call `render_completion` when thinking completes (adds final newline)
///
/// This avoids cursor movement and works correctly under terminal wrapping.
///
/// # CCS Spam Prevention (Bug Fix)
///
/// Like `TextDeltaRenderer`, this implementation suppresses per-delta output in non-TTY modes
/// to prevent repeated "[ccs/codex] Thinking:" and "[ccs/glm] Thinking:" lines in logs.
/// The fix is validated with ultra-extreme streaming tests (1000+ thinking deltas).
///
/// See comprehensive regression tests:
/// - `tests/integration_tests/ccs_delta_spam_systematic_reproduction.rs` (NEW: systematic reproduction test)
/// - `tests/integration_tests/ccs_all_delta_types_spam_reproduction.rs` (1000+ deltas, rapid succession, interleaved blocks)
/// - `tests/integration_tests/ccs_extreme_streaming_regression.rs` (500+ deltas per block)
/// - `tests/integration_tests/ccs_streaming_spam_all_deltas.rs` (all delta types)
/// - `tests/integration_tests/codex_reasoning_spam_regression.rs` (original reasoning fix)
pub struct ThinkingDeltaRenderer;

impl DeltaRenderer for ThinkingDeltaRenderer {
    fn render_first_delta(
        accumulated: &str,
        prefix: &str,
        colors: Colors,
        terminal_mode: TerminalMode,
    ) -> String {
        let sanitized = sanitize_for_display(accumulated);

        match terminal_mode {
            TerminalMode::Full => format!(
                "{}[{}]{} {}Thinking: {}{}{}",
                colors.dim(),
                prefix,
                colors.reset(),
                colors.dim(),
                colors.cyan(),
                sanitized,
                colors.reset()
            ),
            TerminalMode::Basic | TerminalMode::None => {
                // SUPPRESS per-delta thinking output in non-TTY modes.
                // Thinking content will be flushed ONCE at completion boundaries
                // (message_stop for Claude, item.completed for Codex).
                String::new()
            }
        }
    }

    fn render_subsequent_delta(
        _accumulated: &str,
        _prefix: &str,
        _colors: Colors,
        terminal_mode: TerminalMode,
    ) -> String {
        // DEPRECATED: This method implements a carriage return (\r) pattern that FAILS
        // under terminal line wrapping. Parsers implementing the append-only pattern
        // MUST NOT call this method in Full mode.
        //
        // WHY DEPRECATED:
        // - The \r (carriage return) pattern rewrites the full line for each delta
        // - When content exceeds terminal width and wraps to multiple rows, \r only
        //   returns to column 0 of the CURRENT row, not the start of the logical line
        // - This causes orphaned content on wrapped rows, creating a waterfall effect
        //
        // CORRECT PATTERN (used by ClaudeParser, CodexParser):
        // - Parser tracks last rendered content for thinking deltas
        // - Parser computes suffix: new_suffix = current[last_rendered.len()..]
        // - Parser emits ONLY the suffix directly (bypassing this method)
        // - No prefix rewrite, no \r, no cursor movement
        //
        // This method returns empty string in Full mode to make tests fail explicitly
        // if parsers incorrectly call it. Use render_first_delta + suffix emission instead.

        match terminal_mode {
            TerminalMode::Full => {
                // CRITICAL: Parsers MUST NOT call this method in Full mode.
                // Return empty string to make incorrect usage visible in tests.
                //
                // If you're seeing this in a test failure, the parser needs to:
                // 1. Track last rendered content in parser state (for thinking deltas)
                // 2. Compute suffix directly: &sanitized[last_rendered.len()..]
                // 3. Emit suffix with format!("{}{}{}",colors.cyan(), suffix, colors.reset())
                //
                // See ClaudeParser::handle_content_block_delta (thinking branch) for correct pattern.
                String::new()
            }
            TerminalMode::Basic | TerminalMode::None => {
                // SUPPRESS per-delta thinking output in non-TTY modes.
                // Thinking content will be flushed ONCE at completion boundaries
                // (message_stop for Claude, item.completed for Codex).
                String::new()
            }
        }
    }
}
