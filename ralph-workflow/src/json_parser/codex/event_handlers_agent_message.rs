#[cfg(debug_assertions)]
fn warn_if_delta_discontinuity_text(new_suffix: &str, last_rendered: &str, sanitized: &str) {
    if new_suffix.is_empty() && !last_rendered.is_empty() && !sanitized.is_empty() {
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

#[cfg(debug_assertions)]
fn warn_if_delta_discontinuity_thinking(new_suffix: &str, last_rendered: &str, sanitized: &str) {
    if new_suffix.is_empty() && !last_rendered.is_empty() && !sanitized.is_empty() {
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

fn render_agent_msg_subsequent_delta(
    ctx: &EventHandlerContext<'_>,
    key: String,
    last_rendered: &str,
    sanitized: &str,
) -> String {
    let new_suffix =
        crate::json_parser::delta_display::compute_append_only_suffix(last_rendered, sanitized);
    #[cfg(debug_assertions)]
    warn_if_delta_discontinuity_text(new_suffix, last_rendered, sanitized);
    ctx.with_last_rendered_content_mut(|v| {
        v.insert(key, sanitized.to_string());
    });
    if new_suffix.is_empty() {
        String::new()
    } else {
        format!("{}{}{}", ctx.colors.white(), new_suffix, ctx.colors.reset())
    }
}

fn render_agent_msg_full_mode(
    ctx: &EventHandlerContext<'_>,
    accumulated_text: &str,
    sanitized: &str,
) -> String {
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
            v.insert(key, sanitized.to_string());
        });
        rendered
    } else {
        render_agent_msg_subsequent_delta(ctx, key, &last_rendered, sanitized)
    }
}

fn render_agent_msg_no_text(ctx: &EventHandlerContext<'_>) -> String {
    if ctx.verbosity.is_verbose() {
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

fn render_agent_msg_text(ctx: &EventHandlerContext<'_>, text: &str) -> String {
    let accumulated_text = ctx.with_session_mut(|session| {
        session.on_text_delta_key("agent_msg", text);
        session
            .get_accumulated(ContentType::Text, "agent_msg")
            .unwrap_or("")
            .to_string()
    });
    let sanitized = crate::json_parser::delta_display::sanitize_for_display(&accumulated_text);
    if sanitized.is_empty() {
        return String::new();
    }
    if ctx.terminal_mode == TerminalMode::Full {
        render_agent_msg_full_mode(ctx, &accumulated_text, &sanitized)
    } else {
        String::new()
    }
}

pub fn handle_agent_message_started(
    ctx: &EventHandlerContext<'_>,
    text: Option<&String>,
) -> String {
    text.map_or_else(
        || render_agent_msg_no_text(ctx),
        |t| render_agent_msg_text(ctx, t),
    )
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
        |text| handle_reasoning_started_with_text(ctx, text),
    )
}

fn handle_reasoning_started_with_text(ctx: &EventHandlerContext<'_>, text: &str) -> String {
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
        render_reasoning_delta_full_mode(ctx, accumulated, sanitized)
    } else {
        // Basic/None mode: suppress per-delta output
        String::new()
    }
}

fn render_reasoning_delta_full_mode(
    ctx: &EventHandlerContext<'_>,
    accumulated: String,
    sanitized: String,
) -> String {
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
        render_reasoning_subsequent_delta(ctx, key, last_rendered, sanitized)
    }
}

fn emit_reasoning_suffix(ctx: &EventHandlerContext<'_>, new_suffix: &str) -> String {
    if new_suffix.is_empty() {
        String::new()
    } else {
        // Emit only the new suffix (no prefix, no cursor movement)
        // Use cyan color like ThinkingDeltaRenderer
        format!("{}{}{}", ctx.colors.cyan(), new_suffix, ctx.colors.reset())
    }
}

fn render_reasoning_subsequent_delta(
    ctx: &EventHandlerContext<'_>,
    key: String,
    last_rendered: String,
    sanitized: String,
) -> String {
    // Subsequent delta: emit ONLY new suffix
    let new_suffix =
        crate::json_parser::delta_display::compute_append_only_suffix(&last_rendered, &sanitized);

    #[cfg(debug_assertions)]
    warn_if_delta_discontinuity_thinking(new_suffix, &last_rendered, &sanitized);

    ctx.with_last_rendered_content_mut(|v| {
        v.insert(key, sanitized.clone());
    });

    emit_reasoning_suffix(ctx, new_suffix)
}

fn text_completion_for_mode(ctx: &EventHandlerContext<'_>) -> String {
    match ctx.terminal_mode {
        TerminalMode::Full => TextDeltaRenderer::render_completion(ctx.terminal_mode),
        TerminalMode::Basic | TerminalMode::None => String::new(),
    }
}

fn maybe_append_streaming_metrics(
    ctx: &EventHandlerContext<'_>,
    base: &str,
    metrics: &crate::json_parser::health::StreamingQualityMetrics,
) -> String {
    if (ctx.verbosity.is_debug() || ctx.show_streaming_metrics) && metrics.total_deltas > 0 {
        format!("{base}\n{}", metrics.format(*ctx.colors))
    } else {
        base.to_string()
    }
}

fn format_flushed_agent_msg_preview(ctx: &EventHandlerContext<'_>, preview: &str) -> String {
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
        TerminalMode::None => format!("[{}] {}\n", ctx.display_name, preview),
        TerminalMode::Full => unreachable!(),
    }
}

fn flush_streamed_agent_msg_non_full(ctx: &EventHandlerContext<'_>, msg: String) -> String {
    let preview = truncate_text(&msg, ctx.verbosity.truncate_limit("agent_msg"));
    if preview.is_empty() {
        String::new()
    } else {
        format_flushed_agent_msg_preview(ctx, &preview)
    }
}

fn flush_streamed_agent_msg(
    ctx: &EventHandlerContext<'_>,
    streamed_agent_msg: Option<String>,
) -> String {
    match ctx.terminal_mode {
        TerminalMode::Full => String::new(),
        TerminalMode::Basic | TerminalMode::None => streamed_agent_msg
            .map_or_else(String::new, |msg| {
                flush_streamed_agent_msg_non_full(ctx, msg)
            }),
    }
}

fn collect_agent_msg_completion_state(
    ctx: &EventHandlerContext<'_>,
) -> (
    bool,
    bool,
    crate::json_parser::health::StreamingQualityMetrics,
    Option<String>,
) {
    let session = ctx.streaming_session.borrow();
    let is_duplicate = session
        .get_current_message_id()
        .is_some_and(|id| session.is_duplicate_final_message(id));
    let was_streaming = session.has_any_streamed_content();
    let metrics = session.get_streaming_quality_metrics();
    let streamed = session
        .get_accumulated(ContentType::Text, "agent_msg")
        .map(std::string::ToString::to_string);
    (is_duplicate, was_streaming, metrics, streamed)
}

fn complete_streaming_agent_msg(
    ctx: &EventHandlerContext<'_>,
    streamed: Option<String>,
    metrics: &crate::json_parser::health::StreamingQualityMetrics,
) -> String {
    let completion = text_completion_for_mode(ctx);
    let flush = flush_streamed_agent_msg(ctx, streamed);
    ctx.streaming_session
        .borrow_mut()
        .clear_key(ContentType::Text, "agent_msg");
    maybe_append_streaming_metrics(ctx, &format!("{flush}{completion}"), metrics)
}

fn render_agent_msg_non_streaming(ctx: &EventHandlerContext<'_>, text: &str) -> String {
    let preview = truncate_text(text, ctx.verbosity.truncate_limit("agent_msg"));
    match ctx.terminal_mode {
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
    }
}

pub fn handle_agent_message_completed(
    ctx: &EventHandlerContext<'_>,
    text: Option<&String>,
) -> String {
    let (is_duplicate, was_streaming, metrics, streamed) = collect_agent_msg_completion_state(ctx);
    let _was_in_block = ctx.streaming_session.borrow_mut().on_message_stop();
    if is_duplicate {
        return maybe_append_streaming_metrics(ctx, &text_completion_for_mode(ctx), &metrics);
    }
    if was_streaming {
        return complete_streaming_agent_msg(ctx, streamed, &metrics);
    }
    text.map_or_else(String::new, |t| render_agent_msg_non_streaming(ctx, t))
}

fn render_reasoning_completed_full_mode(
    ctx: &EventHandlerContext<'_>,
    completion_text: Option<&str>,
) -> String {
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
                    let completion = ThinkingDeltaRenderer::render_completion(ctx.terminal_mode);
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

fn format_thinking_non_tty(ctx: &EventHandlerContext<'_>, sanitized: &str) -> String {
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
        TerminalMode::None => format!("[{}] Thinking: {}\n", ctx.display_name, sanitized),
        TerminalMode::Full => unreachable!(),
    }
}

fn format_thought_preview(ctx: &EventHandlerContext<'_>, preview: &str) -> String {
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
        TerminalMode::None => format!("[{}] Thought: {}\n", ctx.display_name, preview),
        TerminalMode::Full => unreachable!(),
    }
}

fn render_completion_text_non_tty(ctx: &EventHandlerContext<'_>, text: &str) -> String {
    if ctx.verbosity.is_verbose() {
        format_thought_preview(
            ctx,
            &truncate_text(text, ctx.verbosity.truncate_limit("text")),
        )
    } else {
        let sanitized = sanitize_for_display(text);
        if sanitized.is_empty() {
            String::new()
        } else {
            format_thinking_non_tty(ctx, &sanitized)
        }
    }
}

fn render_streamed_thinking_non_tty(ctx: &EventHandlerContext<'_>, thinking: String) -> String {
    let sanitized = sanitize_for_display(&thinking);
    if sanitized.is_empty() {
        String::new()
    } else {
        format_thinking_non_tty(ctx, &sanitized)
    }
}

fn render_reasoning_completed_non_tty(
    ctx: &EventHandlerContext<'_>,
    completion_text: Option<&str>,
) -> String {
    let streamed_thinking = {
        let session = ctx.streaming_session.borrow();
        session
            .get_accumulated(ContentType::Thinking, "reasoning")
            .map(std::string::ToString::to_string)
    };
    let rendered = streamed_thinking.map_or_else(
        || {
            completion_text.map_or_else(String::new, |text| {
                render_completion_text_non_tty(ctx, text)
            })
        },
        |thinking| render_streamed_thinking_non_tty(ctx, thinking),
    );
    ctx.streaming_session
        .borrow_mut()
        .clear_key(ContentType::Thinking, "reasoning");
    rendered
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
        TerminalMode::Full => render_reasoning_completed_full_mode(ctx, completion_text),
        TerminalMode::Basic | TerminalMode::None => {
            render_reasoning_completed_non_tty(ctx, completion_text)
        }
    }
}
