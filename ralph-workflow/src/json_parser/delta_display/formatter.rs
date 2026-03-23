// Delta display formatter.
//
// Contains the DeltaDisplayFormatter for consistent styling across parsers.

/// Delta display formatter
///
/// Formats delta content for user display with consistent styling across all parsers.
pub struct DeltaDisplayFormatter {
    /// Whether to mark partial content visually
    mark_partial: bool,
}

impl DeltaDisplayFormatter {
    /// Create a new formatter with default settings
    #[must_use] 
    pub const fn new() -> Self {
        Self { mark_partial: true }
    }

    /// Format thinking content specifically.
    ///
    /// Thinking content has special formatting to distinguish it from regular text.
    ///
    /// # Terminal Mode Behavior
    ///
    /// - `TerminalMode::Full` / `TerminalMode::Basic`: May include ANSI colors.
    /// - `TerminalMode::None`: MUST be plain text (no ANSI sequences).
    #[must_use]
    pub fn format_thinking(
        &self,
        content: &str,
        prefix: &str,
        colors: Colors,
        terminal_mode: crate::json_parser::terminal::TerminalMode,
    ) -> String {
        use crate::json_parser::terminal::TerminalMode;
        match terminal_mode {
            TerminalMode::None => format!("[{prefix}] Thinking: {content}\n"),
            TerminalMode::Full | TerminalMode::Basic => {
                format_thinking_with_colors(content, prefix, colors, self.mark_partial)
            }
        }
    }

    /// Format tool input specifically
    ///
    /// Tool input is shown with appropriate styling.
    ///
    /// # Terminal Mode Behavior
    ///
    /// - **Full mode (TTY):** Renders the full `[prefix]   └─ content` pattern
    ///   for each delta, providing real-time feedback with clarity about which
    ///   agent's tool is being invoked.
    ///
    /// - **Basic/None modes (non-TTY):** Suppresses per-delta output to prevent
    ///   repeated prefixed lines in logs and CI output. Tool input is accumulated
    ///   and rendered ONCE at completion boundaries (`message_stop`).
    ///
    /// # CCS Spam Prevention (Bug Fix)
    ///
    /// This implementation prevents repeated "[ccs/glm]" and "[ccs/codex]" prefixed
    /// lines for tool input deltas in non-TTY modes. The fix is validated with
    /// comprehensive regression tests that simulate real-world streaming scenarios.
    ///
    /// See comprehensive regression tests:
    /// - `tests/integration_tests/ccs_delta_spam_systematic_reproduction.rs` (NEW: systematic reproduction test)
    /// - `tests/integration_tests/ccs_all_delta_types_spam_reproduction.rs` (comprehensive coverage)
    /// - `tests/integration_tests/ccs_nuclear_spam_test.rs` (tool input with 500+ deltas)
    /// - `tests/integration_tests/ccs_streaming_spam_all_deltas.rs` (all delta types including tool input)
    ///
    /// # Future Enhancement
    ///
    /// For streaming tool inputs with multiple deltas in Full mode, consider suppressing
    /// the `[prefix]` on continuation lines to reduce visual noise:
    /// - First tool input line: `[prefix] Tool: name`
    /// - Continuation: `           └─ more input` (aligned, no prefix)
    ///
    /// This would require tracking whether the prefix has been displayed
    /// for the current tool block, likely via the streaming session state.
    #[must_use]
    pub fn format_tool_input(
        &self,
        content: &str,
        prefix: &str,
        colors: Colors,
        terminal_mode: crate::json_parser::terminal::TerminalMode,
    ) -> String {
        use crate::json_parser::terminal::TerminalMode;
        match terminal_mode {
            // In Full mode, render tool input deltas as they arrive for real-time feedback
            TerminalMode::Full => format_tool_input_full(content, prefix, colors, self.mark_partial),
            // SUPPRESS per-delta tool input in non-TTY modes.
            // Tool input will be rendered ONCE at tool completion or message_stop.
            TerminalMode::Basic | TerminalMode::None => String::new(),
        }
    }
}

fn format_thinking_with_colors(
    content: &str,
    prefix: &str,
    colors: Colors,
    mark_partial: bool,
) -> String {
    if mark_partial {
        format!(
            "{}[{}]{} {}Thinking: {}{}{}\n",
            colors.dim(),
            prefix,
            colors.reset(),
            colors.dim(),
            colors.cyan(),
            content,
            colors.reset()
        )
    } else {
        format!(
            "{}[{}]{} {}Thinking: {}{}{}\n",
            colors.dim(),
            prefix,
            colors.reset(),
            colors.cyan(),
            colors.reset(),
            content,
            colors.reset()
        )
    }
}

fn format_tool_input_full(
    content: &str,
    prefix: &str,
    colors: Colors,
    mark_partial: bool,
) -> String {
    if mark_partial {
        format!(
            "{}[{}]{} {}  └─ {}{}{}\n",
            colors.dim(),
            prefix,
            colors.reset(),
            colors.dim(),
            colors.reset(),
            content,
            colors.reset()
        )
    } else {
        format!(
            "{}[{}]{} {}  └─ {}{}\n",
            colors.dim(),
            prefix,
            colors.reset(),
            colors.reset(),
            content,
            colors.reset()
        )
    }
}

impl Default for DeltaDisplayFormatter {
    fn default() -> Self {
        Self::new()
    }
}
