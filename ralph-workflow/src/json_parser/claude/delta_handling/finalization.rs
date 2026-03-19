//! Full mode finalization logic for cursor management and line completion.
//!
//! ## Overview
//!
//! In Full (TTY) mode, delta streaming uses append-only rendering with cursor
//! management. When transitioning between content types (e.g., thinking → text)
//! or at message boundaries, active streaming lines must be finalized to emit
//! completion newlines and reset cursor state.
//!
//! ## Finalization Scenarios
//!
//! 1. **Thinking finalization**: Active thinking block ends, emit completion newline
//! 2. **Text finalization**: Active text streaming line ends, emit completion newline
//! 3. **In-place finalization**: Prefer thinking finalization when active, otherwise text
//!
//! ## Cursor State
//!
//! The parser tracks cursor state with `cursor_up_active` and `text_line_active`.
//! Finalization clears these flags to prevent double-completion or orphaned cursor state.

use crate::json_parser::delta_display::{DeltaRenderer, TextDeltaRenderer};
use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;

impl crate::json_parser::claude::ClaudeParser {
    /// Finalize any active streaming line in Full mode (thinking or text).
    ///
    /// Prefers thinking finalization when active (it owns cursor-up state).
    /// Otherwise finalizes active text streaming line. Defensive fallback ensures
    /// cursor state is cleared even if protocol violations reset higher-level flags.
    pub(in crate::json_parser::claude) fn finalize_in_place_full_mode(
        &self,
        session: &mut StreamingSession,
    ) -> String {
        let terminal_mode = *self.state.terminal_mode.borrow();
        if terminal_mode != TerminalMode::Full {
            return String::new();
        }

        // Prefer thinking finalization when active (it owns the cursor-up state).
        if self.state.thinking_active_index.borrow().is_some() {
            return self.finalize_thinking_full_mode(session);
        }

        // Otherwise, finalize an active text streaming line.
        if *self.state.text_line_active.borrow() {
            *self.state.text_line_active.borrow_mut() = false;
            *self.state.cursor_up_active.borrow_mut() = false;
            return TextDeltaRenderer::render_completion(terminal_mode);
        }

        // Defensive fallback: if the last output left us in an unexpected cursor state
        // (e.g., raw passthrough escape sequences), finalize even if higher-level flags
        // were reset by protocol violations.
        if *self.state.cursor_up_active.borrow() {
            *self.state.cursor_up_active.borrow_mut() = false;
            return TextDeltaRenderer::render_completion(terminal_mode);
        }

        String::new()
    }

    /// Finalize active thinking block in Full mode.
    ///
    /// Emits completion newline so subsequent output doesn't glue onto the thinking line.
    /// Clears `thinking_active_index` and `cursor_up_active` flags.
    pub(in crate::json_parser::claude) fn finalize_thinking_full_mode(
        &self,
        session: &mut StreamingSession,
    ) -> String {
        let terminal_mode = *self.state.terminal_mode.borrow();
        match terminal_mode {
            TerminalMode::Full => {
                let Some(_index) = self.state.thinking_active_index.borrow_mut().take() else {
                    return String::new();
                };
                *self.state.cursor_up_active.borrow_mut() = false;
                // Keep `session` in the signature for symmetry with other finalizers.
                // Thinking finalization is parser-owned state in Full mode.
                let _ = session;
                // Finalize the streamed thinking line.
                // In append-only streaming, this emits the completion newline so subsequent output
                // doesn't glue onto the thinking line.
                <crate::json_parser::delta_display::ThinkingDeltaRenderer as DeltaRenderer>::render_completion(
                    terminal_mode,
                )
            }
            TerminalMode::Basic | TerminalMode::None => {
                let _ = session;
                String::new()
            }
        }
    }
}
