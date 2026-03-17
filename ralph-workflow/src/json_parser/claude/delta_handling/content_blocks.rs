//! Content block delta handling (text, thinking, tool use).
//!
//! ## Overview
//!
//! Handles `content_block_delta` events containing text, thinking, or tool use deltas.
//! Implements append-only rendering in Full mode and suppression+flush in Basic/None modes.
//!
//! ## Append-Only Rendering (Full Mode)
//!
//! In Full TTY mode, deltas are rendered incrementally:
//! 1. Track last rendered content for each (`content_type`, index) pair
//! 2. Compute longest common prefix between last and current
//! 3. Emit only the NEW suffix (no prefix, no control codes)
//! 4. Update last rendered state
//!
//! This creates smooth append-only streaming without flickering or re-rendering.
//!
//! ## Suppression+Flush (Basic/None Modes)
//!
//! In non-TTY modes:
//!
//! 1. Accumulate all deltas silently (suppress per-delta output)
//! 2. At `message_stop`, flush accumulated content ONCE per block
//! 3. Prevents CCS spam (hundreds of "[ccs/glm]" lines)
//!
//! ## Deduplication
//!
//! Multiple layers prevent duplicate output:
//! 1. **`StreamingSession`**: Protocol-level deduplication (snapshot-as-delta repair, consecutive duplicates)
//! 2. **Parser layer**: Skip whitespace-only content, hash-based deduplication after sanitization
//! 3. **Prefix trie**: Track rendered prefixes to detect extensions vs new content
//!
//! ## Delta Types
//!
//! - **`TextDelta`**: Assistant text output (main content)
//! - **`ThinkingDelta`**: Extended thinking blocks (Claude reasoning)
//! - **`ToolUseDelta`**: Tool use parameters (partial JSON chunks)

#[cfg(any(test, debug_assertions))]
use std::io::Write;

use crate::json_parser::delta_display::{
    compute_append_only_suffix, sanitize_for_display, DeltaDisplayFormatter, DeltaRenderer,
    TextDeltaRenderer,
};
use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::{ContentBlockDelta, ContentType};

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
        session: &mut std::cell::RefMut<'_, StreamingSession>,
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
                *self.suppress_thinking_for_message.borrow_mut() = true;
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
                let terminal_mode = *self.terminal_mode.borrow();

                if terminal_mode == TerminalMode::Full {
                    *self.text_line_active.borrow_mut() = true;
                }

                // Use prefix trie to detect if new content extends previously rendered content
                // If yes, we do an in-place update (append-only: emit only new suffix)
                let has_prefix = session.has_rendered_prefix(ContentType::Text, &index_str);

                let output = if terminal_mode == TerminalMode::Full {
                    // Append-only pattern in Full mode: track last rendered and emit only new content
                    let key = format!("text:{index}");
                    let last_rendered = self
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
                        self.last_rendered_content
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
                        self.last_rendered_content
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

                if *self.suppress_thinking_for_message.borrow() {
                    // Accumulate for state/deduplication, but don't render late thinking.
                    return String::new();
                }

                *self.thinking_active_index.borrow_mut() = Some(index);

                // In non-TTY modes, we suppress per-delta thinking output and flush once
                // at the next output boundary (or at message_stop).
                let terminal_mode = *self.terminal_mode.borrow();
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
                            self.last_rendered_content
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
                            self.last_rendered_content
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
                        self.thinking_non_tty_indices.borrow_mut().insert(index);
                        String::new()
                    }
                }
            }
            ContentBlockDelta::ToolUseDelta {
                tool_use: Some(tool_delta),
            } => {
                let thinking_finalize = self.finalize_in_place_full_mode(session);
                *self.suppress_thinking_for_message.borrow_mut() = true;
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
                    let terminal_mode = *self.terminal_mode.borrow();
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
