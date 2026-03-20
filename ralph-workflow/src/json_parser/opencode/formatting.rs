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
        let cache_read = tokens
            .cache
            .as_ref()
            .and_then(|cache| cache.read)
            .unwrap_or(0);

        if reasoning > 0 {
            format!("in:{input} out:{output} reason:{reasoning} cache:{cache_read}")
        } else if cache_read > 0 {
            format!("in:{input} out:{output} cache:{cache_read}")
        } else {
            format!("in:{input} out:{output}")
        }
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

        let is_success = reason == "tool-calls" || reason == "end_turn";
        let icon = if is_success { CHECK } else { CROSS };
        let color = if is_success {
            context.colors.green()
        } else {
            context.colors.yellow()
        };

        let newline_prefix = if context.is_duplicate || context.was_streaming {
            let completion = TextDeltaRenderer::render_completion(context.terminal_mode);
            let show_metrics = (self.verbosity.is_debug() || self.show_streaming_metrics)
                && context.metrics.total_deltas > 0;
            if show_metrics {
                format!("{}\n{}", completion, context.metrics.format(context.colors))
            } else {
                completion
            }
        } else {
            String::new()
        };

        let cost_suffix = if cost > 0.0 && !tokens_str.is_empty() {
            format!(", ${cost:.4}")
        } else if cost > 0.0 {
            format!("${cost:.4}")
        } else {
            String::new()
        };
        let tokens_suffix = if tokens_str.is_empty() {
            String::new()
        } else {
            format!(", {tokens_str}")
        };

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

        if let Some(ref part) = event.part {
            if let Some(ref text) = part.text {
                // Accumulate streaming text using StreamingSession
                let (show_prefix, accumulated_text) = self.state.with_session_mut(|session| {
                    let show_prefix = session.on_text_delta_key("main", text);
                    // Get accumulated text for streaming display
                    let accumulated_text = session
                        .get_accumulated(ContentType::Text, "main")
                        .unwrap_or("")
                        .to_string();
                    (show_prefix, accumulated_text)
                });

                // Do NOT truncate during streaming: truncation breaks the append-only suffix
                // contract once the preview stops being a prefix of prior output.
                let preview = accumulated_text;

                let terminal_mode = *self.state.terminal_mode.borrow();

                // Append-only streaming: emit prefix once, then only the new suffix.
                let key = "text:main";

                if show_prefix {
                    let rendered =
                        TextDeltaRenderer::render_first_delta(&preview, prefix, *c, terminal_mode);
                    let new_content = self
                        .state
                        .last_rendered_content
                        .borrow()
                        .clone()
                        .into_iter()
                        .chain([(
                            key.to_string(),
                            crate::json_parser::delta_display::sanitize_for_display(&preview),
                        )])
                        .collect();
                    self.state
                        .with_last_rendered_content_mut(|v| *v = new_content);
                    return rendered;
                }

                let sanitized = crate::json_parser::delta_display::sanitize_for_display(&preview);
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
                );

                // Detect discontinuities in OpenCode text deltas
                if suffix.is_empty() && !last_rendered.is_empty() && !sanitized.is_empty() {
                    #[cfg(debug_assertions)]
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

                let new_content = self
                    .state
                    .last_rendered_content
                    .borrow()
                    .clone()
                    .into_iter()
                    .chain([(key.to_string(), sanitized.clone())])
                    .collect();
                self.state
                    .with_last_rendered_content_mut(|v| *v = new_content);

                return match terminal_mode {
                    TerminalMode::Full => format!("{}{}{}", c.white(), suffix, c.reset()),
                    TerminalMode::Basic | TerminalMode::None => String::new(),
                };
            }
        }
        String::new()
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

        // Try to extract error message from the event
        let error_msg = event.error.as_ref().map_or_else(
            || {
                // Fallback: try to extract from raw JSON
                serde_json::from_str::<serde_json::Value>(raw_line).map_or_else(
                    |_| "Unknown error".to_string(),
                    |json| {
                        json.get("error")
                            .and_then(|e| {
                                // Try data.message first (as in run.ts)
                                e.get("data")
                                    .and_then(|d| d.get("message"))
                                    .and_then(|m| m.as_str())
                                    .map(String::from)
                                    // Then try direct message
                                    .or_else(|| {
                                        e.get("message").and_then(|m| m.as_str()).map(String::from)
                                    })
                                    // Then try name
                                    .or_else(|| {
                                        e.get("name").and_then(|n| n.as_str()).map(String::from)
                                    })
                            })
                            .unwrap_or_else(|| "Unknown error".to_string())
                    },
                )
            },
            |err| {
                // Try data.message first (as in run.ts)
                err.data
                    .as_ref()
                    .and_then(|d| d.get("message"))
                    .and_then(|m| m.as_str())
                    .map(String::from)
                    // Then try direct message
                    .or_else(|| err.message.clone())
                    // Then try name
                    .or_else(|| err.name.clone())
                    .unwrap_or_else(|| "Unknown error".to_string())
            },
        );

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

        if let Some(ref state) = part.state {
            if let Some(ref input_val) = state.input {
                let input_str = Self::format_tool_specific_input(tool_name, input_val);
                let limit = self.verbosity.truncate_limit("tool_input");
                let preview = truncate_text(&input_str, limit);
                if !preview.is_empty() {
                    return format!(
                        "{}[{}]{} {}  └─ {}{}\n",
                        c.dim(),
                        prefix,
                        c.reset(),
                        c.dim(),
                        preview,
                        c.reset()
                    );
                }
            }
        }
        String::new()
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

        if let Some(ref state) = part.state {
            if let Some(error_msg) = state.error.as_deref() {
                let limit = self.verbosity.truncate_limit("tool_result");
                let preview = truncate_text(error_msg, limit);
                return format!(
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
                );
            }
        }
        String::new()
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

        if let Some(ref state) = part.state {
            if let Some(ref output_val) = state.output {
                let output_str = match output_val {
                    serde_json::Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                if !output_str.is_empty() {
                    let limit = self.verbosity.truncate_limit("tool_result");
                    return Self::format_tool_output_to_string_static(
                        &output_str,
                        limit,
                        prefix,
                        c,
                    );
                }
            }
        }
        String::new()
    }

    fn format_tool_output_to_string_static(
        output: &str,
        limit: usize,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        use crate::config::truncation::MAX_OUTPUT_LINES;

        let lines: Vec<&str> = output.lines().collect();
        let is_multiline = lines.len() > 1;

        if !is_multiline {
            let preview = truncate_text(output, limit);
            if preview.is_empty() {
                return String::new();
            }
            return format!(
                "{}[{}]{} {}  └─ Output:{} {}\n",
                c.dim(),
                prefix,
                c.reset(),
                c.cyan(),
                c.reset(),
                preview
            );
        }

        let indent = format!("{}[{}]{}     ", c.dim(), prefix, c.reset());
        let remaining = lines.len();

        let prefix_sums: Vec<usize> = (0..lines.len())
            .map(|i| lines[..i].iter().map(|l| l.len() + 1).sum())
            .collect();

        let result = [
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
                    if idx >= MAX_OUTPUT_LINES {
                        let more = remaining - idx;
                        format!("{}{}...({} more lines)\n", indent, c.dim(), more)
                    } else {
                        let chars_used_before = prefix_sums[idx];
                        if chars_used_before + line.len() > limit {
                            let more = remaining - idx;
                            format!("{}{}...({} more lines)\n", indent, c.dim(), more)
                        } else {
                            format!("{}{}{}{}\n", indent, c.dim(), line, c.reset())
                        }
                    }
                })
                .collect::<Vec<_>>()
                .join(""),
        ]
        .join("");

        result
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

        match tool_name {
            "read" | "view" => {
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
            "bash" => {
                // Primary: command
                obj.get("command")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string()
            }
            "write" => {
                // Primary: filePath (don't show content in summary)
                let file_path = obj.get("filePath").and_then(|v| v.as_str()).unwrap_or("");
                let content_len = obj
                    .get("content")
                    .and_then(|v| v.as_str())
                    .map_or(0, str::len);
                if content_len > 0 {
                    format!("{file_path} ({content_len} bytes)")
                } else {
                    file_path.to_string()
                }
            }
            "edit" => {
                // Primary: filePath
                obj.get("filePath")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string()
            }
            "glob" => {
                // Primary: pattern, optional: path
                let pattern = obj.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
                let path = obj.get("path").and_then(|v| v.as_str());
                path.map_or_else(|| pattern.to_string(), |p| format!("{pattern} in {p}"))
            }
            "grep" => {
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
            "fetch" | "webfetch" => {
                // Primary: url, optional: format
                let url = obj.get("url").and_then(|v| v.as_str()).unwrap_or("");
                let format = obj.get("format").and_then(|v| v.as_str());
                format.map_or_else(|| url.to_string(), |f| format!("{url} ({f})"))
            }
            "todowrite" | "todoread" => {
                // Show count of todos if available
                obj.get("todos").and_then(|v| v.as_array()).map_or_else(
                    || format_tool_input(input),
                    |todos| format!("{} items", todos.len()),
                )
            }
            _ => {
                // Fallback to generic formatting
                format_tool_input(input)
            }
        }
    }
}
