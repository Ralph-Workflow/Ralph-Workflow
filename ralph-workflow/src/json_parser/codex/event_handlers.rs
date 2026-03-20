use crate::common::truncate_text;
use crate::json_parser::codex::event_interpretation::{
    compute_reasoning_incremental_delta, interpret_item_completed_type,
    interpret_item_started_type, ItemCompletedInterpretation, ItemStartedInterpretation,
};
use crate::json_parser::delta_display::{
    sanitize_for_display, DeltaRenderer, TextDeltaRenderer, ThinkingDeltaRenderer,
};
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::{format_tool_input, CodexItem, CodexUsage, ContentType};
use crate::logger::{CHECK, CROSS};
pub struct EventHandlerContext<'a> {
    pub colors: &'a crate::logger::Colors,
    pub verbosity: crate::config::Verbosity,
    pub display_name: &'a str,
    pub streaming_session:
        &'a std::rc::Rc<std::cell::RefCell<crate::json_parser::streaming_state::StreamingSession>>,
    pub reasoning_accumulator:
        &'a std::rc::Rc<std::cell::RefCell<crate::json_parser::types::DeltaAccumulator>>,
    pub terminal_mode: crate::json_parser::terminal::TerminalMode,
    pub show_streaming_metrics: bool,
    pub last_rendered_content: &'a std::cell::RefCell<std::collections::HashMap<String, String>>,
}

impl<'a> EventHandlerContext<'a> {
    pub fn with_session_mut<R>(
        &self,
        f: impl FnOnce(&mut crate::json_parser::streaming_state::StreamingSession) -> R,
    ) -> R {
        f(&mut self.streaming_session.borrow_mut())
    }
    pub fn with_reasoning_accumulator_mut<R>(
        &self,
        f: impl FnOnce(&mut crate::json_parser::types::DeltaAccumulator) -> R,
    ) -> R {
        f(&mut self.reasoning_accumulator.borrow_mut())
    }
    pub fn with_last_rendered_content_mut<R>(
        &self,
        f: impl FnOnce(&mut std::collections::HashMap<String, String>) -> R,
    ) -> R {
        f(&mut self.last_rendered_content.borrow_mut())
    }
}
pub fn handle_item_started(
    ctx: &EventHandlerContext<'_>,
    item: Option<&CodexItem>,
) -> Option<String> {
    item.and_then(
        |item| match interpret_item_started_type(item.item_type.as_deref()) {
            Some(ItemStartedInterpretation::CommandExecution) => {
                let output = handle_command_execution_started(ctx, item.command.clone());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::AgentMessage) => {
                Some(handle_agent_message_started(ctx, item.text.as_ref()))
            }
            Some(ItemStartedInterpretation::Reasoning) => {
                Some(handle_reasoning_started(ctx, item.text.as_ref()))
            }
            Some(ItemStartedInterpretation::FileRead) => {
                let output = handle_file_io_started(ctx, item.path.clone(), "file_read");
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::FileWrite) => {
                let output = handle_file_io_started(ctx, item.path.clone(), "file_write");
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::McpTool) => {
                let output =
                    handle_mcp_tool_started(ctx, item.tool.as_ref(), item.arguments.as_ref());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::WebSearch) => {
                let output = handle_web_search_started(ctx, item.query.as_ref());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::PlanUpdate) => {
                let output = handle_plan_update_started(ctx);
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::Unknown(item_type)) => {
                let output = handle_unknown_item_started(ctx, Some(item_type), item.path.clone());
                (!output.is_empty()).then_some(output)
            }
            None => None,
        },
    )
}
pub fn handle_item_completed(
    ctx: &EventHandlerContext<'_>,
    item: Option<&CodexItem>,
) -> Option<String> {
    item.and_then(
        |item| match interpret_item_completed_type(item.item_type.as_deref()) {
            Some(ItemCompletedInterpretation::AgentMessage) => {
                Some(handle_agent_message_completed(ctx, item.text.as_ref()))
            }
            Some(ItemCompletedInterpretation::Reasoning) => {
                Some(handle_reasoning_completed(ctx, item.text.as_ref()))
            }
            Some(ItemCompletedInterpretation::CommandExecution) => {
                let output = handle_command_execution_completed(ctx);
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::FileWrite) => {
                let output = handle_file_write_completed(ctx, item.path.clone());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::FileRead) => {
                let output = handle_file_read_completed(ctx, item.path.clone());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::McpTool) => {
                let output = handle_mcp_tool_completed(ctx, item.tool.clone());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::WebSearch) => {
                let output = handle_web_search_completed(ctx);
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::PlanUpdate) => {
                let output = handle_plan_update_completed(ctx, item.plan.as_ref());
                (!output.is_empty()).then_some(output)
            }
            _ => None,
        },
    )
}
pub fn handle_thread_started(ctx: &EventHandlerContext<'_>, thread_id: Option<String>) -> String {
    let tid = thread_id.unwrap_or_else(|| "unknown".to_string());

    // Thread start indicates a new logical stream in Codex; reset any append-only tracking
    // so subsequent deltas start fresh.
    ctx.last_rendered_content.borrow_mut().clear();

    ctx.streaming_session
        .borrow_mut()
        .set_current_message_id(Some(tid.clone()));
    format!(
        "{}[{}]{} {}Thread started{} {}({:.8}...){}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.cyan(),
        ctx.colors.reset(),
        ctx.colors.dim(),
        tid,
        ctx.colors.reset()
    )
}

pub fn handle_turn_started(ctx: &EventHandlerContext<'_>, turn_id: String) -> String {
    ctx.streaming_session.borrow_mut().on_message_start();
    let mut acc = ctx.reasoning_accumulator.borrow_mut();
    let placeholder = crate::json_parser::types::DeltaAccumulator::new();
    let old = std::mem::replace(&mut *acc, placeholder);
    let new = old.clear();
    *acc = new;

    // Each Codex turn is a new logical stream. Clear append-only renderer state so the
    // first delta of the new turn re-emits the prefix/label instead of computing a suffix
    // against the previous turn's content.
    ctx.last_rendered_content.borrow_mut().clear();

    ctx.streaming_session
        .borrow_mut()
        .set_current_message_id(Some(turn_id));
    format!(
        "{}[{}]{} {}Turn started{}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.blue(),
        ctx.colors.reset()
    )
}

pub fn handle_turn_completed(ctx: &EventHandlerContext<'_>, usage: Option<CodexUsage>) -> String {
    let was_in_block = ctx.streaming_session.borrow_mut().on_message_stop();
    let (input, output) = usage.map_or((0, 0), |u| {
        (u.input_tokens.unwrap_or(0), u.output_tokens.unwrap_or(0))
    });
    let completion = if was_in_block {
        TextDeltaRenderer::render_completion(ctx.terminal_mode)
    } else {
        String::new()
    };
    format!(
        "{}{}[{}]{} {}{} Turn completed{} {}(in:{} out:{}){}\n",
        completion,
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.green(),
        CHECK,
        ctx.colors.reset(),
        ctx.colors.dim(),
        input,
        output,
        ctx.colors.reset()
    )
}

pub fn handle_turn_failed(ctx: &EventHandlerContext<'_>, error: Option<String>) -> String {
    let was_in_block = ctx.streaming_session.borrow_mut().on_message_stop();
    let completion = if was_in_block {
        TextDeltaRenderer::render_completion(ctx.terminal_mode)
    } else {
        String::new()
    };
    let err = error.unwrap_or_else(|| "unknown error".to_string());
    format!(
        "{}{}[{}]{} {}{} Turn failed:{} {}\n",
        completion,
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.red(),
        CROSS,
        ctx.colors.reset(),
        err
    )
}

#[cfg(any(test, debug_assertions))]
use std::io::Write;
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
        let sanitized = crate::json_parser::delta_display::sanitize_for_display(&accumulated_text);

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
                    &accumulated_text,
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

                let delta = compute_reasoning_incremental_delta(&previous_content, text);

                // Only send the incremental delta to the session
                session.on_thinking_delta_key("reasoning", &delta);
                (
                    delta,
                    session
                        .get_accumulated(ContentType::Thinking, "reasoning")
                        .unwrap_or("")
                        .to_string(),
                )
            });

            // Accumulate for backward compatibility with reasoning_completed
            // For backward compat, use the full text not just delta
            ctx.with_reasoning_accumulator_mut(|acc| {
                let placeholder = crate::json_parser::types::DeltaAccumulator::new();
                let old = std::mem::replace(acc, placeholder);
                let new = old.add_delta(ContentType::Thinking, "reasoning", &incremental_delta);
                *acc = new;
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
        return if show_metrics {
            format!("{}\n{}", out, metrics.format(*ctx.colors))
        } else {
            out
        };
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

pub fn handle_reasoning_completed(ctx: &EventHandlerContext<'_>, text: Option<&String>) -> String {
    let full_reasoning = ctx
        .reasoning_accumulator
        .borrow()
        .get(ContentType::Thinking, "reasoning")
        .map(std::string::ToString::to_string);
    let mut acc = ctx.reasoning_accumulator.borrow_mut();
    let placeholder = crate::json_parser::types::DeltaAccumulator::new();
    let old = std::mem::replace(&mut *acc, placeholder);
    let new = old.clear_key(ContentType::Thinking, "reasoning");
    *acc = new;

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

pub fn handle_error(
    ctx: &EventHandlerContext<'_>,
    message: Option<String>,
    error: Option<String>,
) -> String {
    let err = message
        .or(error)
        .unwrap_or_else(|| "unknown error".to_string());
    format!(
        "{}[{}]{} {}{} Error:{} {}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.red(),
        CROSS,
        ctx.colors.reset(),
        err
    )
}
