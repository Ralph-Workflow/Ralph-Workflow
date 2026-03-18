//! Message-level delta handling (text deltas, message stop, flush logic).
//!
//! ## Overview
//!
//! Handles standalone text deltas (not part of content blocks) and message stop events.
//! Implements the critical flush logic that prevents CCS spam in non-TTY modes.
//!
//! ## Text Delta Handling
//!
//! Standalone text deltas use a default index ("0") for accumulation and follow the
//! same append-only rendering pattern as content block text deltas.
//!
//! ## Message Stop Flush Logic (CCS Spam Prevention)
//!
//! At `message_stop`, accumulated content is flushed ONCE per content block:
//!
//! 1. **Thinking flush**: Emit all accumulated thinking blocks (multiple indices supported)
//! 2. **Tool input flush**: Emit all accumulated tool inputs (respects verbosity)
//! 3. **Text flush**: Emit all accumulated text blocks
//!
//! Each flush emits a single prefixed line per content block, preventing the hundreds of
//! repeated "[ccs/glm]" lines that occurred with per-delta output.
//!
//! ## Completion Handling
//!
//! In Full mode, emit completion newline if:
//! - We were in an active content block (`was_in_block`)
//! - OR an active text streaming line exists (`text_line_active` or `cursor_up_active`)
//!
//! This handles protocol violations where block lifecycle ordering is violated.

use crate::json_parser::delta_display::{
    compute_append_only_suffix, sanitize_for_display, DeltaRenderer, TextDeltaRenderer,
};
use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::ContentType;
use std::fmt::Write as FmtWrite;

#[cfg(any(test, debug_assertions))]
use std::io::Write;

impl crate::json_parser::claude::ClaudeParser {
    /// Handle standalone text delta events (not part of content blocks).
    ///
    /// Uses default index "0" for accumulation and follows append-only rendering.
    pub(in crate::json_parser::claude) fn handle_text_delta(
        &self,
        session: &mut std::cell::RefMut<'_, StreamingSession>,
        text: &str,
    ) -> String {
        let thinking_finalize = self.finalize_thinking_full_mode(session);
        *self.suppress_thinking_for_message.borrow_mut() = true;
        let c = &self.colors;
        let prefix = &self.display_name;

        let default_index = 0u64;
        let default_index_str = "0";

        let show_prefix = session.on_text_delta(default_index, text);

        let accumulated_text = session
            .get_accumulated(ContentType::Text, default_index_str)
            .unwrap_or("");

        let sanitized_text = sanitize_for_display(accumulated_text);

        if sanitized_text.is_empty() {
            return String::new();
        }

        if session.is_content_hash_rendered(ContentType::Text, default_index_str, &sanitized_text) {
            return String::new();
        }

        let terminal_mode = *self.terminal_mode.borrow();

        if terminal_mode == TerminalMode::Full {
            *self.text_line_active.borrow_mut() = true;
        }

        let has_prefix = session.has_rendered_prefix(ContentType::Text, default_index_str);

        let output = if terminal_mode == TerminalMode::Full {
            let key = format!("text:{default_index}");
            let last_rendered = self
                .last_rendered_content
                .borrow()
                .get(&key)
                .cloned()
                .unwrap_or_default();

            if last_rendered.is_empty() {
                let rendered = TextDeltaRenderer::render_first_delta(
                    accumulated_text,
                    prefix,
                    *c,
                    terminal_mode,
                );
                self.last_rendered_content
                    .borrow_mut()
                    .insert(key, sanitized_text.clone());
                rendered
            } else {
                let new_suffix = compute_append_only_suffix(&last_rendered, &sanitized_text);

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

                self.last_rendered_content
                    .borrow_mut()
                    .insert(key, sanitized_text.clone());

                format!("{}{}{}", c.white(), new_suffix, c.reset())
            }
        } else {
            if show_prefix && !has_prefix {
                TextDeltaRenderer::render_first_delta(accumulated_text, prefix, *c, terminal_mode)
            } else {
                TextDeltaRenderer::render_subsequent_delta(
                    accumulated_text,
                    prefix,
                    *c,
                    terminal_mode,
                )
            }
        };

        session.mark_rendered(ContentType::Text, default_index_str);
        session.mark_content_hash_rendered(ContentType::Text, default_index_str, &sanitized_text);

        format!("{thinking_finalize}{output}")
    }

    /// Handle message stop events - flush accumulated content in non-TTY modes.
    pub(in crate::json_parser::claude) fn handle_message_stop(
        &self,
        session: &mut std::cell::RefMut<'_, StreamingSession>,
    ) -> String {
        let c = &self.colors;

        let terminal_mode = *self.terminal_mode.borrow();

        let thinking_finalize = self.finalize_thinking_full_mode(session);

        let (thinking_flush_non_tty, tool_input_flush_non_tty, text_flush_non_tty) =
            match terminal_mode {
                TerminalMode::Full => (String::new(), String::new(), String::new()),
                TerminalMode::Basic | TerminalMode::None => {
                    if session
                        .get_current_message_id()
                        .is_some_and(|message_id| session.is_message_pre_rendered(message_id))
                    {
                        self.thinking_active_index.borrow_mut().take();
                        self.thinking_non_tty_indices.borrow_mut().clear();
                        (String::new(), String::new(), String::new())
                    } else {
                        let indices: Vec<u64> = if self.thinking_non_tty_indices.borrow().is_empty()
                        {
                            self.thinking_active_index
                                .borrow()
                                .iter()
                                .copied()
                                .collect()
                        } else {
                            self.thinking_non_tty_indices
                                .borrow()
                                .iter()
                                .copied()
                                .collect()
                        };

                        self.thinking_non_tty_indices.borrow_mut().clear();
                        self.thinking_active_index.borrow_mut().take();

                        let thinking_output = indices
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

                                let (prefix_fmt, label_fmt, suffix_fmt) = match terminal_mode {
                                    TerminalMode::Basic => (
                                        format!(
                                            "{}[{}]{} {}",
                                            c.dim(),
                                            &self.display_name,
                                            c.reset(),
                                            c.dim()
                                        ),
                                        format!("Thinking: {}", c.cyan()),
                                        c.reset().to_string(),
                                    ),
                                    TerminalMode::None => (
                                        format!("[{}] ", &self.display_name),
                                        "Thinking: ".to_string(),
                                        String::new(),
                                    ),
                                    TerminalMode::Full => unreachable!(),
                                };

                                Some(format!(
                                    "{}{}{}{}{}\n",
                                    prefix_fmt, label_fmt, sanitized, suffix_fmt
                                ))
                            })
                            .collect::<Vec<_>>()
                            .join("");

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

                                    let (prefix_fmt, label_fmt, suffix_fmt) = match terminal_mode {
                                        TerminalMode::Basic => (
                                            format!(
                                                "{}[{}]{} {}",
                                                c.dim(),
                                                &self.display_name,
                                                c.reset(),
                                                c.dim()
                                            ),
                                            format!("Tool input: {}", c.cyan()),
                                            c.reset().to_string(),
                                        ),
                                        TerminalMode::None => (
                                            format!("[{}] ", &self.display_name),
                                            "Tool input: ".to_string(),
                                            String::new(),
                                        ),
                                        TerminalMode::Full => unreachable!(),
                                    };

                                    Some(format!(
                                        "{}{}{}{}{}\n",
                                        prefix_fmt, label_fmt, sanitized, suffix_fmt
                                    ))
                                })
                                .collect::<Vec<_>>()
                                .join("")
                        } else {
                            String::new()
                        };

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

                                let (prefix_fmt, suffix_fmt) = match terminal_mode {
                                    TerminalMode::Basic => (
                                        format!(
                                            "{}[{}]{} {}",
                                            c.dim(),
                                            &self.display_name,
                                            c.reset(),
                                            c.white()
                                        ),
                                        c.reset().to_string(),
                                    ),
                                    TerminalMode::None => {
                                        (format!("[{}] ", &self.display_name), String::new())
                                    }
                                    TerminalMode::Full => unreachable!(),
                                };

                                Some(format!("{}{}{}{}\n", prefix_fmt, sanitized, suffix_fmt))
                            })
                            .collect::<Vec<_>>()
                            .join("");

                        (thinking_output, tool_output, text_output)
                    }
                }
            };

        let metrics = session.get_streaming_quality_metrics();
        let was_in_block = session.on_message_stop();

        let needs_text_completion = terminal_mode == TerminalMode::Full
            && (*self.text_line_active.borrow() || *self.cursor_up_active.borrow());
        let should_emit_completion = was_in_block || needs_text_completion;

        if should_emit_completion {
            if terminal_mode == TerminalMode::Full {
                *self.text_line_active.borrow_mut() = false;
                *self.cursor_up_active.borrow_mut() = false;
            }

            let completion = if terminal_mode == TerminalMode::Full {
                format!(
                    "{}{}",
                    c.reset(),
                    TextDeltaRenderer::render_completion(terminal_mode)
                )
            } else {
                String::new()
            };

            let show_metrics = (self.verbosity.is_debug() || self.show_streaming_metrics)
                && metrics.total_deltas > 0;
            let completion_with_metrics = if show_metrics {
                if terminal_mode == TerminalMode::Full {
                    format!("{}\n{}", completion, metrics.format(*c))
                } else {
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
