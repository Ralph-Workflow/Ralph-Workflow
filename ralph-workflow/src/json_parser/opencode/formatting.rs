// Step lifecycle, text, error, and tool formatting.

struct StepFinishRenderContext<'a> {
    is_duplicate: bool,
    was_streaming: bool,
    metrics: &'a crate::json_parser::health::StreamingQualityMetrics,
    text_flush_non_tty: &'a str,
    terminal_mode: TerminalMode,
    prefix: &'a str,
    colors: crate::logger::Colors,
}

impl OpenCodeParser {
    fn derive_step_id(&self, event: &OpenCodeEvent, session: &str) -> String {
        let step_id = event.part.as_ref().and_then(|part| {
            part.message_id.clone().or_else(|| {
                part.id
                    .as_ref()
                    .map(|id| format!("{session}:{id}"))
                    .or_else(|| {
                        part.snapshot
                            .as_ref()
                            .map(|snapshot| format!("{session}:{snapshot}"))
                    })
            })
        });

        step_id.unwrap_or_else(|| self.next_fallback_step_id(session, event.timestamp))
    }

    fn ensure_current_step_id_for_finish(&self, event: &OpenCodeEvent) {
        let has_current = self
            .state
            .streaming_session
            .borrow()
            .get_current_message_id()
            .is_some();
        if has_current {
            return;
        }

        let session = event.session_id.as_deref().unwrap_or("unknown");
        let step_id = self.derive_step_id(event, session);
        self.state.with_session_mut(|session| {
            session.set_current_message_id(Some(step_id));
        });
    }

    fn flush_non_tty_accumulated_text(
        &self,
        terminal_mode: TerminalMode,
        prefix: &str,
        colors: crate::logger::Colors,
    ) -> String {
        match terminal_mode {
            TerminalMode::Full => String::new(),
            TerminalMode::Basic | TerminalMode::None => {
                let lines: Vec<String> = self.state.with_session_mut(|session| {
                    session
                        .accumulated_keys(ContentType::Text)
                        .into_iter()
                        .filter_map(|key| {
                            let accumulated = session
                                .get_accumulated(ContentType::Text, &key)
                                .unwrap_or("");
                            let sanitized = crate::json_parser::delta_display::sanitize_for_display(
                                accumulated,
                            );
                            if sanitized.is_empty() {
                                return None;
                            }

                            Some(match terminal_mode {
                                TerminalMode::Basic => format!(
                                    "{}[{}]{} {}{}{}",
                                    colors.dim(),
                                    prefix,
                                    colors.reset(),
                                    colors.white(),
                                    sanitized,
                                    colors.reset()
                                ),
                                TerminalMode::None => format!("[{prefix}] {sanitized}"),
                                TerminalMode::Full => unreachable!(),
                            })
                        })
                        .collect()
                });
                lines.join("\n")
            }
        }
    }

    fn format_tokens_summary(tokens: &OpenCodeTokens) -> String {
        let input = tokens.input.unwrap_or(0);
        let output = tokens.output.unwrap_or(0);
        let reasoning = tokens.reasoning.unwrap_or(0);
        let cache_read = tokens.cache.as_ref().and_then(|c| c.read).unwrap_or(0);
        format_token_counts(input, output, reasoning, cache_read)
    }

    fn format_step_finish_payload(
        &self,
        part: &OpenCodePart,
        context: &StepFinishRenderContext<'_>,
    ) -> String {
        let reason = part.reason.as_deref().unwrap_or("unknown");
        let cost = part.cost.unwrap_or(0.0);
        let tokens_str = part
            .tokens
            .as_ref()
            .map_or_else(String::new, Self::format_tokens_summary);
        let newline_prefix = self.compute_step_finish_newline_prefix(context);
        let (icon, color) = step_finish_icon_and_color(reason, context.colors);
        let cost_suffix = format_cost_suffix(cost, &tokens_str);
        let tokens_suffix = format_tokens_suffix(&tokens_str);

        format!(
            "{}{}{}[{}]{} {}{} Step finished{} {}({}{}{}){}",
            context.text_flush_non_tty,
            newline_prefix,
            context.colors.dim(),
            context.prefix,
            context.colors.reset(),
            color,
            icon,
            context.colors.reset(),
            context.colors.dim(),
            reason,
            tokens_suffix,
            cost_suffix,
            context.colors.reset()
        )
    }

    fn compute_step_finish_newline_prefix(&self, context: &StepFinishRenderContext<'_>) -> String {
        if !(context.is_duplicate || context.was_streaming) {
            return String::new();
        }
        let completion = TextDeltaRenderer::render_completion(context.terminal_mode);
        let show_metrics = (self.verbosity.is_debug() || self.show_streaming_metrics)
            && context.metrics.total_deltas > 0;
        append_metrics_if_needed(completion, show_metrics, context)
    }

    /// Format a `step_start` event
    pub(super) fn format_step_start_event(&self, event: &OpenCodeEvent) -> String {
        let colors = self.colors;
        let prefix = &self.display_name;
        let session = event.session_id.as_deref().unwrap_or("unknown");
        let step_id = self.derive_step_id(event, session);

        let current_msg_id: Option<String> = {
            let session = self.state.streaming_session.borrow();
            session.get_current_message_id().map(str::to_string)
        };
        if current_msg_id.is_some_and(|current| current == step_id) {
            return String::new();
        }

        self.state.with_session_mut(|session| {
            session.on_message_start();
            session.set_current_message_id(Some(step_id));
        });
        self.state.with_last_rendered_content_mut(|v| v.clear());

        let snapshot = event
            .part
            .as_ref()
            .and_then(|p| p.snapshot.as_ref())
            .map(|s| format!("({s:.8}...)"))
            .unwrap_or_default();
        format!(
            "{}[{}]{} {}Step started{} {}{}{}\n",
            colors.dim(),
            prefix,
            colors.reset(),
            colors.cyan(),
            colors.reset(),
            colors.dim(),
            snapshot,
            colors.reset()
        )
    }

    /// Format a `step_finish` event
    pub(super) fn format_step_finish_event(&self, event: &OpenCodeEvent) -> String {
        let colors = self.colors;
        let prefix = &self.display_name;

        self.ensure_current_step_id_for_finish(event);

        let (is_duplicate, was_streaming, metrics) = {
            let session = self.state.streaming_session.borrow();
            let is_duplicate = session.get_current_message_id().map_or_else(
                || session.has_any_streamed_content(),
                |message_id| session.is_duplicate_final_message(message_id),
            );
            let was_streaming = session.has_any_streamed_content();
            let metrics = session.get_streaming_quality_metrics();
            (is_duplicate, was_streaming, metrics)
        };

        let _was_in_block = self
            .state
            .with_session_mut(|session| session.on_message_stop());

        let terminal_mode = *self.state.terminal_mode.borrow();
        let text_flush_non_tty = self.flush_non_tty_accumulated_text(terminal_mode, prefix, colors);
        let render_context = StepFinishRenderContext {
            is_duplicate,
            was_streaming,
            metrics: &metrics,
            text_flush_non_tty: &text_flush_non_tty,
            terminal_mode,
            prefix,
            colors,
        };

        event.part.as_ref().map_or_else(String::new, |part| {
            self.format_step_finish_payload(part, &render_context)
        })
    }

    pub(super) fn format_text_event(&self, event: &OpenCodeEvent) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;
        let Some(ref part) = event.part else {
            return String::new();
        };
        let Some(ref text) = part.text else {
            return String::new();
        };
        self.format_text_delta(text, prefix, *c)
    }

    fn format_text_delta(
        &self,
        text: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        let (show_prefix, preview) = self.state.with_session_mut(|session| {
            let show_prefix = session.on_text_delta_key("main", text);
            // Do NOT truncate during streaming: truncation breaks the append-only suffix
            // contract once the preview stops being a prefix of prior output.
            let accumulated = session
                .get_accumulated(ContentType::Text, "main")
                .unwrap_or("")
                .to_string();
            (show_prefix, accumulated)
        });

        let terminal_mode = *self.state.terminal_mode.borrow();
        let key = "text:main";

        if show_prefix {
            return self.render_first_text_delta(&preview, key, prefix, c, terminal_mode);
        }
        self.render_subsequent_text_delta(&preview, key, c, terminal_mode)
    }

    fn render_first_text_delta(
        &self,
        preview: &str,
        key: &str,
        prefix: &str,
        c: crate::logger::Colors,
        terminal_mode: TerminalMode,
    ) -> String {
        let rendered = TextDeltaRenderer::render_first_delta(preview, prefix, c, terminal_mode);
        let sanitized = crate::json_parser::delta_display::sanitize_for_display(preview);
        let new_content = self
            .state
            .last_rendered_content
            .borrow()
            .clone()
            .into_iter()
            .chain([(key.to_string(), sanitized)])
            .collect();
        self.state
            .with_last_rendered_content_mut(|v| *v = new_content);
        rendered
    }

    fn render_subsequent_text_delta(
        &self,
        preview: &str,
        key: &str,
        c: crate::logger::Colors,
        terminal_mode: TerminalMode,
    ) -> String {
        let sanitized = crate::json_parser::delta_display::sanitize_for_display(preview);
        let last_rendered = self
            .state
            .last_rendered_content
            .borrow()
            .get(key)
            .cloned()
            .unwrap_or_default();
        let suffix = crate::json_parser::delta_display::compute_append_only_suffix(
            &last_rendered,
            sanitized.as_str(),
        )
        .to_string();
        debug_log_opencode_discontinuity(&last_rendered, &sanitized, &suffix);
        let new_content = self
            .state
            .last_rendered_content
            .borrow()
            .clone()
            .into_iter()
            .chain([(key.to_string(), sanitized)])
            .collect();
        self.state
            .with_last_rendered_content_mut(|v| *v = new_content);
        match terminal_mode {
            TerminalMode::Full => format!("{}{}{}", c.white(), suffix, c.reset()),
            TerminalMode::Basic | TerminalMode::None => String::new(),
        }
    }

    /// Format an `error` event
    ///
    /// From `OpenCode` source (`run.ts` lines 192-202), error events are emitted for session errors:
    /// ```typescript
    /// if (event.type === "session.error") {
    ///   let err = String(props.error.name)
    ///   if ("data" in props.error && props.error.data && "message" in props.error.data) {
    ///     err = String(props.error.data.message)
    ///   }
    ///   outputJsonEvent("error", { error: props.error })
    /// }
    /// ```
    pub(super) fn format_error_event(&self, event: &OpenCodeEvent, raw_line: &str) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;
        let error_msg = extract_opencode_error_message(event, raw_line);
        let limit = self.verbosity.truncate_limit("text");
        let preview = truncate_text(&error_msg, limit);
        format!(
            "{}[{}]{} {}{} Error:{} {}{}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.red(),
            CROSS,
            c.reset(),
            c.red(),
            preview,
            c.reset()
        )
    }

    fn format_tool_event_header(
        tool_name: &str,
        status: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        // Status-specific icon and color based on ToolState variants from message-v2.ts
        // Statuses: "pending", "running", "completed", "error"
        let (icon, color) = match status {
            "completed" => (CHECK, c.green()),
            "error" => (CROSS, c.red()),
            "running" => ('►', c.cyan()),
            _ => ('…', c.yellow()), // "pending" or unknown
        };

        format!(
            "{}[{}]{} {}Tool{}: {}{}{} {}{}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.magenta(),
            c.reset(),
            c.bold(),
            tool_name,
            c.reset(),
            color,
            icon,
            c.reset()
        )
    }

    fn format_tool_title_to_string(
        &self,
        title: Option<&str>,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        if let Some(t) = title {
            let limit = self.verbosity.truncate_limit("text");
            let preview = truncate_text(t, limit);
            format!(
                "{}[{}]{} {}  └─ {}{}\n",
                c.dim(),
                prefix,
                c.reset(),
                c.dim(),
                preview,
                c.reset()
            )
        } else {
            String::new()
        }
    }

    fn format_tool_input_to_string(
        &self,
        part: &OpenCodePart,
        tool_name: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        if !self.verbosity.show_tool_input() {
            return String::new();
        }
        let input_str = part
            .state
            .as_ref()
            .and_then(|s| s.input.as_ref())
            .map(|v| Self::format_tool_specific_input(tool_name, v));
        let Some(input_str) = input_str else {
            return String::new();
        };
        let preview = truncate_text(&input_str, self.verbosity.truncate_limit("tool_input"));
        if preview.is_empty() {
            return String::new();
        }
        format_dim_continuation_line(&preview, prefix, c)
    }

    fn format_tool_error_to_string(
        &self,
        part: &OpenCodePart,
        status: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        if status != "error" {
            return String::new();
        }
        let error_msg = part
            .state
            .as_ref()
            .and_then(|s| s.error.as_deref());
        let Some(error_msg) = error_msg else {
            return String::new();
        };
        let preview = truncate_text(error_msg, self.verbosity.truncate_limit("tool_result"));
        format!(
            "{}[{}]{} {}  └─ {}Error:{} {}{}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.red(),
            c.bold(),
            c.reset(),
            c.red(),
            preview,
            c.reset()
        )
    }

    fn format_tool_output_to_string(
        &self,
        part: &OpenCodePart,
        status: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        if !self.verbosity.show_tool_input() || status != "completed" {
            return String::new();
        }
        let output_str = extract_tool_output_str(part).filter(|s| !s.is_empty());
        output_str.map_or(String::new(), |s| {
            let limit = self.verbosity.truncate_limit("tool_result");
            format_tool_output_lines(&s, limit, prefix, c)
        })
    }

    /// Format a `tool_use` event
    ///
    /// Based on `OpenCode` source (`run.ts` lines 163-174, `message-v2.ts` lines 221-287):
    /// - Shows tool name with status-specific icon and color
    /// - Status handling: pending (…), running (►), completed (✓), error (✗)
    /// - Title/description when available (from `state.title`)
    /// - Tool-specific input formatting based on tool type
    /// - Tool output/results shown at Normal+ verbosity
    /// - Error messages shown in red when status is "error"
    pub(super) fn format_tool_use_event(&self, event: &OpenCodeEvent) -> String {
        let c = self.colors;
        let prefix = &self.display_name;

        event.part.as_ref().map_or_else(String::new, |part| {
            let tool_name = part.tool.as_deref().unwrap_or("unknown");
            let status = part
                .state
                .as_ref()
                .and_then(|s| s.status.as_deref())
                .unwrap_or("pending");
            let title = part.state.as_ref().and_then(|s| s.title.as_deref());

            format!(
                "{}{}{}{}{}",
                Self::format_tool_event_header(tool_name, status, prefix, c),
                self.format_tool_title_to_string(title, prefix, c),
                self.format_tool_input_to_string(part, tool_name, prefix, c),
                self.format_tool_error_to_string(part, status, prefix, c),
                self.format_tool_output_to_string(part, status, prefix, c),
            )
        })
    }

    /// Format tool input based on tool type
    ///
    /// From `OpenCode` source, each tool has specific input fields:
    /// - `read`: `filePath`, `offset?`, `limit?`
    /// - `bash`: `command`, `timeout?`
    /// - `write`: `filePath`, `content`
    /// - `edit`: `filePath`, ...
    /// - `glob`: `pattern`, `path?`
    /// - `grep`: `pattern`, `path?`, `include?`
    /// - `fetch`: `url`, `format?`, `timeout?`
    pub(super) fn format_tool_specific_input(tool_name: &str, input: &serde_json::Value) -> String {
        let Some(obj) = input.as_object() else {
            return format_tool_input(input);
        };
        format_known_tool_input(tool_name, obj)
            .unwrap_or_else(|| format_tool_input(input))
    }
}

fn format_token_counts(input: u64, output: u64, reasoning: u64, cache_read: u64) -> String {
    if reasoning > 0 {
        format!("in:{input} out:{output} reason:{reasoning} cache:{cache_read}")
    } else if cache_read > 0 {
        format!("in:{input} out:{output} cache:{cache_read}")
    } else {
        format!("in:{input} out:{output}")
    }
}

fn step_finish_icon_and_color(reason: &str, colors: crate::logger::Colors) -> (char, &'static str) {
    let is_success = reason == "tool-calls" || reason == "end_turn";
    if is_success {
        (CHECK, colors.green())
    } else {
        (CROSS, colors.yellow())
    }
}

fn format_cost_suffix(cost: f64, tokens_str: &str) -> String {
    if cost > 0.0 && !tokens_str.is_empty() {
        format!(", ${cost:.4}")
    } else if cost > 0.0 {
        format!("${cost:.4}")
    } else {
        String::new()
    }
}

fn format_tokens_suffix(tokens_str: &str) -> String {
    if tokens_str.is_empty() {
        String::new()
    } else {
        format!(", {tokens_str}")
    }
}

#[cfg(debug_assertions)]
fn debug_log_opencode_discontinuity(last_rendered: &str, sanitized: &str, suffix: &str) {
    if suffix.is_empty() && !last_rendered.is_empty() && !sanitized.is_empty() {
        let _ = writeln!(
            std::io::stderr(),
            "Warning: Delta discontinuity detected in OpenCode text. \
             Provider sent non-monotonic content. \
             Last: {:?} (len={}), Current: {:?} (len={})",
            &last_rendered[..last_rendered.len().min(40)],
            last_rendered.len(),
            &sanitized[..sanitized.len().min(40)],
            sanitized.len()
        );
    }
}

#[cfg(not(debug_assertions))]
#[inline]
fn debug_log_opencode_discontinuity(_last_rendered: &str, _sanitized: &str, _suffix: &str) {}

fn extract_opencode_error_message(event: &OpenCodeEvent, raw_line: &str) -> String {
    event.error.as_ref().map_or_else(
        || extract_error_from_raw_json(raw_line),
        extract_error_from_event_error,
    )
}

fn extract_error_from_raw_json(raw_line: &str) -> String {
    serde_json::from_str::<serde_json::Value>(raw_line).map_or_else(
        |_| "Unknown error".to_string(),
        |json| {
            json.get("error")
                .and_then(|e| {
                    e.get("data")
                        .and_then(|d| d.get("message"))
                        .and_then(|m| m.as_str())
                        .map(String::from)
                        .or_else(|| {
                            e.get("message").and_then(|m| m.as_str()).map(String::from)
                        })
                        .or_else(|| e.get("name").and_then(|n| n.as_str()).map(String::from))
                })
                .unwrap_or_else(|| "Unknown error".to_string())
        },
    )
}

fn extract_error_from_event_error(err: &OpenCodeError) -> String {
    err.data
        .as_ref()
        .and_then(|d| d.get("message"))
        .and_then(|m| m.as_str())
        .map(String::from)
        .or_else(|| err.message.clone())
        .or_else(|| err.name.clone())
        .unwrap_or_else(|| "Unknown error".to_string())
}

fn format_dim_continuation_line(preview: &str, prefix: &str, c: crate::logger::Colors) -> String {
    format!(
        "{}[{}]{} {}  └─ {}{}\n",
        c.dim(),
        prefix,
        c.reset(),
        c.dim(),
        preview,
        c.reset()
    )
}

fn format_tool_output_lines(
    output: &str,
    limit: usize,
    prefix: &str,
    c: crate::logger::Colors,
) -> String {
    let lines: Vec<&str> = output.lines().collect();
    if lines.len() <= 1 {
        return format_single_line_output(output, limit, prefix, c);
    }
    format_multiline_output(&lines, limit, prefix, c)
}

fn format_single_line_output(
    output: &str,
    limit: usize,
    prefix: &str,
    c: crate::logger::Colors,
) -> String {
    let preview = crate::common::truncate_text(output, limit);
    if preview.is_empty() {
        return String::new();
    }
    format!(
        "{}[{}]{} {}  └─ Output:{} {}\n",
        c.dim(),
        prefix,
        c.reset(),
        c.cyan(),
        c.reset(),
        preview
    )
}

fn format_multiline_output(
    lines: &[&str],
    limit: usize,
    prefix: &str,
    c: crate::logger::Colors,
) -> String {
    use crate::config::truncation::MAX_OUTPUT_LINES;

    let indent = format!("{}[{}]{}     ", c.dim(), prefix, c.reset());
    let remaining = lines.len();
    let prefix_sums: Vec<usize> = (0..lines.len())
        .map(|i| lines[..i].iter().map(|l| l.len() + 1).sum())
        .collect();

    [
        format!(
            "{}[{}]{} {}  └─ Output:{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.cyan(),
            c.reset()
        ),
        lines
            .iter()
            .enumerate()
            .map(|(idx, line)| {
                format_output_line(
                    idx,
                    line,
                    &indent,
                    &OutputLineCtx { prefix_sums: &prefix_sums, limit, remaining, max_lines: MAX_OUTPUT_LINES },
                    c,
                )
            })
            .collect::<Vec<_>>()
            .join(""),
    ]
    .join("")
}

struct OutputLineCtx<'a> {
    prefix_sums: &'a [usize],
    limit: usize,
    remaining: usize,
    max_lines: usize,
}

fn format_output_line(
    idx: usize,
    line: &str,
    indent: &str,
    ctx: &OutputLineCtx<'_>,
    c: crate::logger::Colors,
) -> String {
    if idx >= ctx.max_lines || ctx.prefix_sums[idx] + line.len() > ctx.limit {
        let more = ctx.remaining - idx;
        format!("{}{}...({} more lines)\n", indent, c.dim(), more)
    } else {
        format!("{}{}{}{}\n", indent, c.dim(), line, c.reset())
    }
}

fn format_known_tool_input(
    tool_name: &str,
    obj: &serde_json::Map<String, serde_json::Value>,
) -> Option<String> {
    match tool_name {
        "read" | "view" => Some(format_read_tool_input(obj)),
        "bash" => Some(obj.get("command").and_then(|v| v.as_str()).unwrap_or("").to_string()),
        "write" => Some(format_write_tool_input(obj)),
        "edit" => Some(obj.get("filePath").and_then(|v| v.as_str()).unwrap_or("").to_string()),
        "glob" => Some(format_glob_tool_input(obj)),
        "grep" => Some(format_grep_tool_input(obj)),
        "fetch" | "webfetch" => Some(format_fetch_tool_input(obj)),
        "todowrite" | "todoread" => {
            obj.get("todos").and_then(|v| v.as_array()).map(|t| format!("{} items", t.len()))
        }
        _ => None,
    }
}

fn format_read_tool_input(obj: &serde_json::Map<String, serde_json::Value>) -> String {
    let file_path = obj.get("filePath").and_then(|v| v.as_str()).unwrap_or("");
    let offset_part = obj
        .get("offset")
        .and_then(serde_json::Value::as_u64)
        .map_or(String::new(), |o| format!(" (offset: {o})"));
    let limit_part = obj
        .get("limit")
        .and_then(serde_json::Value::as_u64)
        .map_or(String::new(), |l| format!(" (limit: {l})"));
    format!("{file_path}{offset_part}{limit_part}")
}

fn format_write_tool_input(obj: &serde_json::Map<String, serde_json::Value>) -> String {
    let file_path = obj.get("filePath").and_then(|v| v.as_str()).unwrap_or("");
    let content_len = obj.get("content").and_then(|v| v.as_str()).map_or(0, str::len);
    if content_len > 0 {
        format!("{file_path} ({content_len} bytes)")
    } else {
        file_path.to_string()
    }
}

fn format_glob_tool_input(obj: &serde_json::Map<String, serde_json::Value>) -> String {
    let pattern = obj.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
    let path = obj.get("path").and_then(|v| v.as_str());
    path.map_or_else(|| pattern.to_string(), |p| format!("{pattern} in {p}"))
}

fn format_grep_tool_input(obj: &serde_json::Map<String, serde_json::Value>) -> String {
    let pattern = obj.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
    let path_part = obj
        .get("path")
        .and_then(|v| v.as_str())
        .map_or(String::new(), |p| format!(" in {p}"));
    let include_part = obj
        .get("include")
        .and_then(|v| v.as_str())
        .map_or(String::new(), |i| format!(" ({i})"));
    format!("/{pattern}/{path_part}{include_part}")
}

fn format_fetch_tool_input(obj: &serde_json::Map<String, serde_json::Value>) -> String {
    let url = obj.get("url").and_then(|v| v.as_str()).unwrap_or("");
    let format = obj.get("format").and_then(|v| v.as_str());
    format.map_or_else(|| url.to_string(), |f| format!("{url} ({f})"))
}

fn append_metrics_if_needed(
    completion: String,
    show_metrics: bool,
    context: &StepFinishRenderContext<'_>,
) -> String {
    if show_metrics {
        format!("{}\n{}", completion, context.metrics.format(context.colors))
    } else {
        completion
    }
}

fn extract_tool_output_str(part: &OpenCodePart) -> Option<String> {
    part.state
        .as_ref()
        .and_then(|s| s.output.as_ref())
        .map(|v| match v {
            serde_json::Value::String(s) => s.clone(),
            other => other.to_string(),
        })
}
