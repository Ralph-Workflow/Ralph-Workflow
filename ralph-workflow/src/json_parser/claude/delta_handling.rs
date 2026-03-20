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

/// Format tool input for display (convert non-string values to JSON).
fn format_tool_input(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::String(s) => s.clone(),
        other => serde_json::to_string_pretty(other).unwrap_or_else(|_| "{}".to_string()),
    }
}

impl crate::json_parser::claude::ClaudeParser {
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
        let c = &self.colors;
        let prefix = &self.display_name;

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

        match delta {
            ContentBlockDelta::TextDelta { text: Some(text) } => {
                let thinking_finalize = self.finalize_thinking_full_mode(session);
                *self.state.suppress_thinking_for_message.borrow_mut() = true;
                let index_str = index.to_string();

                // Track this delta with StreamingSession for state management.
                //
                // StreamingSession handles protocol/streaming quality concerns (including
                // snapshot-as-delta repairs and consecutive duplicate filtering) and returns
                // whether a prefix should be displayed for this stream.
                //
                // The parser layer still applies additional deduplication:
                // - Skip whitespace-only accumulated output
                // - Hash-based deduplication after sanitization (whitespace-insensitive)
                let show_prefix = session.on_text_delta(index, &text);

                // `on_text_delta` returns whether the prefix should be shown, not whether output
                // should be emitted. If the accumulated content is non-empty and not a duplicate,
                // we still need to render it even when `show_prefix` is false.

                // Get accumulated text for streaming display
                let accumulated_text = session
                    .get_accumulated(ContentType::Text, &index_str)
                    .unwrap_or("");

                // Sanitize the accumulated text to check if it's empty
                // This is needed to skip rendering when the accumulated content is just whitespace
                let sanitized_text = sanitize_for_display(accumulated_text);

                // Skip rendering if the sanitized text is empty (e.g., only whitespace)
                // This prevents rendering empty lines when the accumulated content is just whitespace
                if sanitized_text.is_empty() {
                    return String::new();
                }

                // Check if this sanitized content has already been rendered
                // This prevents duplicates when accumulated content differs only by whitespace
                if session.is_content_hash_rendered(ContentType::Text, &index_str, &sanitized_text)
                {
                    return String::new();
                }

                // Use TextDeltaRenderer for consistent rendering
                let terminal_mode = *self.state.terminal_mode.borrow();

                if terminal_mode == TerminalMode::Full {
                    *self.state.text_line_active.borrow_mut() = true;
                }

                // Use prefix trie to detect if new content extends previously rendered content
                // If yes, we do an in-place update (append-only: emit only new suffix)
                let has_prefix = session.has_rendered_prefix(ContentType::Text, &index_str);

                let output = if terminal_mode == TerminalMode::Full {
                    // Append-only pattern in Full mode: track last rendered and emit only new content
                    let key = format!("text:{index}");
                    let last_rendered = self
                        .state
                        .last_rendered_content
                        .borrow()
                        .get(&key)
                        .cloned()
                        .unwrap_or_default();

                    if last_rendered.is_empty() {
                        // First delta for this index: emit prefix + content
                        let rendered = TextDeltaRenderer::render_first_delta(
                            accumulated_text,
                            prefix,
                            *c,
                            terminal_mode,
                        );
                        // Track what we rendered (the sanitized content, not the ANSI codes)
                        self.state
                            .last_rendered_content
                            .borrow_mut()
                            .insert(key, sanitized_text.clone());
                        rendered
                    } else {
                        // Subsequent delta: emit only NEW suffix
                        // Compute longest common prefix between last rendered and current
                        let new_suffix =
                            compute_append_only_suffix(&last_rendered, &sanitized_text);

                        // Detect discontinuities: when both last_rendered and current are non-empty
                        // but the suffix is empty, it indicates non-monotonic deltas from the provider
                        if new_suffix.is_empty()
                            && !last_rendered.is_empty()
                            && !sanitized_text.is_empty()
                        {
                            // This is a protocol violation - content changed unexpectedly
                            // Log it for debugging provider behavior (similar to snapshot-as-delta warnings)
                            #[cfg(debug_assertions)]
                            {
                                let _ = writeln!(
                                    std::io::stderr(),
                                    "Warning: Delta discontinuity detected for text block {}. \
                                     Provider sent non-monotonic content. \
                                     Last: {:?} (len={}), Current: {:?} (len={})",
                                    index,
                                    &last_rendered[..last_rendered.len().min(40)],
                                    last_rendered.len(),
                                    &sanitized_text[..sanitized_text.len().min(40)],
                                    sanitized_text.len()
                                );
                            }
                        }

                        // Track new rendered content
                        self.state
                            .last_rendered_content
                            .borrow_mut()
                            .insert(key, sanitized_text.clone());

                        // Emit only the new suffix (no prefix, no control codes)
                        format!("{}{}{}", c.white(), new_suffix, c.reset())
                    }
                } else {
                    // Basic/None mode: suppress per-delta output (existing behavior)
                    if show_prefix && !has_prefix {
                        TextDeltaRenderer::render_first_delta(
                            accumulated_text,
                            prefix,
                            *c,
                            terminal_mode,
                        )
                    } else {
                        TextDeltaRenderer::render_subsequent_delta(
                            accumulated_text,
                            prefix,
                            *c,
                            terminal_mode,
                        )
                    }
                };

                // Mark this sanitized content as rendered for future duplicate detection
                // We use the sanitized text (not the rendered output) to avoid false positives
                // when the same accumulated text is rendered with different terminal modes
                session.mark_rendered(ContentType::Text, &index_str);
                session.mark_content_hash_rendered(ContentType::Text, &index_str, &sanitized_text);

                format!("{thinking_finalize}{output}")
            }
            ContentBlockDelta::ThinkingDelta {
                thinking: Some(text),
            } => {
                let _show_prefix = session.on_thinking_delta(index, &text);

                if *self.state.suppress_thinking_for_message.borrow() {
                    // Accumulate for state/deduplication, but don't render late thinking.
                    return String::new();
                }

                *self.state.thinking_active_index.borrow_mut() = Some(index);

                // In non-TTY modes, we suppress per-delta thinking output and flush once
                // at the next output boundary (or at message_stop).
                let terminal_mode = *self.state.terminal_mode.borrow();
                match terminal_mode {
                    TerminalMode::Full => {
                        let index_str = index.to_string();
                        let accumulated = session
                            .get_accumulated(ContentType::Thinking, &index_str)
                            .unwrap_or("");
                        let sanitized = sanitize_for_display(accumulated);

                        // Append-only pattern: track last rendered and emit only new content
                        let key = format!("thinking:{index}");
                        let last_rendered = self
                            .state
                            .last_rendered_content
                            .borrow()
                            .get(&key)
                            .cloned()
                            .unwrap_or_default();

                        let out = if last_rendered.is_empty() {
                            // First delta for this thinking block: emit prefix + content
                            let rendered = crate::json_parser::delta_display::ThinkingDeltaRenderer::render_first_delta(
                                accumulated,
                                prefix,
                                *c,
                                terminal_mode,
                            );
                            // Track what we rendered (the sanitized content)
                            self.state
                                .last_rendered_content
                                .borrow_mut()
                                .insert(key, sanitized);
                            rendered
                        } else {
                            // Subsequent delta: emit only NEW suffix
                            let new_suffix = compute_append_only_suffix(&last_rendered, &sanitized);

                            // Detect discontinuities in thinking deltas
                            if new_suffix.is_empty()
                                && !last_rendered.is_empty()
                                && !sanitized.is_empty()
                            {
                                #[cfg(debug_assertions)]
                                {
                                    let _ = writeln!(
                                        std::io::stderr(),
                                        "Warning: Delta discontinuity detected for thinking block {}. \
                                         Provider sent non-monotonic content. \
                                         Last: {:?} (len={}), Current: {:?} (len={})",
                                        index,
                                        &last_rendered[..last_rendered.len().min(40)],
                                        last_rendered.len(),
                                        &sanitized[..sanitized.len().min(40)],
                                        sanitized.len()
                                    );
                                }
                            }

                            // Track new rendered content
                            self.state
                                .last_rendered_content
                                .borrow_mut()
                                .insert(key, sanitized.clone());

                            // Emit only the new suffix (no prefix, no \r)
                            // Use the same color scheme as ThinkingDeltaRenderer for consistency
                            format!("{}{}{}", c.cyan(), new_suffix, c.reset())
                        };

                        out
                    }
                    TerminalMode::Basic | TerminalMode::None => {
                        // Track all thinking indices that accumulated content so we can flush them
                        // at message_stop. Providers can emit multiple thinking content blocks in a
                        // single message, so tracking only the "active" index would drop earlier
                        // thinking blocks from non-TTY output.
                        self.state
                            .thinking_non_tty_indices
                            .borrow_mut()
                            .insert(index);
                        String::new()
                    }
                }
            }
            ContentBlockDelta::ToolUseDelta {
                tool_use: Some(tool_delta),
            } => {
                let thinking_finalize = self.finalize_in_place_full_mode(session);
                *self.state.suppress_thinking_for_message.borrow_mut() = true;
                // Track tool name for GLM/CCS deduplication (if available in delta)
                if let Some(serde_json::Value::String(name)) = tool_delta.get("name") {
                    session.set_tool_name(index, Some(name.clone()));
                }

                // Handle tool input streaming
                // Extract the tool input from the delta
                let input_str =
                    tool_delta
                        .get("input")
                        .map_or_else(String::new, |input| match input {
                            serde_json::Value::String(s) => s.clone(),
                            other => format_tool_input(other),
                        });

                if input_str.is_empty() {
                    thinking_finalize
                } else {
                    // Accumulate tool input
                    session.on_tool_input_delta(index, &input_str);

                    // Tool input is rendered once at tool completion/message_stop in non-TTY modes
                    // to avoid repeated prefixed lines for partial JSON chunks.
                    let terminal_mode = *self.state.terminal_mode.borrow();
                    if matches!(terminal_mode, TerminalMode::Basic | TerminalMode::None) {
                        thinking_finalize
                    } else {
                        // Show partial tool input in real-time in Full TTY mode
                        let formatter = DeltaDisplayFormatter::new();
                        let tool_out =
                            formatter.format_tool_input(&input_str, prefix, *c, terminal_mode);
                        format!("{thinking_finalize}{tool_out}")
                    }
                }
            }
            _ => String::new(),
        }
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
        let thinking_finalize = self.finalize_thinking_full_mode(session);
        *self.state.suppress_thinking_for_message.borrow_mut() = true;
        let c = &self.colors;
        let prefix = &self.display_name;

        // Standalone text delta (not part of content block)
        // Use default index "0" for standalone text
        let default_index = 0u64;
        let default_index_str = "0";

        // Track this delta with StreamingSession for state management.
        //
        // StreamingSession handles protocol/streaming quality concerns (including
        // snapshot-as-delta repairs and consecutive duplicate filtering) and returns
        // whether a prefix should be displayed for this stream.
        //
        // The parser layer still applies additional deduplication:
        // - Skip whitespace-only accumulated output
        // - Hash-based deduplication after sanitization (whitespace-insensitive)
        let show_prefix = session.on_text_delta(default_index, text);

        // Get accumulated text for streaming display
        let accumulated_text = session
            .get_accumulated(ContentType::Text, default_index_str)
            .unwrap_or("");

        // Sanitize the accumulated text to check if it's empty
        // This is needed to skip rendering when the accumulated content is just whitespace
        let sanitized_text = sanitize_for_display(accumulated_text);

        // Skip rendering if the sanitized text is empty (e.g., only whitespace)
        // This prevents rendering empty lines when the accumulated content is just whitespace
        if sanitized_text.is_empty() {
            return String::new();
        }

        // Check if this sanitized content has already been rendered
        // This prevents duplicates when accumulated content differs only by whitespace
        if session.is_content_hash_rendered(ContentType::Text, default_index_str, &sanitized_text) {
            return String::new();
        }

        // Use TextDeltaRenderer for consistent rendering across all parsers
        let terminal_mode = *self.state.terminal_mode.borrow();

        if terminal_mode == TerminalMode::Full {
            // Append-only streaming keeps the cursor on the current line; we still track
            // that a streaming text line is active so newline-based output can ensure the
            // final completion newline is emitted at message boundaries.
            *self.state.text_line_active.borrow_mut() = true;
        }

        // Use prefix trie to detect if new content extends previously rendered content
        let has_prefix = session.has_rendered_prefix(ContentType::Text, default_index_str);

        let output = if terminal_mode == TerminalMode::Full {
            // Append-only pattern in Full mode: track last rendered and emit only new content
            let key = format!("text:{default_index}");
            let last_rendered = self
                .state
                .last_rendered_content
                .borrow()
                .get(&key)
                .cloned()
                .unwrap_or_default();

            if last_rendered.is_empty() {
                // First delta for this index: emit prefix + content
                let rendered = TextDeltaRenderer::render_first_delta(
                    accumulated_text,
                    prefix,
                    *c,
                    terminal_mode,
                );
                // Track what we rendered (the sanitized content, not the ANSI codes)
                self.state
                    .last_rendered_content
                    .borrow_mut()
                    .insert(key, sanitized_text.clone());
                rendered
            } else {
                // Subsequent delta: emit only NEW suffix
                // Compute longest common prefix between last rendered and current
                let new_suffix = compute_append_only_suffix(&last_rendered, &sanitized_text);

                // Detect discontinuities in tool use deltas
                if new_suffix.is_empty() && !last_rendered.is_empty() && !sanitized_text.is_empty()
                {
                    #[cfg(debug_assertions)]
                    {
                        let _ = writeln!(
                            std::io::stderr(),
                            "Warning: Delta discontinuity detected for tool use text. \
                             Provider sent non-monotonic content. \
                             Last: {:?} (len={}), Current: {:?} (len={})",
                            &last_rendered[..last_rendered.len().min(40)],
                            last_rendered.len(),
                            &sanitized_text[..sanitized_text.len().min(40)],
                            sanitized_text.len()
                        );
                    }
                }

                // Track new rendered content
                self.state
                    .last_rendered_content
                    .borrow_mut()
                    .insert(key, sanitized_text.clone());

                // Emit only the new suffix (no prefix, no control codes)
                format!("{}{}{}", c.white(), new_suffix, c.reset())
            }
        } else {
            // Basic/None mode: use original logic
            if show_prefix && !has_prefix {
                TextDeltaRenderer::render_first_delta(accumulated_text, prefix, *c, terminal_mode)
            } else {
                // In Basic/None modes, render_subsequent_delta returns empty string anyway
                TextDeltaRenderer::render_subsequent_delta(
                    accumulated_text,
                    prefix,
                    *c,
                    terminal_mode,
                )
            }
        };

        // Mark this sanitized content as rendered for future duplicate detection
        // We use the sanitized text (not the rendered output) to avoid false positives
        // when the same accumulated text is rendered with different terminal modes
        session.mark_rendered(ContentType::Text, default_index_str);
        session.mark_content_hash_rendered(ContentType::Text, default_index_str, &sanitized_text);

        format!("{thinking_finalize}{output}")
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
        let c = &self.colors;

        let terminal_mode = *self.state.terminal_mode.borrow();

        // In Full mode, finalize any active thinking line.
        let thinking_finalize = self.finalize_thinking_full_mode(session);

        // In non-TTY modes, flush thinking, tool input, and text once at message_stop.
        let (thinking_flush_non_tty, tool_input_flush_non_tty, text_flush_non_tty) =
            match terminal_mode {
                TerminalMode::Full => (String::new(), String::new(), String::new()),
                TerminalMode::Basic | TerminalMode::None => {
                    // If the final assistant message was already rendered (pre-rendered), do not
                    // flush accumulated streaming state in non-TTY modes.
                    //
                    // Some providers emit a complete assistant event before streaming deltas;
                    // those deltas can still arrive and would otherwise be accumulated and flushed
                    // here, duplicating already-rendered content.
                    if session
                        .get_current_message_id()
                        .is_some_and(|message_id| session.is_message_pre_rendered(message_id))
                    {
                        // Clear any pending thinking indices to avoid cross-message contamination.
                        self.state.thinking_active_index.borrow_mut().take();
                        self.state.thinking_non_tty_indices.borrow_mut().clear();
                        (String::new(), String::new(), String::new())
                    } else {
                        // Flush accumulated thinking.
                        // We format the output directly here because the renderers now suppress
                        // output in non-TTY modes (to prevent per-delta spam).
                        let thinking_output = {
                            let indices: Vec<u64> =
                                if self.state.thinking_non_tty_indices.borrow().is_empty() {
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

                                    let prefix_fmt = match terminal_mode {
                                        TerminalMode::Basic => format!(
                                            "{}[{}]{} {}",
                                            c.dim(),
                                            &self.display_name,
                                            c.reset(),
                                            c.dim()
                                        ),
                                        TerminalMode::None => {
                                            format!("[{}] ", &self.display_name)
                                        }
                                        TerminalMode::Full => unreachable!(),
                                    };

                                    let label_fmt = match terminal_mode {
                                        TerminalMode::Basic => {
                                            format!("Thinking: {}", c.cyan())
                                        }
                                        TerminalMode::None => "Thinking: ".to_string(),
                                        TerminalMode::Full => unreachable!(),
                                    };

                                    let suffix_fmt = match terminal_mode {
                                        TerminalMode::Basic => c.reset().to_string(),
                                        TerminalMode::None => String::new(),
                                        TerminalMode::Full => unreachable!(),
                                    };

                                    Some(format!(
                                        "{prefix_fmt}{label_fmt}{sanitized}{suffix_fmt}\n"
                                    ))
                                })
                                .collect::<String>()
                        };

                        // Flush accumulated tool input.
                        // Tool input deltas can arrive as partial JSON chunks; in non-TTY modes we
                        // render the final accumulated value once at message_stop.
                        //
                        // IMPORTANT: Tool inputs can contain secrets. Respect the global verbosity
                        // policy (same as assistant tool blocks) rather than unconditionally printing.
                        let tool_output = if self.verbosity.show_tool_input() {
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
                                    let prefix_fmt = match terminal_mode {
                                        TerminalMode::Basic => format!(
                                            "{}[{}]{} {}",
                                            c.dim(),
                                            &self.display_name,
                                            c.reset(),
                                            c.dim()
                                        ),
                                        TerminalMode::None => {
                                            format!("[{}] ", &self.display_name)
                                        }
                                        TerminalMode::Full => unreachable!(),
                                    };

                                    let label_fmt = match terminal_mode {
                                        TerminalMode::Basic => {
                                            format!("Tool input: {}", c.cyan())
                                        }
                                        TerminalMode::None => "Tool input: ".to_string(),
                                        TerminalMode::Full => unreachable!(),
                                    };

                                    let suffix_fmt = match terminal_mode {
                                        TerminalMode::Basic => c.reset().to_string(),
                                        TerminalMode::None => String::new(),
                                        TerminalMode::Full => unreachable!(),
                                    };

                                    Some(format!(
                                        "{prefix_fmt}{label_fmt}{sanitized}{suffix_fmt}\n"
                                    ))
                                })
                                .collect::<String>()
                        } else {
                            String::new()
                        };

                        // Flush accumulated text content for all content blocks.
                        // We format the output directly here because the renderers now suppress
                        // output in non-TTY modes (to prevent per-delta spam).
                        let text_output = session
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
                                    TerminalMode::Basic => format!(
                                        "{}[{}]{} {}",
                                        c.dim(),
                                        &self.display_name,
                                        c.reset(),
                                        c.white()
                                    ),
                                    TerminalMode::None => {
                                        format!("[{}] ", &self.display_name)
                                    }
                                    TerminalMode::Full => unreachable!(),
                                };

                                let suffix_fmt = match terminal_mode {
                                    TerminalMode::Basic => c.reset().to_string(),
                                    TerminalMode::None => String::new(),
                                    TerminalMode::Full => unreachable!(),
                                };

                                Some(format!("{prefix_fmt}{sanitized}{suffix_fmt}\n"))
                            })
                            .collect::<String>();

                        (thinking_output, tool_output, text_output)
                    }
                }
            };

        // Message complete - add final newline if we were in a content block
        // OR if any content was streamed (handles edge cases where block state
        // may not have been set but content was still streamed)
        let metrics = session.get_streaming_quality_metrics();
        let was_in_block = session.on_message_stop();

        // In Full mode, a streamed text line can leave the cursor positioned on the current line
        // (append-only streaming emits no cursor controls during deltas). Normally `was_in_block`
        // implies we should emit a completion newline, but some real-world logs can violate block
        // lifecycle ordering. If we have an active text streaming line, still emit completion.
        let needs_text_completion = terminal_mode == TerminalMode::Full
            && (*self.state.text_line_active.borrow() || *self.state.cursor_up_active.borrow());
        let should_emit_completion = was_in_block || needs_text_completion;

        if should_emit_completion {
            if terminal_mode == TerminalMode::Full {
                *self.state.text_line_active.borrow_mut() = false;
                *self.state.cursor_up_active.borrow_mut() = false;
            }

            let completion = if terminal_mode == TerminalMode::Full {
                format!(
                    "{}{}",
                    c.reset(),
                    TextDeltaRenderer::render_completion(terminal_mode)
                )
            } else {
                // In non-TTY modes, flush output paths already include newline terminators and
                // individual lines already end with a reset. Emitting an additional standalone
                // reset here can leave a trailing ANSI sequence after the final newline.
                String::new()
            };

            // Show streaming quality metrics in debug mode or when flag is set
            let show_metrics = (self.verbosity.is_debug() || self.show_streaming_metrics)
                && metrics.total_deltas > 0;
            let completion_with_metrics = if show_metrics {
                if terminal_mode == TerminalMode::Full {
                    format!("{}\n{}", completion, metrics.format(*c))
                } else {
                    // In non-TTY, the flush output already ended with a newline, so metrics can be
                    // appended directly without inserting an extra blank line.
                    metrics.format(*c)
                }
            } else {
                completion
            };

            format!("{thinking_finalize}{thinking_flush_non_tty}{tool_input_flush_non_tty}{text_flush_non_tty}{completion_with_metrics}")
        } else {
            format!("{thinking_finalize}{thinking_flush_non_tty}{tool_input_flush_non_tty}{text_flush_non_tty}")
        }
    }
}
