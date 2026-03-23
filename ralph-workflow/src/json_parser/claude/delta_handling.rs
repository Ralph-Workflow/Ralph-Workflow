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
//! - `finalization`: Full mode finalization logic (cursor management, thinking/text line finalization)
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

#[cfg(any(test, debug_assertions))]
use std::io::Write;

use crate::json_parser::delta_display::{
    compute_append_only_suffix, sanitize_for_display, DeltaDisplayFormatter, DeltaRenderer,
    TextDeltaRenderer,
};
use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::{ContentBlockDelta, ContentType};

impl crate::json_parser::claude::ClaudeParser {
    /// Handle error events from the streaming API.
    ///
    /// Formats error message with agent prefix and red color in TTY modes.
    pub(in crate::json_parser::claude) fn handle_error_event(
        &self,
        err: crate::json_parser::types::StreamError,
    ) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        let msg = err
            .message
            .unwrap_or_else(|| "Unknown streaming error".to_string());
        format!(
            "{}[{}]{} {}Error: {}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.red(),
            msg,
            c.reset()
        )
    }

    /// Handle unknown event types.
    ///
    /// In debug mode, logs unknown event with agent prefix.
    /// In production mode, suppresses output to avoid noise.
    pub(in crate::json_parser::claude) fn handle_unknown_event(&self) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        // Unknown stream event - in debug mode, log it
        if self.verbosity.is_debug() {
            format!(
                "{}[{}]{} {}Unknown streaming event{}\n",
                c.dim(),
                prefix,
                c.reset(),
                c.dim(),
                c.reset()
            )
        } else {
            String::new()
        }
    }
}

#[cfg(debug_assertions)]
fn log_delta_discontinuity_if_detected(
    index: u64,
    last_rendered: &str,
    sanitized: &str,
    new_suffix: &str,
    block_type: &str,
) {
    if new_suffix.is_empty() && !last_rendered.is_empty() && !sanitized.is_empty() {
        let _ = writeln!(
            std::io::stderr(),
            "Warning: Delta discontinuity detected for {block_type} block {index}. \
             Provider sent non-monotonic content. \
             Last: {:?} (len={}), Current: {:?} (len={})",
            &last_rendered[..last_rendered.len().min(40)],
            last_rendered.len(),
            &sanitized[..sanitized.len().min(40)],
            sanitized.len()
        );
    }
}

/// Format tool input for display (convert non-string values to JSON).
fn format_tool_input(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::String(s) => s.clone(),
        other => serde_json::to_string_pretty(other).unwrap_or_else(|_| "{}".to_string()),
    }
}

impl crate::json_parser::claude::ClaudeParser {
    fn update_last_rendered(&self, key: &str, value: &str) {
        self.state
            .last_rendered_content
            .borrow_mut()
            .insert(key.to_string(), value.to_string());
    }

    fn get_last_rendered(&self, key: &str) -> String {
        self.state
            .last_rendered_content
            .borrow()
            .get(key)
            .cloned()
            .unwrap_or_default()
    }

    /// Handle content block delta events (text, thinking, tool use).
    ///
    /// # Arguments
    ///
    /// * `session` - Mutable session for accumulation and deduplication
    /// * `index` - Content block index from the streaming API
    /// * `delta` - Delta content (text, thinking, or tool use)
    ///
    /// # Returns
    ///
    /// Formatted output string (may be empty if suppressed or deduplicated)
    pub(in crate::json_parser::claude) fn handle_content_block_delta(
        &self,
        session: &mut StreamingSession,
        index: u64,
        delta: ContentBlockDelta,
    ) -> String {
        // If an assistant event fully rendered this message before streaming started,
        // suppress ALL subsequent streaming deltas and avoid accumulating them.
        //
        // Rationale: if we keep accumulating deltas, the non-TTY flush at `message_stop`
        // would re-emit already-rendered content.
        if session
            .get_current_message_id()
            .is_some_and(|message_id| session.is_message_pre_rendered(message_id))
        {
            return String::new();
        }
        self.dispatch_delta(session, index, delta)
    }

    fn dispatch_delta(
        &self,
        session: &mut StreamingSession,
        index: u64,
        delta: ContentBlockDelta,
    ) -> String {
        match delta {
            ContentBlockDelta::TextDelta { text: Some(text) } => {
                self.handle_text_block_delta(session, index, &text)
            }
            ContentBlockDelta::ThinkingDelta {
                thinking: Some(text),
            } => self.handle_thinking_block_delta(session, index, &text),
            ContentBlockDelta::ToolUseDelta {
                tool_use: Some(tool_delta),
            } => self.handle_tool_use_block_delta(session, index, &tool_delta),
            _ => String::new(),
        }
    }

    /// Handle a text content block delta.
    fn handle_text_block_delta(
        &self,
        session: &mut StreamingSession,
        index: u64,
        text: &str,
    ) -> String {
        let thinking_finalize = self.finalize_thinking_full_mode(session);
        *self.state.suppress_thinking_for_message.borrow_mut() = true;
        let output = self.render_text_delta(session, index, text);
        format!("{thinking_finalize}{output}")
    }

    /// Accumulate a text delta and render the output string.
    fn render_text_delta(&self, session: &mut StreamingSession, index: u64, text: &str) -> String {
        let index_str = index.to_string();
        let show_prefix = session.on_text_delta(index, text);
        let accumulated = session
            .get_accumulated(ContentType::Text, &index_str)
            .unwrap_or("")
            .to_owned();
        let sanitized = sanitize_for_display(&accumulated);
        let should_skip = sanitized.is_empty()
            || session.is_content_hash_rendered(ContentType::Text, &index_str, &sanitized);
        if should_skip {
            return String::new();
        }
        self.emit_text_delta(
            session,
            index,
            show_prefix,
            &accumulated,
            &sanitized,
            &index_str,
        )
    }

    fn emit_text_delta(
        &self,
        session: &mut StreamingSession,
        index: u64,
        show_prefix: bool,
        accumulated_text: &str,
        sanitized_text: &str,
        index_str: &str,
    ) -> String {
        let terminal_mode = *self.state.terminal_mode.borrow();
        let has_prefix = session.has_rendered_prefix(ContentType::Text, index_str);
        let output = self.render_text_for_mode(
            index,
            show_prefix,
            has_prefix,
            accumulated_text,
            sanitized_text,
            terminal_mode,
        );
        session.mark_rendered(ContentType::Text, index_str);
        session.mark_content_hash_rendered(ContentType::Text, index_str, sanitized_text);
        output
    }

    fn render_text_for_mode(
        &self,
        index: u64,
        show_prefix: bool,
        has_prefix: bool,
        accumulated_text: &str,
        sanitized_text: &str,
        terminal_mode: TerminalMode,
    ) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;
        if terminal_mode == TerminalMode::Full {
            *self.state.text_line_active.borrow_mut() = true;
            self.render_text_delta_full_mode(
                index,
                accumulated_text,
                sanitized_text,
                prefix,
                *c,
                terminal_mode,
            )
        } else {
            render_text_delta_basic_mode(
                show_prefix,
                has_prefix,
                accumulated_text,
                prefix,
                *c,
                terminal_mode,
            )
        }
    }

    /// Render a text delta in Full (TTY) mode using append-only suffix emission.
    fn render_text_delta_full_mode(
        &self,
        index: u64,
        accumulated_text: &str,
        sanitized_text: &str,
        prefix: &str,
        c: crate::logger::Colors,
        terminal_mode: TerminalMode,
    ) -> String {
        let key = format!("text:{index}");
        let last_rendered = self.get_last_rendered(&key);
        if last_rendered.is_empty() {
            let rendered =
                TextDeltaRenderer::render_first_delta(accumulated_text, prefix, c, terminal_mode);
            self.update_last_rendered(&key, sanitized_text);
            return rendered;
        }
        let new_suffix = compute_append_only_suffix(&last_rendered, sanitized_text);
        #[cfg(debug_assertions)]
        log_delta_discontinuity_if_detected(
            index,
            &last_rendered,
            sanitized_text,
            new_suffix,
            "text",
        );
        self.update_last_rendered(&key, sanitized_text);
        format!("{}{}{}", c.white(), new_suffix, c.reset())
    }

    /// Handle a thinking content block delta.
    fn handle_thinking_block_delta(
        &self,
        session: &mut StreamingSession,
        index: u64,
        text: &str,
    ) -> String {
        let _show_prefix = session.on_thinking_delta(index, text);

        if *self.state.suppress_thinking_for_message.borrow() {
            return String::new();
        }

        *self.state.thinking_active_index.borrow_mut() = Some(index);
        self.render_thinking_by_mode(session, index)
    }

    fn render_thinking_by_mode(&self, session: &mut StreamingSession, index: u64) -> String {
        match *self.state.terminal_mode.borrow() {
            TerminalMode::Full => self.render_thinking_delta_full_mode(session, index),
            TerminalMode::Basic | TerminalMode::None => {
                self.state
                    .thinking_non_tty_indices
                    .borrow_mut()
                    .insert(index);
                String::new()
            }
        }
    }

    /// Render a thinking delta in Full (TTY) mode using append-only suffix emission.
    fn render_thinking_delta_full_mode(
        &self,
        session: &mut StreamingSession,
        index: u64,
    ) -> String {
        let index_str = index.to_string();
        let accumulated = session
            .get_accumulated(ContentType::Thinking, &index_str)
            .unwrap_or("");
        let sanitized = sanitize_for_display(accumulated);
        let key = format!("thinking:{index}");
        let last_rendered = self.get_last_rendered(&key);
        if last_rendered.is_empty() {
            let rendered =
                crate::json_parser::delta_display::ThinkingDeltaRenderer::render_first_delta(
                    accumulated,
                    &self.display_name,
                    self.colors,
                    TerminalMode::Full,
                );
            self.update_last_rendered(&key, &sanitized);
            return rendered;
        }
        let new_suffix = compute_append_only_suffix(&last_rendered, &sanitized);
        #[cfg(debug_assertions)]
        log_delta_discontinuity_if_detected(
            index,
            &last_rendered,
            &sanitized,
            new_suffix,
            "thinking",
        );
        self.update_last_rendered(&key, &sanitized);
        format!(
            "{}{}{}",
            self.colors.cyan(),
            new_suffix,
            self.colors.reset()
        )
    }

    /// Handle a tool use content block delta.
    fn handle_tool_use_block_delta(
        &self,
        session: &mut StreamingSession,
        index: u64,
        tool_delta: &serde_json::Value,
    ) -> String {
        let thinking_finalize = self.finalize_in_place_full_mode(session);
        *self.state.suppress_thinking_for_message.borrow_mut() = true;
        apply_tool_name(session, index, tool_delta);
        let input_str = extract_tool_input_str(tool_delta);
        if input_str.is_empty() {
            return thinking_finalize;
        }
        session.on_tool_input_delta(index, &input_str);
        format!(
            "{thinking_finalize}{}",
            self.render_tool_input_full(&input_str)
        )
    }

    fn render_tool_input_full(&self, input_str: &str) -> String {
        let terminal_mode = *self.state.terminal_mode.borrow();
        if matches!(terminal_mode, TerminalMode::Basic | TerminalMode::None) {
            return String::new();
        }
        let formatter = DeltaDisplayFormatter::new();
        formatter.format_tool_input(input_str, &self.display_name, self.colors, terminal_mode)
    }
}

/// Extract and set tool name from a tool delta if present.
fn apply_tool_name(session: &mut StreamingSession, index: u64, tool_delta: &serde_json::Value) {
    if let Some(serde_json::Value::String(name)) = tool_delta.get("name") {
        session.set_tool_name(index, Some(name.clone()));
    }
}

/// Extract the tool input string from a tool delta value.
fn extract_tool_input_str(tool_delta: &serde_json::Value) -> String {
    tool_delta
        .get("input")
        .map_or_else(String::new, |input| match input {
            serde_json::Value::String(s) => s.clone(),
            other => format_tool_input(other),
        })
}

/// Render text delta in Basic/None mode.
fn render_text_delta_basic_mode(
    show_prefix: bool,
    has_prefix: bool,
    accumulated_text: &str,
    prefix: &str,
    c: crate::logger::Colors,
    terminal_mode: TerminalMode,
) -> String {
    if show_prefix && !has_prefix {
        TextDeltaRenderer::render_first_delta(accumulated_text, prefix, c, terminal_mode)
    } else {
        TextDeltaRenderer::render_subsequent_delta(accumulated_text, prefix, c, terminal_mode)
    }
}

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

        self.finalize_text_line_full_mode(terminal_mode)
    }

    /// Finalize active text streaming line or defensive cursor fallback in Full mode.
    fn finalize_text_line_full_mode(&self, terminal_mode: TerminalMode) -> String {
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

impl crate::json_parser::claude::ClaudeParser {
    /// Handle standalone text delta events (not part of content blocks).
    ///
    /// Uses default index "0" for accumulation and follows append-only rendering.
    pub(in crate::json_parser::claude) fn handle_text_delta(
        &self,
        session: &mut StreamingSession,
        text: &str,
    ) -> String {
        // Standalone text delta uses default index "0".
        self.handle_text_block_delta(session, 0, text)
    }

    /// Handle message stop events - flush accumulated content in non-TTY modes.
    ///
    /// ## Flush Strategy (CCS Spam Prevention)
    ///
    /// In non-TTY modes (Basic/None), emit accumulated content ONCE per content block:
    /// 1. Thinking: Flush all thinking indices (multiple blocks supported)
    /// 2. Tool input: Flush all tool inputs (respects verbosity for secrets)
    /// 3. Text: Flush all text blocks
    ///
    /// In Full mode, finalize active thinking line and emit completion newline.
    ///
    /// ## Pre-rendered Message Handling
    ///
    /// If the message was already rendered by an assistant event before streaming,
    /// skip flushing accumulated deltas to avoid duplicate output.
    pub(in crate::json_parser::claude) fn handle_message_stop(
        &self,
        session: &mut StreamingSession,
    ) -> String {
        let terminal_mode = *self.state.terminal_mode.borrow();
        let thinking_finalize = self.finalize_thinking_full_mode(session);
        let (thinking_flush, tool_flush, text_flush) =
            self.collect_non_tty_flush(session, terminal_mode);
        let metrics = session.get_streaming_quality_metrics();
        let was_in_block = session.on_message_stop();
        let completion = self.build_stop_completion(was_in_block, terminal_mode, &metrics);
        format!("{thinking_finalize}{thinking_flush}{tool_flush}{text_flush}{completion}")
    }

    fn collect_non_tty_flush(
        &self,
        session: &mut StreamingSession,
        terminal_mode: TerminalMode,
    ) -> (String, String, String) {
        if terminal_mode == TerminalMode::Full {
            (String::new(), String::new(), String::new())
        } else {
            self.flush_non_tty_content(session, terminal_mode)
        }
    }

    fn build_stop_completion(
        &self,
        was_in_block: bool,
        terminal_mode: TerminalMode,
        metrics: &crate::json_parser::health::StreamingQualityMetrics,
    ) -> String {
        if was_in_block || self.needs_full_mode_completion(terminal_mode) {
            self.emit_stop_completion(terminal_mode, metrics)
        } else {
            String::new()
        }
    }

    fn needs_full_mode_completion(&self, terminal_mode: TerminalMode) -> bool {
        terminal_mode == TerminalMode::Full
            && (*self.state.text_line_active.borrow() || *self.state.cursor_up_active.borrow())
    }

    fn emit_stop_completion(
        &self,
        terminal_mode: TerminalMode,
        metrics: &crate::json_parser::health::StreamingQualityMetrics,
    ) -> String {
        if terminal_mode == TerminalMode::Full {
            *self.state.text_line_active.borrow_mut() = false;
            *self.state.cursor_up_active.borrow_mut() = false;
        }
        let completion = build_message_stop_completion(self.colors, terminal_mode);
        let show_metrics = self.should_show_streaming_metrics(metrics);
        build_completion_with_metrics(
            completion,
            show_metrics,
            terminal_mode,
            self.colors,
            metrics,
        )
    }

    fn should_show_streaming_metrics(
        &self,
        metrics: &crate::json_parser::health::StreamingQualityMetrics,
    ) -> bool {
        (self.verbosity.is_debug() || self.show_streaming_metrics) && metrics.total_deltas > 0
    }

    /// Flush accumulated thinking, tool input, and text in non-TTY modes.
    fn flush_non_tty_content(
        &self,
        session: &mut StreamingSession,
        terminal_mode: TerminalMode,
    ) -> (String, String, String) {
        if session
            .get_current_message_id()
            .is_some_and(|message_id| session.is_message_pre_rendered(message_id))
        {
            self.state.thinking_active_index.borrow_mut().take();
            self.state.thinking_non_tty_indices.borrow_mut().clear();
            return (String::new(), String::new(), String::new());
        }

        let thinking_output = self.flush_thinking_non_tty(session, terminal_mode);
        let tool_output = self.flush_tool_input_non_tty(session, terminal_mode);
        let text_output =
            flush_text_non_tty(session, &self.display_name, terminal_mode, self.colors);
        (thinking_output, tool_output, text_output)
    }

    /// Collect and format accumulated thinking indices for non-TTY output.
    fn flush_thinking_non_tty(
        &self,
        session: &mut StreamingSession,
        terminal_mode: TerminalMode,
    ) -> String {
        let indices = self.collect_and_clear_thinking_indices();
        indices
            .iter()
            .filter_map(|&index| {
                let index_str = index.to_string();
                let accumulated = session
                    .get_accumulated(ContentType::Thinking, &index_str)
                    .unwrap_or("");
                let sanitized = sanitize_for_display(accumulated);
                if sanitized.is_empty() {
                    return None;
                }
                Some(format_non_tty_thinking_line(
                    &sanitized,
                    &self.display_name,
                    terminal_mode,
                    self.colors,
                ))
            })
            .collect()
    }

    fn collect_and_clear_thinking_indices(&self) -> Vec<u64> {
        let indices: Vec<u64> = if self.state.thinking_non_tty_indices.borrow().is_empty() {
            self.state
                .thinking_active_index
                .borrow()
                .iter()
                .copied()
                .collect()
        } else {
            self.state
                .thinking_non_tty_indices
                .borrow()
                .iter()
                .copied()
                .collect()
        };
        self.state.thinking_non_tty_indices.borrow_mut().clear();
        self.state.thinking_active_index.borrow_mut().take();
        indices
    }

    /// Format and collect accumulated tool input for non-TTY output.
    fn flush_tool_input_non_tty(
        &self,
        session: &mut StreamingSession,
        terminal_mode: TerminalMode,
    ) -> String {
        if !self.verbosity.show_tool_input() {
            return String::new();
        }
        let c = &self.colors;
        session
            .accumulated_keys(ContentType::ToolInput)
            .iter()
            .filter_map(|index_str| {
                let accumulated = session
                    .get_accumulated(ContentType::ToolInput, index_str)
                    .unwrap_or("");
                let sanitized = sanitize_for_display(accumulated);
                if sanitized.is_empty() {
                    return None;
                }
                Some(format_non_tty_tool_input_line(
                    &sanitized,
                    &self.display_name,
                    terminal_mode,
                    *c,
                ))
            })
            .collect()
    }
}

/// Build prefix/suffix parts for a non-TTY line in Basic or None mode.
///
/// Returns `(prefix, suffix)` for the given terminal mode.
fn non_tty_line_parts(
    display_name: &str,
    terminal_mode: TerminalMode,
    c: crate::logger::Colors,
) -> (String, String) {
    match terminal_mode {
        TerminalMode::Basic => (
            format!("{}[{}]{} {}", c.dim(), display_name, c.reset(), c.dim()),
            c.reset().to_string(),
        ),
        TerminalMode::None => (format!("[{display_name}] "), String::new()),
        TerminalMode::Full => unreachable!(),
    }
}

/// Format a single thinking line for non-TTY output.
fn format_non_tty_thinking_line(
    sanitized: &str,
    display_name: &str,
    terminal_mode: TerminalMode,
    c: crate::logger::Colors,
) -> String {
    let (prefix_fmt, suffix_fmt) = non_tty_line_parts(display_name, terminal_mode, c);
    let label_color = if terminal_mode == TerminalMode::Basic {
        c.cyan().to_string()
    } else {
        String::new()
    };
    format!("{prefix_fmt}Thinking: {label_color}{sanitized}{suffix_fmt}\n")
}

/// Format a single tool input line for non-TTY output.
fn format_non_tty_tool_input_line(
    sanitized: &str,
    display_name: &str,
    terminal_mode: TerminalMode,
    c: crate::logger::Colors,
) -> String {
    let (prefix_fmt, suffix_fmt) = non_tty_line_parts(display_name, terminal_mode, c);
    let label_color = if terminal_mode == TerminalMode::Basic {
        c.cyan().to_string()
    } else {
        String::new()
    };
    format!("{prefix_fmt}Tool input: {label_color}{sanitized}{suffix_fmt}\n")
}

/// Format and collect accumulated text blocks for non-TTY output.
fn flush_text_non_tty(
    session: &mut StreamingSession,
    display_name: &str,
    terminal_mode: TerminalMode,
    c: crate::logger::Colors,
) -> String {
    session
        .accumulated_keys(ContentType::Text)
        .iter()
        .filter_map(|index_str| {
            let accumulated = session
                .get_accumulated(ContentType::Text, index_str)
                .unwrap_or("");
            let sanitized = sanitize_for_display(accumulated);
            if sanitized.is_empty() {
                return None;
            }
            let prefix_fmt = match terminal_mode {
                TerminalMode::Basic => {
                    format!("{}[{}]{} {}", c.dim(), display_name, c.reset(), c.white())
                }
                TerminalMode::None => format!("[{display_name}] "),
                TerminalMode::Full => unreachable!(),
            };
            let suffix_fmt = match terminal_mode {
                TerminalMode::Basic => c.reset().to_string(),
                TerminalMode::None => String::new(),
                TerminalMode::Full => unreachable!(),
            };
            Some(format!("{prefix_fmt}{sanitized}{suffix_fmt}\n"))
        })
        .collect()
}

/// Build the completion string for message_stop in Full mode.
fn build_message_stop_completion(c: crate::logger::Colors, terminal_mode: TerminalMode) -> String {
    if terminal_mode == TerminalMode::Full {
        format!(
            "{}{}",
            c.reset(),
            TextDeltaRenderer::render_completion(terminal_mode)
        )
    } else {
        String::new()
    }
}

/// Append streaming metrics to a completion string if enabled.
fn build_completion_with_metrics(
    completion: String,
    show_metrics: bool,
    terminal_mode: TerminalMode,
    c: crate::logger::Colors,
    metrics: &crate::json_parser::health::StreamingQualityMetrics,
) -> String {
    if show_metrics {
        if terminal_mode == TerminalMode::Full {
            format!("{}\n{}", completion, metrics.format(c))
        } else {
            metrics.format(c)
        }
    } else {
        completion
    }
}
