// Codex `item.completed` event handlers.
//
// This file implements completion handlers for Codex streaming items.
//
// # CCS Spam Prevention Architecture (Layer 3: Flush at Completion)
//
// These handlers implement the final layer of the three-layer spam prevention
// architecture for Codex agents (ccs/codex):
//
// ## Architecture Overview
//
// 1. **Layer 1 (Suppression):** Delta renderers return empty strings in non-TTY modes
//    - Implemented in `ralph-workflow/src/json_parser/delta_display/renderer_impls.rs`
//
// 2. **Layer 2 (Accumulation):** StreamingSession accumulates content across deltas
//    - Implemented in `ralph-workflow/src/json_parser/streaming_state/session.rs`
//
// 3. **Layer 3 (Flush):** Completion handlers flush accumulated content ONCE
//    - Implemented HERE in `handle_agent_message_completed` and `handle_reasoning_completed`
//
// ## Completion Strategy
//
// ### Full Mode (TTY)
// - Content was rendered using the append-only pattern during deltas
// - Emit a single completion newline (via renderer completion) to finalize the line
// - No content flush needed (already visible)
//
// ### Basic/None Modes (non-TTY)
// - Per-delta output was suppressed during streaming (Layer 1)
// - Content was accumulated in StreamingSession (Layer 2)
// - Now flush the final accumulated content ONCE with proper prefix
// - This ensures logs contain observable output without per-delta spam
//
// ## Validation
//
// Comprehensive regression tests validate this architecture:
// - `tests/integration_tests/ccs_delta_spam_systematic_reproduction.rs` - NEW: systematic reproduction & verification
// - `tests/integration_tests/ccs_comprehensive_spam_verification.rs` - Architecture verification
// - `tests/integration_tests/ccs_nuclear_spam_test.rs` - 500+ deltas with hard assertions
// - `tests/integration_tests/ccs_all_delta_types_spam_reproduction.rs` - 1000+ deltas
// - `tests/integration_tests/codex_reasoning_spam_regression.rs` - Original Codex fix
//
// ## Cross-References
//
// - Renderer suppression: `ralph-workflow/src/json_parser/delta_display/renderer_impls.rs`
// - Claude flush logic: `ralph-workflow/src/json_parser/claude/delta_handling.rs::handle_message_stop`
// - Codex delta handling: `ralph-workflow/src/json_parser/codex/event_handlers/items_started.rs`

/// Handle `ItemCompleted` event for `agent_message` type.
///
/// This handler implements Layer 3 (Flush) of the spam prevention architecture
/// for Codex `agent_message` items. In non-TTY modes, it flushes the accumulated
/// content that was suppressed during per-delta rendering.
///
/// # CCS Spam Prevention Architecture
///
/// 1. **Layer 1 (Suppression):** Renderer returned empty strings during deltas (non-TTY)
/// 2. **Layer 2 (Accumulation):** `StreamingSession` preserved content across deltas
/// 3. **Layer 3 (Flush):** This handler emits accumulated content ONCE at completion
///
/// See file-level documentation for details.
pub fn handle_agent_message_completed(
    ctx: &EventHandlerContext<'_>,
    text: Option<&String>,
) -> String {
    let (is_duplicate, was_streaming, metrics, streamed_agent_msg) = {
        let session = ctx.streaming_session.borrow();
        let is_duplicate = session
            .get_current_message_id()
            .is_some_and(|message_id| session.is_duplicate_final_message(message_id));
        let was_streaming = session.has_any_streamed_content();
        let metrics = session.get_streaming_quality_metrics();
        let streamed_agent_msg = session
            .get_accumulated(ContentType::Text, "agent_msg")
            .map(std::string::ToString::to_string);
        (is_duplicate, was_streaming, metrics, streamed_agent_msg)
    };

    let _was_in_block = ctx.streaming_session.borrow_mut().on_message_stop();

    // Duplicate completion events must be suppressed even if we streamed content.
    // Codex can emit duplicate `item.completed` events for the same message; if we
    // flush before checking duplication, we can print the final message twice.
    if is_duplicate {
        // Still finalize any cursor state (Full) and optionally emit metrics.
        // In Basic/None, do not emit an extra newline for suppressed duplicates.
        let completion = match ctx.terminal_mode {
            TerminalMode::Full => TextDeltaRenderer::render_completion(ctx.terminal_mode),
            TerminalMode::Basic | TerminalMode::None => String::new(),
        };
        let show_metrics =
            (ctx.verbosity.is_debug() || ctx.show_streaming_metrics) && metrics.total_deltas > 0;
        if show_metrics {
            return format!("{}\n{}", completion, metrics.format(*ctx.colors));
        }
        return completion;
    }

    // If we streamed any content, the per-delta renderer may have suppressed output in non-TTY
    // modes. Flush the final accumulated agent message ONCE at completion so logs remain
    // observable, while still preventing per-delta prefix spam.
    if was_streaming {
        // In Basic/None we already flush newline-terminated output below, so avoid appending an
        // additional completion newline (which would create a blank line in non-TTY logs).
        let completion = match ctx.terminal_mode {
            TerminalMode::Full => TextDeltaRenderer::render_completion(ctx.terminal_mode),
            TerminalMode::Basic | TerminalMode::None => String::new(),
        };
        let show_metrics =
            (ctx.verbosity.is_debug() || ctx.show_streaming_metrics) && metrics.total_deltas > 0;

        let flush = match ctx.terminal_mode {
            TerminalMode::Full => String::new(),
            TerminalMode::Basic | TerminalMode::None => {
                streamed_agent_msg.map_or_else(String::new, |msg| {
                    let limit = ctx.verbosity.truncate_limit("agent_msg");
                    let preview = truncate_text(&msg, limit);
                    if preview.is_empty() {
                        String::new()
                    } else {
                        // TerminalMode::None must be plain text even when colors are enabled.
                        match ctx.terminal_mode {
                            TerminalMode::Basic => format!(
                                "{}[{}]{} {}{}{}\n",
                                ctx.colors.dim(),
                                ctx.display_name,
                                ctx.colors.reset(),
                                ctx.colors.white(),
                                preview,
                                ctx.colors.reset()
                            ),
                            TerminalMode::None => {
                                format!("[{}] {}\n", ctx.display_name, preview)
                            }
                            TerminalMode::Full => unreachable!(),
                        }
                    }
                })
            }
        };

        // Clear the streaming key after first completion so duplicates have nothing to flush.
        ctx.streaming_session
            .borrow_mut()
            .clear_key(ContentType::Text, "agent_msg");

        let out = format!("{flush}{completion}");
        if show_metrics {
            format!("{}\n{}", out, metrics.format(*ctx.colors))
        } else {
            out
        }
    }

    if let Some(text) = text {
        let limit = ctx.verbosity.truncate_limit("agent_msg");
        let preview = truncate_text(text, limit);
        return match ctx.terminal_mode {
            TerminalMode::Full | TerminalMode::Basic => format!(
                "{}[{}]{} {}{}{}\n",
                ctx.colors.dim(),
                ctx.display_name,
                ctx.colors.reset(),
                ctx.colors.white(),
                preview,
                ctx.colors.reset()
            ),
            TerminalMode::None => format!("[{}] {}\n", ctx.display_name, preview),
        };
    }

    String::new()
}

/// Handle `ItemCompleted` event for `reasoning` type.
///
/// # Reasoning Completion Strategy (Bug Fix: Codex Thinking Spam)
///
/// This handler completes the reasoning spam fix by flushing accumulated content:
///
/// ## Full TTY Mode
/// - Reasoning is rendered using the append-only streaming contract during deltas
/// - Emit the completion newline to finalize the line
///
/// ## Non-TTY Modes (Basic/None)
/// - Per-delta output was suppressed during streaming
/// - Now flush the final accumulated thinking content **once** with "Thinking:" label
/// - If no streamed thinking exists, render the completion text once (non-spam),
///   and always clear the streaming key to avoid cross-item contamination.
///
/// ## Regression Test
/// See `tests/integration_tests/codex_reasoning_spam_regression.rs` for verification.
pub fn handle_reasoning_completed(ctx: &EventHandlerContext<'_>, text: Option<&String>) -> String {
    let full_reasoning = ctx
        .reasoning_accumulator
        .borrow()
        .get(ContentType::Thinking, "reasoning")
        .map(std::string::ToString::to_string);
    ctx.reasoning_accumulator
        .borrow_mut()
        .clear_key(ContentType::Thinking, "reasoning");

    let completion_text = full_reasoning
        .as_deref()
        .or_else(|| text.map(std::string::String::as_str));

    match ctx.terminal_mode {
        TerminalMode::Full => {
            // In Full mode, most reasoning arrives via deltas rendered in-place.
            // If Codex provides reasoning only at completion, render it once here.
            let streamed_thinking = {
                let session = ctx.streaming_session.borrow();
                session
                    .get_accumulated(ContentType::Thinking, "reasoning")
                    .map(std::string::ToString::to_string)
            };

            let result = streamed_thinking.map_or_else(
                || {
                    completion_text.map_or_else(String::new, |text| {
                        let sanitized = sanitize_for_display(text);
                        if sanitized.is_empty() {
                            String::new()
                        } else {
                            let rendered = ThinkingDeltaRenderer::render_first_delta(
                                &sanitized,
                                ctx.display_name,
                                *ctx.colors,
                                ctx.terminal_mode,
                            );
                            let completion =
                                ThinkingDeltaRenderer::render_completion(ctx.terminal_mode);
                            format!("{rendered}{completion}")
                        }
                    })
                },
                |thinking| {
                    if thinking.is_empty() {
                        String::new()
                    } else {
                        ThinkingDeltaRenderer::render_completion(ctx.terminal_mode)
                    }
                },
            );

            ctx.streaming_session
                .borrow_mut()
                .clear_key(ContentType::Thinking, "reasoning");
            result
        }
        TerminalMode::Basic | TerminalMode::None => {
            // In non-TTY modes, suppress per-delta output and flush once at completion.
            //
            // If we received streamed reasoning deltas, flush the accumulated thinking once.
            // If reasoning arrives only at completion (no deltas), preserve the existing
            // verbose-mode "Thought:" summary behavior.
            let streamed_thinking = {
                let session = ctx.streaming_session.borrow();
                session
                    .get_accumulated(ContentType::Thinking, "reasoning")
                    .map(std::string::ToString::to_string)
            };

            // Format the output directly because the renderers now suppress
            // output in non-TTY modes (to prevent per-delta spam).
            let rendered = streamed_thinking.map_or_else(
                || {
                    completion_text.map_or_else(String::new, |text| {
                        if ctx.verbosity.is_verbose() {
                            let limit = ctx.verbosity.truncate_limit("text");
                            let preview = truncate_text(text, limit);
                            match ctx.terminal_mode {
                                TerminalMode::Basic => format!(
                                    "{}[{}]{} {}Thought:{} {}{}{}\n",
                                    ctx.colors.dim(),
                                    ctx.display_name,
                                    ctx.colors.reset(),
                                    ctx.colors.cyan(),
                                    ctx.colors.reset(),
                                    ctx.colors.dim(),
                                    preview,
                                    ctx.colors.reset()
                                ),
                                TerminalMode::None => {
                                    format!("[{}] Thought: {}\n", ctx.display_name, preview)
                                }
                                TerminalMode::Full => unreachable!(),
                            }
                        } else {
                            let sanitized = sanitize_for_display(text);
                            if sanitized.is_empty() {
                                String::new()
                            } else {
                                // TerminalMode::None must be plain text even when colors are enabled.
                                match ctx.terminal_mode {
                                    TerminalMode::Basic => format!(
                                        "{}[{}]{} {}Thinking: {}{}{}\n",
                                        ctx.colors.dim(),
                                        ctx.display_name,
                                        ctx.colors.reset(),
                                        ctx.colors.dim(),
                                        ctx.colors.cyan(),
                                        sanitized,
                                        ctx.colors.reset()
                                    ),
                                    TerminalMode::None => {
                                        format!("[{}] Thinking: {}\n", ctx.display_name, sanitized)
                                    }
                                    TerminalMode::Full => unreachable!(),
                                }
                            }
                        }
                    })
                },
                |thinking| {
                    let sanitized = sanitize_for_display(&thinking);
                    if sanitized.is_empty() {
                        String::new()
                    } else {
                        // TerminalMode::None must be plain text even when colors are enabled.
                        match ctx.terminal_mode {
                            TerminalMode::Basic => format!(
                                "{}[{}]{} {}Thinking: {}{}{}\n",
                                ctx.colors.dim(),
                                ctx.display_name,
                                ctx.colors.reset(),
                                ctx.colors.dim(),
                                ctx.colors.cyan(),
                                sanitized,
                                ctx.colors.reset()
                            ),
                            TerminalMode::None => {
                                format!("[{}] Thinking: {}\n", ctx.display_name, sanitized)
                            }
                            TerminalMode::Full => unreachable!(),
                        }
                    }
                },
            );

            // Always clear key-scoped streaming state, even if the rendered output is empty.
            ctx.streaming_session
                .borrow_mut()
                .clear_key(ContentType::Thinking, "reasoning");

            rendered
        }
    }
}

/// Handle `ItemCompleted` event for `command_execution` type.
pub fn handle_command_execution_completed(ctx: &EventHandlerContext<'_>) -> String {
    match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}{} Command done{}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.green(),
            CHECK,
            ctx.colors.reset()
        ),
        TerminalMode::None => format!("[{}] Command done\n", ctx.display_name),
    }
}

/// Handle `ItemCompleted` event for `file_change`/`file_write` types.
pub fn handle_file_write_completed(ctx: &EventHandlerContext<'_>, path: Option<String>) -> String {
    let path = path.unwrap_or_else(|| "unknown".to_string());
    match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}File{}: {}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.yellow(),
            ctx.colors.reset(),
            path
        ),
        TerminalMode::None => format!("[{}] File: {}\n", ctx.display_name, path),
    }
}

/// Handle `ItemCompleted` event for `file_read` type.
pub fn handle_file_read_completed(ctx: &EventHandlerContext<'_>, path: Option<String>) -> String {
    if ctx.verbosity.is_verbose() {
        let path = path.unwrap_or_else(|| "unknown".to_string());
        format!(
            "{}[{}]{} {}{} Read:{} {}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.green(),
            CHECK,
            ctx.colors.reset(),
            path
        )
    } else {
        String::new()
    }
}

/// Handle `ItemCompleted` event for `mcp_tool_call`/`mcp` types.
pub fn handle_mcp_tool_completed(
    ctx: &EventHandlerContext<'_>,
    tool_name: Option<String>,
) -> String {
    let tool_name = tool_name.unwrap_or_else(|| "tool".to_string());
    match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}{} MCP:{} {} done\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.green(),
            CHECK,
            ctx.colors.reset(),
            tool_name
        ),
        TerminalMode::None => format!("[{}] MCP: {} done\n", ctx.display_name, tool_name),
    }
}

/// Handle `ItemCompleted` event for `web_search` type.
pub fn handle_web_search_completed(ctx: &EventHandlerContext<'_>) -> String {
    match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}{} Search completed{}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.green(),
            CHECK,
            ctx.colors.reset()
        ),
        TerminalMode::None => format!("[{}] Search completed\n", ctx.display_name),
    }
}

/// Handle `ItemCompleted` event for `plan_update` type.
pub fn handle_plan_update_completed(
    ctx: &EventHandlerContext<'_>,
    plan: Option<&String>,
) -> String {
    if ctx.verbosity.is_verbose() {
        let limit = ctx.verbosity.truncate_limit("text");
        plan.map_or_else(
            || match ctx.terminal_mode {
                TerminalMode::Full | TerminalMode::Basic => format!(
                    "{}[{}]{} {}{} Plan updated{}\n",
                    ctx.colors.dim(),
                    ctx.display_name,
                    ctx.colors.reset(),
                    ctx.colors.green(),
                    CHECK,
                    ctx.colors.reset()
                ),
                TerminalMode::None => format!("[{}] Plan updated\n", ctx.display_name),
            },
            |plan| {
                let preview = truncate_text(plan, limit);
                match ctx.terminal_mode {
                    TerminalMode::Full | TerminalMode::Basic => format!(
                        "{}[{}]{} {}Plan:{} {}\n",
                        ctx.colors.dim(),
                        ctx.display_name,
                        ctx.colors.reset(),
                        ctx.colors.blue(),
                        ctx.colors.reset(),
                        preview
                    ),
                    TerminalMode::None => format!("[{}] Plan: {}\n", ctx.display_name, preview),
                }
            },
        )
    } else {
        String::new()
    }
}
