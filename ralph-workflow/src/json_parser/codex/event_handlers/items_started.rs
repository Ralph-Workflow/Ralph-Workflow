// Codex `item.started` event handlers.
//
// This file implements delta handlers for Codex streaming items.

#[cfg(any(test, debug_assertions))]
use std::io::Write;

// # CCS Spam Prevention Architecture (Layers 1 & 2: Suppress & Accumulate)
//
// These handlers work with the delta renderers to implement the first two layers
// of the spam prevention architecture for Codex agents (ccs/codex):
//
// ## Architecture Overview
//
// 1. **Layer 1 (Suppression):** Delta renderers return empty strings in non-TTY modes
//    - Called by these handlers via `TextDeltaRenderer::render_*` and `ThinkingDeltaRenderer::render_*`
//    - Implemented in `ralph-workflow/src/json_parser/delta_display/renderer_impls.rs`
//
// 2. **Layer 2 (Accumulation):** StreamingSession accumulates content across deltas
//    - These handlers call `session.on_text_delta_key()` and `session.on_thinking_delta_key()`
//    - Content is preserved for flush at completion
//
// 3. **Layer 3 (Flush):** Completion handlers flush accumulated content ONCE
//    - Implemented in `ralph-workflow/src/json_parser/codex/event_handlers/items_completed.rs`
//
// ## Delta Rendering Strategy
//
// ### Full Mode (TTY)
// - True append-only streaming (NO cursor movement)
// - First delta emits prefix + accumulated content (no newline)
// - Subsequent deltas emit ONLY the new suffix (no prefix rewrite)
// - Completion emits a single newline to finalize the line
//
// ### Basic/None Modes (non-TTY)
// - Each delta calls renderer which returns empty string (suppression at Layer 1)
// - Content is accumulated in StreamingSession (Layer 2)
// - No output until completion boundary (Layer 3)
//
// ## Validation
//
// See comprehensive regression tests:
// - `tests/integration_tests/ccs_comprehensive_spam_verification.rs` - Architecture verification
// - `tests/integration_tests/ccs_nuclear_spam_test.rs` - 500+ deltas with hard assertions
// - `tests/integration_tests/ccs_all_delta_types_spam_reproduction.rs` - 1000+ deltas
// - `tests/integration_tests/codex_reasoning_spam_regression.rs` - Original Codex fix
//
// ## Cross-References
//
// - Renderer suppression: `ralph-workflow/src/json_parser/delta_display/renderer_impls.rs`
// - Completion flush: `ralph-workflow/src/json_parser/codex/event_handlers/items_completed.rs`
// - Claude equivalent: `ralph-workflow/src/json_parser/claude/delta_handling.rs`

/// Handle `ItemStarted` event for `agent_message` type.
///
/// This handler implements the append-only streaming pattern for Codex `agent_message`
/// items, avoiding cursor movement to support wrapping and ANSI-stripping environments.
///
/// # Append-Only Pattern (Full Mode)
///
/// 1. **First delta**: Shows prefix with accumulated content, NO newline
/// 2. **Subsequent deltas**: Emits ONLY new suffix (no prefix, no cursor movement)
/// 3. **Completion**: Single newline to finalize the line
///
/// This pattern works correctly under terminal wrapping because there is NO cursor
/// movement. The terminal naturally handles wrapping, and content appears to grow
/// incrementally on the same logical line.
///
/// # Non-TTY Modes (Basic/None)
///
/// Per-delta output is suppressed. Content is flushed ONCE at completion boundaries
/// by the parser layer to prevent spam in logs and CI output.
///
/// See file-level documentation for details.
pub fn handle_agent_message_started(
    ctx: &EventHandlerContext<'_>,
    text: Option<&String>,
) -> String {
    if let Some(text) = text {
        let accumulated_text = ctx.with_session_mut(|session| {
            session.on_text_delta_key("agent_msg", text);
            session
                .get_accumulated(ContentType::Text, "agent_msg")
                .unwrap_or("")
                .to_string()
        });

        // Sanitize for display
        let sanitized = crate::json_parser::delta_display::sanitize_for_display(accumulated_text);

        // Skip rendering if empty
        if sanitized.is_empty() {
            return String::new();
        }

        // Append-only pattern in Full mode
        if ctx.terminal_mode == TerminalMode::Full {
            let key = "text:agent_msg".to_string();
            let last_rendered = ctx
                .last_rendered_content
                .borrow()
                .get(&key)
                .cloned()
                .unwrap_or_default();

            if last_rendered.is_empty() {
                // First delta: emit prefix + content (no newline)
                let rendered = TextDeltaRenderer::render_first_delta(
                    accumulated_text,
                    ctx.display_name,
                    *ctx.colors,
                    ctx.terminal_mode,
                );
                ctx.with_last_rendered_content_mut(|v| {
                    v.insert(key, sanitized);
                });
                rendered
            } else {
                // Subsequent delta: emit ONLY new suffix
                let new_suffix = crate::json_parser::delta_display::compute_append_only_suffix(
                    &last_rendered,
                    &sanitized,
                );

                // Detect discontinuities
                if new_suffix.is_empty() && !last_rendered.is_empty() && !sanitized.is_empty() {
                    #[cfg(debug_assertions)]
                    {
                        let _ = writeln!(
                            std::io::stderr(),
                            "Warning: Delta discontinuity detected in Codex text item. \
                             Provider sent non-monotonic content. \
                             Last: {:?} (len={}), Current: {:?} (len={})",
                            &last_rendered[..last_rendered.len().min(40)],
                            last_rendered.len(),
                            &sanitized[..sanitized.len().min(40)],
                            sanitized.len()
                        );
                    }
                }

                ctx.with_last_rendered_content_mut(|v| {
                    v.insert(key, sanitized.clone());
                });

                if new_suffix.is_empty() {
                    String::new()
                } else {
                    // Emit only the new suffix (no prefix, no cursor movement)
                    format!("{}{}{}", ctx.colors.white(), new_suffix, ctx.colors.reset())
                }
            }
        } else {
            // Basic/None mode: suppress per-delta output
            String::new()
        }
    } else if ctx.verbosity.is_verbose() {
        String::new()
    } else {
        format!(
            "{}[{}]{} {}Thinking...{}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.blue(),
            ctx.colors.reset()
        )
    }
}

/// Handle `ItemStarted` event for `reasoning` type.
///
/// # Reasoning Output Strategy (Bug Fix: Codex Thinking Spam)
///
/// This handler implements the append-only streaming pattern for Codex `reasoning`
/// items, avoiding cursor movement to support wrapping and ANSI-stripping environments.
///
/// ## Append-Only Pattern (Full Mode)
///
/// 1. **First delta**: Shows prefix + "Thinking: " + content (no newline)
/// 2. **Subsequent deltas**: Emits ONLY new suffix (no prefix, no cursor movement)
/// 3. **Completion**: Single newline to finalize the line
///
/// This pattern works correctly under terminal wrapping because there is NO cursor
/// movement. The terminal naturally handles wrapping, and content appears to grow
/// incrementally on the same logical line.
///
/// ## Non-TTY Modes (Basic/None)
///
/// Per-delta output is suppressed. Content is flushed ONCE at completion boundaries
/// to prevent spam in logs and CI output.
///
/// ## State Tracking
/// - Uses `StreamingSession::on_thinking_delta_key("reasoning", ...)` to track deltas
/// - Accumulates in `reasoning_accumulator` for backward compatibility with completion handler
///
/// ## Regression Test
/// See `tests/integration_tests/codex_reasoning_spam_regression.rs` for verification.
pub fn handle_reasoning_started(ctx: &EventHandlerContext<'_>, text: Option<&String>) -> String {
    text.map_or_else(
        || {
            if ctx.verbosity.is_verbose() {
                format!(
                    "{}[{}]{} {}Reasoning...{}\n",
                    ctx.colors.dim(),
                    ctx.display_name,
                    ctx.colors.reset(),
                    ctx.colors.cyan(),
                    ctx.colors.reset()
                )
            } else {
                String::new()
            }
        },
        |text| {
            // Codex sends FULL accumulated content in each item.started event (snapshot-style),
            // not incremental deltas like Claude. We need to compute the incremental delta here.
            let (incremental_delta, accumulated) = ctx.with_session_mut(|session| {
                let previous_content = session
                    .get_accumulated(ContentType::Thinking, "reasoning")
                    .unwrap_or("")
                    .to_string();

                // Compute incremental delta: if new content extends previous, extract the suffix
                let delta = if text.starts_with(&previous_content) {
                    &text[previous_content.len()..]
                } else {
                    // New content doesn't extend previous (replace or first delta)
                    text.as_str()
                };

                // Only send the incremental delta to the session
                session.on_thinking_delta_key("reasoning", delta);
                (
                    delta.to_string(),
                    session
                        .get_accumulated(ContentType::Thinking, "reasoning")
                        .unwrap_or("")
                        .to_string(),
                )
            });

            // Accumulate for backward compatibility with reasoning_completed
            // For backward compat, use the full text not just delta
            ctx.with_reasoning_accumulator_mut(|acc| {
                acc.add_delta(ContentType::Thinking, "reasoning", &incremental_delta);
            });

            // Sanitize for display
            let sanitized = crate::json_parser::delta_display::sanitize_for_display(&accumulated);

            // Append-only pattern in Full mode
            if ctx.terminal_mode == TerminalMode::Full {
                let key = "thinking:reasoning".to_string();
                let last_rendered = ctx
                    .last_rendered_content
                    .borrow()
                    .get(&key)
                    .cloned()
                    .unwrap_or_default();

                if last_rendered.is_empty() {
                    // First delta: emit prefix + "Thinking: " + content (no newline)
                    let rendered = ThinkingDeltaRenderer::render_first_delta(
                        &accumulated,
                        ctx.display_name,
                        *ctx.colors,
                        ctx.terminal_mode,
                    );
                    ctx.with_last_rendered_content_mut(|v| {
                        v.insert(key, sanitized);
                    });
                    rendered
                } else {
                    // Subsequent delta: emit ONLY new suffix
                    let new_suffix = crate::json_parser::delta_display::compute_append_only_suffix(
                        &last_rendered,
                        &sanitized,
                    );

                    // Detect discontinuities in thinking deltas
                    if new_suffix.is_empty() && !last_rendered.is_empty() && !sanitized.is_empty() {
                        #[cfg(debug_assertions)]
                        {
                            let _ = writeln!(
                                std::io::stderr(),
                                "Warning: Delta discontinuity detected in Codex thinking item. \
                             Provider sent non-monotonic content. \
                             Last: {:?} (len={}), Current: {:?} (len={})",
                                &last_rendered[..last_rendered.len().min(40)],
                                last_rendered.len(),
                                &sanitized[..sanitized.len().min(40)],
                                sanitized.len()
                            );
                        }
                    }

                    ctx.with_last_rendered_content_mut(|v| {
                        v.insert(key, sanitized.clone());
                    });

                    if new_suffix.is_empty() {
                        String::new()
                    } else {
                        // Emit only the new suffix (no prefix, no cursor movement)
                        // Use cyan color like ThinkingDeltaRenderer
                        format!("{}{}{}", ctx.colors.cyan(), new_suffix, ctx.colors.reset())
                    }
                }
            } else {
                // Basic/None mode: suppress per-delta output
                String::new()
            }
        },
    )
}

/// Handle `ItemStarted` event for `command_execution` type.
pub fn handle_command_execution_started(
    ctx: &EventHandlerContext<'_>,
    command: Option<String>,
) -> String {
    let cmd = command.unwrap_or_default();
    let limit = ctx.verbosity.truncate_limit("command");
    let preview = truncate_text(&cmd, limit);
    format!(
        "{}[{}]{} {}Exec{}: {}{}{}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.magenta(),
        ctx.colors.reset(),
        ctx.colors.white(),
        preview,
        ctx.colors.reset()
    )
}

/// Handle `ItemStarted` event for `file_read`/`file_write` types.
pub fn handle_file_io_started(
    ctx: &EventHandlerContext<'_>,
    path: Option<String>,
    action: &str,
) -> String {
    let path = path.unwrap_or_default();
    format!(
        "{}[{}]{} {}{}:{} {}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.yellow(),
        action,
        ctx.colors.reset(),
        path
    )
}

/// Handle `ItemStarted` event for `mcp_tool_call`/`mcp` types.
pub fn handle_mcp_tool_started(
    ctx: &EventHandlerContext<'_>,
    tool_name: Option<&String>,
    arguments: Option<&serde_json::Value>,
) -> String {
    let default = String::from("unknown");
    let tool_name = tool_name.unwrap_or(&default);

    let base = match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}MCP Tool{}: {}{}{}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.magenta(),
            ctx.colors.reset(),
            ctx.colors.bold(),
            tool_name,
            ctx.colors.reset()
        ),
        TerminalMode::None => format!("[{}] MCP Tool: {}\n", ctx.display_name, tool_name),
    };

    let tool_input = if ctx.verbosity.show_tool_input() {
        if let Some(args) = arguments {
            let args_str = format_tool_input(args);
            let limit = ctx.verbosity.truncate_limit("tool_input");
            let preview = truncate_text(&args_str, limit);
            if preview.is_empty() {
                String::new()
            } else {
                match ctx.terminal_mode {
                    TerminalMode::Full | TerminalMode::Basic => format!(
                        "{}[{}]{} {}  └─ {}{}{}\n",
                        ctx.colors.dim(),
                        ctx.display_name,
                        ctx.colors.reset(),
                        ctx.colors.dim(),
                        ctx.colors.reset(),
                        preview,
                        ctx.colors.reset()
                    ),
                    TerminalMode::None => format!("[{}]   └─ {}\n", ctx.display_name, preview),
                }
            }
        } else {
            String::new()
        }
    } else {
        String::new()
    };

    format!("{base}{tool_input}")
}

/// Handle `ItemStarted` event for `web_search` type.
pub fn handle_web_search_started(ctx: &EventHandlerContext<'_>, query: Option<&String>) -> String {
    let default = String::new();
    let query = query.unwrap_or(&default);
    let limit = ctx.verbosity.truncate_limit("command");
    let preview = truncate_text(query, limit);
    format!(
        "{}[{}]{} {}Search{}: {}{}{}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.cyan(),
        ctx.colors.reset(),
        ctx.colors.white(),
        preview,
        ctx.colors.reset()
    )
}

/// Handle `ItemStarted` event for `plan_update` type.
pub fn handle_plan_update_started(ctx: &EventHandlerContext<'_>) -> String {
    format!(
        "{}[{}]{} {}Updating plan...{}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.blue(),
        ctx.colors.reset()
    )
}

/// Handle `ItemStarted` event for unknown/other types.
pub fn handle_unknown_item_started(
    ctx: &EventHandlerContext<'_>,
    item_type: Option<String>,
    path: Option<String>,
) -> String {
    if ctx.verbosity.is_verbose() {
        if let Some(t) = item_type {
            return format!(
                "{}[{}]{} {}{}:{} {}\n",
                ctx.colors.dim(),
                ctx.display_name,
                ctx.colors.reset(),
                ctx.colors.dim(),
                t,
                ctx.colors.reset(),
                path.unwrap_or_default()
            );
        }
    }
    String::new()
}
